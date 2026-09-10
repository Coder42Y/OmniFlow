"""官方 agy stream-json 适配；工具日志仅观察，绝不把 tool_info 当第二次执行请求。

真实启动器必须经过隔离/授权审查后显式注入；默认没有真实启动能力。测试使用同一
协议解析器和有界管道启动合成 Python 进程，不运行 agy、不复制 Google 授权。
"""

import json
import math
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .agy_schema import StructuredReply, reply_schema
from .problems import ProblemError
from .process_transport import decode_frame
from .provider_gate import MODELS, EvidenceGate
from .run_manager import ProviderUnavailable, TextDelta, TurnResult

TOOLS = ("submit_image", "submit_video", "submit_local_motion", "read_task", "list_artifacts")


@dataclass(frozen=True)
class StructuredActions:
    actions: list[dict]


@dataclass(frozen=True)
class TurnFailure:
    status: str


@dataclass(frozen=True)
class LaunchPlan:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    settings: dict


def private_directory(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("CLI 私有目录无效")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise ValueError("CLI 私有目录无效")


def launch_plan(root, owner_id, conversation_id, resume_id):
    owner_id, conversation_id = str(UUID(owner_id)), str(UUID(conversation_id))
    if resume_id is not None:
        resume_id = str(UUID(resume_id))
    root = Path(root).absolute()
    private_directory(root)
    owner = root / owner_id
    private_directory(owner)
    directory = owner / conversation_id
    private_directory(directory)
    home = directory / "home"
    work = directory / "workspace"
    private_directory(home)
    private_directory(work)
    argv = (
        "/usr/local/bin/agy",
        "--model",
        MODELS["text"],
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--sandbox",
        "--json-schema",
        json.dumps(reply_schema(), ensure_ascii=True, separators=(",", ":")),
    )
    if resume_id:
        argv += ("--conversation", resume_id)
    settings = {
        "useG1Credits": False,
        "allowNonWorkspaceAccess": False,
        "enableTerminalSandbox": True,
        "toolPermission": "request-review",
        "permissions": {
            "allow": [f"mcp(omniflow/{name})" for name in TOOLS],
            "deny": [
                f"{action}(*)"
                for action in (
                    "read_file",
                    "write_file",
                    "read_url",
                    "execute_url",
                    "command",
                    "unsandboxed",
                )
            ],
            # Deny > Ask > Allow；不放 mcp(*) 以免覆盖精确放行。
            "ask": [],
        },
    }
    # 这是交给可信启动器的计划，不改全局或真实 Google 设置。
    return LaunchPlan(argv, work, {"HOME": str(home), "LANG": "C.UTF-8"}, settings)


class AgyProvider:
    def __init__(self, root, *, gate=None, launcher=None):
        self.root = root
        self.gate = gate or EvidenceGate()
        self.launcher = launcher

    def open(self, *, owner_id, conversation_id, resume_id, model):
        try:
            if model != MODELS["text"] or self.launcher is None:
                raise ValueError
            self.gate.check("text")
            plan = launch_plan(self.root, owner_id, conversation_id, resume_id)
        except ProblemError as exc:
            raise ProviderUnavailable(exc.code) from None
        except Exception:
            raise ProviderUnavailable from None  # 尚未创建进程。
        transport = self.launcher(plan)  # 抛异常可能已留下进程，不能伪装安全 open 失败。
        try:
            return AgySession(transport, plan, resume_id, self.gate)
        except BaseException:
            transport.close()
            raise


class AgySession:
    def __init__(self, transport, plan, resume_id, gate):
        self.transport, self.gate = transport, gate
        self.pending = deque()
        self.active = False
        self.failed = False
        self.text = ""
        self.cumulative = {}
        self.completed_turns = None
        greeting = transport.read(2)
        if not greeting or greeting.get("event") != "init":
            raise ValueError("缺少官方 init")
        self.session_id = str(UUID(greeting["conversation_id"]))
        init = greeting["init"]
        if (
            (resume_id and resume_id != self.session_id)
            or init.get("model") != MODELS["text"]
            or init.get("permission_mode") != "request-review"
            or init.get("cwd") != str(plan.cwd)
            or init.get("json_schema") != reply_schema()
        ):
            raise ValueError("CLI 上下文或配置不符")

    def check_ready(self):
        try:
            self.gate.check("text")
        except ProblemError as exc:
            raise ProviderUnavailable(exc.code) from None

    def send(self, history, permission):
        if self.active or self.pending or self.failed or not history:
            raise ValueError("轮次尚未确认完成")
        self.gate.check("text")  # 常驻后权益仍可能到期，逐轮重查。
        latest = history[-1]
        if latest["role"] != "user" or permission not in ("requested_only", "discuss_only"):
            raise ValueError("消息边界无效")
        # 常驻/明确恢复已持有旧历史，只发送当前消息，不重复注入整个聊天。
        # CLI 仅支持 text block；版本标识是受控工具引用，不伪装 image block 或本地路径。
        from .tool_gateway import ToolGateway

        content = json.dumps(
            {
                "tool_contract": {
                    "tools": ToolGateway.schemas(),
                    "instructions": (
                        "只输出 schema 中的 text 与 actions。actions 是提交给服务端校验的请求，"
                        "不是已执行结果；不要声称作品已完成。参数遵循 inputSchema，"
                        "不填写 owner、conversation、run、路径或凭据。缺少参数先澄清。"
                        "discuss_only 不生成。首帧确认由服务端绑定，不能自行确认。"
                    ),
                },
                "user_message": latest["content"],
                "generation_permission": permission,
                "attachment_version_ids": latest.get("attachment_version_ids", []),
                "selected_version_id": latest.get("selected_version_id"),
            },
            ensure_ascii=False,
        )
        self.active = True  # 写管道失败仍可能已被接收，不重试。
        self.text = ""
        self.transport.write({"event": "user", "message": {"content": content}})

    def receive(self, timeout):
        if self.pending:
            return self.pending.popleft()
        if not self.active:
            raise ValueError("没有活动轮次")
        frame = self.transport.read(timeout)
        if frame is None:
            return None
        kind = frame.get("event")
        if kind not in ("step_update", "result"):
            raise ValueError("未知官方事件")
        payload = frame[kind]
        if payload.get("conversation_id") != self.session_id:
            raise ValueError("CLI 会话不匹配")
        if kind == "step_update":
            if payload.get("state") not in ("ACTIVE", "DONE"):
                raise ValueError("未知步骤状态")
            if payload.get("step_type") == "agent_response":
                delta = payload.get("text_delta", "")
                if not isinstance(delta, str):
                    raise ValueError
                delta.encode("utf-8")
                self.text += delta
                if len(self.text) > 128000:
                    raise ValueError("单轮文字过长")
                # --json-schema 的增量可能是完整动作 JSON 的片段。仅有界缓存，
                # 不做截取 JSON/正则脱敏；直到终态完整校验才交付专用 text 字段。
                return None
            # thought、tool_info.output、usage、主机路径等从不进入公开正文。
            return None
        self.active = False
        status = payload.get("status")
        if status != "SUCCESS":
            if status not in ("ERROR", "CANCELED", "INTERRUPTED", "INVALID", "WAITING", "RUNNING"):
                raise ValueError("未知终态")
            self.failed = True
            return TurnFailure(status)
        turns = payload.get("num_turns")
        if (
            type(turns) is not int
            or turns < 1
            or (self.completed_turns is not None and turns != self.completed_turns + 1)
        ):
            raise ValueError("重复或跳过了轮次 result")
        self.completed_turns = turns
        response = payload.get("response")
        if not isinstance(response, str):
            raise ValueError
        response.encode("utf-8")
        if len(response) > 128000 or not response.startswith(self.text):
            raise ValueError("终态正文与增量不符")
        structured = payload.get("structured_output")
        if payload.get("json_schema") != reply_schema():
            raise ValueError("终态缺少指定 schema")
        # response 只校验与官方解析对象相符，绝不从自由文本提取/执行动作。
        if decode_frame(response.encode("utf-8")) != structured:
            raise ValueError("终态结构化对象与序列化文本不符")
        reply = StructuredReply.model_validate(structured)
        # 只保留最新累计快照；不将 step usage 与 result 或跨轮累计相加。
        stats = {k: payload[k] for k in ("num_turns", "duration_seconds") if k in payload}
        usage = payload.get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError
        stats.update(
            {
                k: usage[k]
                for k in (
                    "input_tokens",
                    "output_tokens",
                    "thinking_tokens",
                    "cache_read_tokens",
                    "total_tokens",
                )
                if k in usage
            }
        )
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in stats.values()
        ):
            raise ValueError
        self.cumulative = stats
        # 全部终态字段通过后才发布，坏统计/坏动作结构也不能留下待交付正文。
        if reply.text:
            self.pending.append(TextDelta(reply.text))
        if reply.actions:
            self.pending.append(
                StructuredActions([action.model_dump() for action in reply.actions])
            )
        self.pending.append(TurnResult())
        return self.pending.popleft()

    def stop(self):
        if not self.active:
            return self.transport.close() is True
        self.transport.interrupt()
        deadline = time.monotonic() + 1
        confirmed = False
        try:
            while time.monotonic() < deadline:
                event = self.receive(min(0.1, deadline - time.monotonic()))
                if isinstance(event, TurnFailure):
                    confirmed = event.status in ("CANCELED", "INTERRUPTED")
                    break
                if isinstance(event, TurnResult):
                    break  # 完成与停止竞争，不能谎报已取消。
        except Exception:
            pass
        return self.transport.close() is True and confirmed

    def close(self):
        return self.transport.close()
