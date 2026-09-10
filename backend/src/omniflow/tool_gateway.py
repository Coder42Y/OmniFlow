"""封闭 MCP JSON-RPC 网关；仅可信 stdio 宿主绑定 run，无 HTTP/匿名通用入口。

网站 Cookie、owner、租约及幂等动作标识从不进入工具参数。自然语言仅接受保守的
明确祈使句；不确定时拒绝生成要求澄清。不会以关键词命中或模型的 confirmed 放行。
"""

import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from .agy_adapter import TOOLS
from .artifacts import ArtifactService
from .auth_security import fail
from .conversations import get_message, not_found, require_tool_run
from .problems import ProblemError
from .process_transport import decode_frame
from .task_models import ImageTaskCreate, LocalMotionTaskCreate, VideoTaskCreate
from .tasks import TaskService, ToolAuthority, public_task, task_owned

MODELS = {
    "submit_image": ImageTaskCreate,
    "submit_video": VideoTaskCreate,
    "submit_local_motion": LocalMotionTaskCreate,
}
KINDS = {"submit_image": "image", "submit_video": "ai_video", "submit_local_motion": "local_motion"}


@dataclass(frozen=True)
class RunScope:
    owner_id: str
    conversation_id: str
    run_id: str
    manager_token: str


def intent(content, name, mode=None):
    # 此语法只用于保守放行，不是通用意图模型。前缀必须由真实用户写出。
    patterns = {
        "submit_image": (
            r"(?:请|帮我)?(?:生成|画|制作)(?P<count>一|[1-8])张"
            r"(?:图片|图|视觉稿)[：:,，].+"
        ),
        "submit_video": (
            r"(?:请|帮我)?直接(?:文生视频|生成文字视频)[：:,，].+"
            if mode == "text"
            else r"(?:请|帮我)?(?:用|根据)已确认图片(?:制作|生成)(?:视频)[：:,，].+"
        ),
        "submit_local_motion": r"(?:请|帮我)?(?:使用|选择)本地(?:快速)?运镜[：:,，].+",
    }
    # 引用/疑问/否定/条件句不升级为执行授权。不能支持的表达由助手澄清或 UI 直调。
    if any(
        word in content
        for word in ("不要", "别", "如果", "是否", "能否", "？", "?", "例如", "示例")
    ):
        return False
    match = re.fullmatch(patterns[name], content.strip())
    if match is None:
        return 0
    count = match.groupdict().get("count", "一")
    return 1 if count == "一" else int(count)


class ToolGateway:
    def __init__(self, database, provider=None):
        self.database = database
        self.tasks = TaskService(database, provider)

    def guard(self, connection, scope, *, media=False):
        return require_tool_run(connection, **scope.__dict__, media=media)

    def call(self, scope, call_id, name, arguments):
        if name not in TOOLS or not isinstance(arguments, dict):
            fail(403, "FORBIDDEN", "工具或参数不在许可范围")
        if type(call_id) not in (str, int) or not 1 <= len(str(call_id)) <= 128:
            fail(422, "VALIDATION_ERROR", "工具调用标识无效")
        with self.database.snapshot() as connection:
            run = self.guard(connection, scope, media=name in MODELS)
            message = get_message(connection, run["user_message_id"])
            allowed = ArtifactService(self.database).authorized_versions(
                connection, scope.owner_id, scope.conversation_id, through_seq=message["seq"]
            )
            if name == "list_artifacts":
                if arguments:
                    fail(422, "VALIDATION_ERROR", "列表不接受其他范围")
                # 工具不接收下载链接，更不接收服务器路径/全库内容。
                return {
                    "items": [
                        {
                            k: v[k]
                            for k in (
                                "id",
                                "artifact_id",
                                "version_number",
                                "media_type",
                                "width",
                                "height",
                            )
                        }
                        for v in allowed
                    ]
                }
            if name == "read_task":
                if set(arguments) != {"task_id"}:
                    fail(422, "VALIDATION_ERROR", "查询字段无效")
                value = arguments["task_id"]
                if type(value) is not str or len(value) != 36:
                    fail(422, "VALIDATION_ERROR", "查询编号必须为 UUID 字符串")
                try:
                    tid = str(UUID(value))
                    if tid != value.lower():
                        raise ValueError
                except ValueError:
                    fail(422, "VALIDATION_ERROR", "查询编号无效")
                task = task_owned(connection, tid, scope.owner_id)
                if task["conversation_id"] != scope.conversation_id:
                    not_found()
                if task["run_id"] and task["run_id"] != scope.run_id:
                    other = connection.execute(
                        "SELECT m.seq FROM runs r JOIN messages m ON m.id=r.user_message_id "
                        "WHERE r.id=?",
                        (task["run_id"],),
                    ).fetchone()
                    if other[0] > message["seq"]:
                        not_found()
                return public_task(task)
            budget = intent(message["content"], name, arguments.get("mode"))
            if not budget:
                fail(403, "FORBIDDEN", "原用户意图不足，请澄清后再执行")
            if "conversation_id" in arguments or "kind" in arguments:
                fail(422, "VALIDATION_ERROR", "工具不能指定执行范围")
            if name == "submit_video" and arguments.get("mode") == "keyframe":
                evidence = json.loads(run["input_json"]).get("reference_confirmation_id")
                if not evidence or (
                    "reference_confirmation_id" in arguments
                    and arguments["reference_confirmation_id"] != evidence
                ):
                    fail(409, "REFERENCE_CONFIRMATION_REQUIRED", "本轮须明确选择同一确认图片")
                # 确认来自本轮经网站接口校验的不可变输入，不要求 CLI 猜不可见 ID。
                # 不查最近确认/后排消息，不接受模型改选；任务事务仍复核归属与有效性。
                arguments = {**arguments, "reference_confirmation_id": evidence}
        try:
            data = MODELS[name].model_validate(
                {**arguments, "conversation_id": scope.conversation_id, "kind": KINDS[name]}
            )
        except (ValidationError, ValueError, TypeError):
            fail(422, "VALIDATION_ERROR", "工具参数不符合契约")
        authority = ToolAuthority(
            **scope.__dict__,
            action_id=hashlib.sha256(str(call_id).encode()).hexdigest(),
            direct_text_video=name == "submit_video" and arguments.get("mode") == "text",
            max_tasks=budget,
        )
        return self.tasks.create_tool(data, authority)[0]

    @staticmethod
    def schemas():
        tools = []
        for name in TOOLS:
            if name in MODELS:
                schema = MODELS[name].model_json_schema()
                for field in ("conversation_id", "kind"):
                    schema["properties"].pop(field)
                    schema["required"].remove(field)
                if name == "submit_video":
                    schema["properties"]["reference_confirmation_id"] = {
                        "type": "string",
                        "format": "uuid",
                        "description": (
                            "首帧模式可省略，由服务端绑定本轮用户的确认；"
                            "若提供必须与该确认一致，不能自行确认或改选。文字模式禁止提供。"
                        ),
                    }
            else:
                schema = {"type": "object", "properties": {}, "additionalProperties": False}
                if name == "read_task":
                    schema["properties"] = {"task_id": {"type": "string", "format": "uuid"}}
                    schema["required"] = ["task_id"]
            tools.append(
                {"name": name, "description": "仅本轮授权的创作动作或查询", "inputSchema": schema}
            )
        return tools

    def rpc(self, scope, frame):
        identity = frame.get("id") if isinstance(frame, dict) else None
        try:
            if not isinstance(frame, dict):
                raise ValueError
            if frame.get("jsonrpc") != "2.0" or set(frame) - {"jsonrpc", "id", "method", "params"}:
                raise ValueError
            with self.database.snapshot() as connection:
                self.guard(connection, scope)
            method, params = frame.get("method"), frame.get("params", {})
            if method == "notifications/initialized" and identity is None:
                return None
            if type(identity) not in (int, str) or len(str(identity)) > 128:
                raise ValueError
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "omniflow", "version": "1.0"},
                }
            elif method == "tools/list" and params == {}:
                result = {"tools": self.schemas()}
            elif (
                method == "tools/call"
                and isinstance(params, dict)
                and set(params) == {"name", "arguments"}
            ):
                value = self.call(scope, identity, params["name"], params["arguments"])
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(value, ensure_ascii=True, sort_keys=True),
                        }
                    ],
                    "isError": False,
                }
            else:
                raise ValueError
            return {"jsonrpc": "2.0", "id": identity, "result": result}
        except (ProblemError, ValueError, TypeError, KeyError) as exc:
            code = exc.code if isinstance(exc, ProblemError) else "VALIDATION_ERROR"
            safe_id = (
                identity if type(identity) in (str, int) and len(str(identity)) <= 128 else None
            )
            return {"jsonrpc": "2.0", "id": safe_id, "error": {"code": -32000, "message": code}}

    def serve_stdio(self, scope, reader, writer):
        # reader/writer 必须是可信宿主创建的专用管道；无可由 CLI 指定 owner 的命令入口。
        while True:
            line = reader.readline(65537)
            if not line:
                return
            if len(line) > 65536 or not line.endswith(b"\n"):
                raise ValueError("MCP 帧过大或不完整")
            try:
                frame = decode_frame(line)
                # 不依赖不同 Python 版本的递归上限；正常工具参数远低于此深度。
                pending = [(frame, 0)]
                while pending:
                    value, depth = pending.pop()
                    if depth > 32:
                        raise ValueError("MCP 参数嵌套过深")
                    children = value.values() if isinstance(value, dict) else value
                    if isinstance(value, (dict, list)):
                        pending.extend((child, depth + 1) for child in children)
            except (ValueError, UnicodeError, RecursionError):
                # 已取得有界完整一行，可以安全对齐下一帧；超长/残帧仍拒绝并关闭。
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "VALIDATION_ERROR"},
                }
            else:
                response = self.rpc(scope, frame)
            if response is not None:
                writer.write((json.dumps(response, ensure_ascii=True) + "\n").encode())
                writer.flush()
