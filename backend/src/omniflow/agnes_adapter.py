"""Agnes Flash 官方协议映射；同步图片结果先存线索，视频仅续查 video_id。

默认无 HTTP/密钥/免费证明，真实调用仍拒绝。图片接口文档顶层 image 与示例冲突，
采用官方“重要说明”及图生图示例的 extra_body.image，不静默尝试另一请求/收费模型。
"""

import json
import re
from urllib.parse import urlencode
from uuid import UUID

from .artifacts import TaskMediaService
from .auth_security import fail
from .media_provider import Accepted, Download, Observation, SubmissionRejected
from .process_transport import decode_frame
from .provider_gate import MODELS, EvidenceGate
from .provider_limits import ProviderLimits
from .safe_http import HTTPRateLimited, validate_url
from .task_models import ImageTaskCreate, VideoTaskCreate

API_HOSTS = ("api.agnes-ai.cn",)


class AgnesProvider:
    reference_editing_enabled = False

    def __init__(
        self,
        database,
        *,
        http=None,
        authorization=None,
        gate=None,
        download_hosts=(),
        reference_editing_enabled=False,
    ):
        self.database = database
        self.http, self.authorization = http, authorization
        self.gate = gate or EvidenceGate()
        self.limits = ProviderLimits(database)
        # 只能由可信宿主核对真实存储目的地后注入，空集合默认拒绝下载。
        self.download_hosts = tuple(download_hosts)
        self.reference_editing_enabled = reference_editing_enabled is True

    def check(self, kind):
        if kind not in ("image", "ai_video"):
            raise ValueError("不支持此媒体路径")
        self.limits.check(self.gate.check(kind))
        if self.http is None or self.authorization is None:
            raise ValueError("真实传输尚未接入")

    def request(self, method, path, body=None):
        if self.http is None or self.authorization is None:
            raise ValueError("真实传输尚未接入")
        token = self.authorization()
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{8,512}", token):
            raise ValueError("提供方授权不可用")
        try:
            status, headers, raw = self.http.request(
                method,
                "https://api.agnes-ai.cn" + path,
                hosts=API_HOSTS,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                },
                body=json.dumps(body, ensure_ascii=True).encode() if body is not None else None,
                max_bytes=1048576,
                timeout=120 if method == "POST" else 30,
            )
            if status == 429:  # 兼容可信注入传输直接返回状态的边界。
                raise HTTPRateLimited(
                    next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
                )
        except HTTPRateLimited as exc:
            self.limits.record(exc.retry_after)
            # POST 仍为提交未知；不凭限流响应声称绝对未受理或自动重发。
            fail(503, "PROVIDER_LIMIT_REACHED", "提供方限流，已暂停新增生成")
        # 非明确的供应商 400 不声称未受理；所有网络超时/5xx 等仍提交未知。
        if method == "POST" and status == 400:
            raise SubmissionRejected
        if (
            not 200 <= status < 300
            or headers.get("content-type", "").split(";")[0] != "application/json"
        ):
            raise ValueError("提供方未返回有效成功响应")
        return decode_frame(raw)

    def references(self, tid, inputs):
        service = TaskMediaService(self.database)
        origin = self.database.settings.public_origin
        if not origin.startswith("https://"):
            raise ValueError("参考素材要求 HTTPS")
        with self.database.transaction() as connection:
            return [
                origin + f"/api/v1/media-grants/{identity}/content?token={token}"
                for identity, token in (service.issue(connection, tid, vid) for vid in inputs)
            ]

    def submit(self, task, inputs):
        kind = task["kind"]
        try:
            self.check(kind)  # worker 检查后，临近外部调用再查时效。
            model = ImageTaskCreate if kind == "image" else VideoTaskCreate
            data = model.model_validate(task["requested_parameters"]).model_dump(
                mode="json", exclude_none=True
            )
            if kind == "image":
                if set(inputs) != set(data.get("reference_version_ids", [])):
                    raise ValueError
                if inputs and not self.reference_editing_enabled:
                    raise ValueError
                # 请求顺序保留“第一张/第二张”语义，不采用 SQL 的 UUID 排序。
                urls = self.references(task["id"], data.get("reference_version_ids", []))
                body = {
                    "model": MODELS[kind],
                    "prompt": data["prompt"],
                    "size": data["size_tier"],
                    "ratio": data["aspect_ratio"],
                    "extra_body": {"response_format": "url"},
                }
                if urls:
                    body["extra_body"]["image"] = urls
                path = "/v1/images/generations"
            else:
                if len(inputs) != (1 if data["mode"] == "keyframe" else 0):
                    raise ValueError
                body = {
                    "model": MODELS[kind],
                    "prompt": data["prompt"],
                    "size": "720P",
                    "n": 1,
                    "seconds": str(data["seconds"]),
                    "aspect_ratio": data["aspect_ratio"],
                    "mode": data["mode"],
                }
                if inputs:
                    body["first_frame"] = self.references(task["id"], inputs)[0]
                path = "/v1/videos"
            self.check(kind)
        except Exception:
            raise SubmissionRejected from None  # 只在网络边界之前保证未提交。
        result = self.request("POST", path, body)
        if kind == "image":
            if not isinstance(result.get("data"), list) or len(result["data"]) != 1:
                raise ValueError("结果数量不符")
            url = result["data"][0]["url"]
            validate_url(url, self.download_hosts)
            # 同步生成没有供应商任务 ID；持久线索用于重启后的查询/下载，不靠内存缓存。
            with self.database.transaction() as connection:
                connection.execute("INSERT INTO adapter_receipts VALUES (?,?)", (task["id"], url))
            return Accepted("image:" + task["id"])
        if result.get("model") != MODELS["ai_video"]:
            raise ValueError("提供方实际模型不符")
        identity = result.get("video_id")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", identity):
            raise ValueError("缺少视频查询 ID")
        return Accepted("video:" + identity)

    def poll(self, task_id):
        if task_id.startswith("image:"):
            tid = str(UUID(task_id[6:]))
            with self.database.snapshot() as connection:
                row = connection.execute(
                    "SELECT result_key FROM adapter_receipts WHERE task_id=?", (tid,)
                ).fetchone()
            if row is None:
                raise ValueError("缺少持久结果")
            validate_url(row[0], self.download_hosts)
            return Observation("completed", row[0])
        if not task_id.startswith("video:") or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,256}", task_id[6:]
        ):
            raise ValueError("不支持查询标识")
        identity = task_id[6:]
        result = self.request(
            "GET",
            "/agnesapi?" + urlencode({"video_id": identity, "model_name": MODELS["ai_video"]}),
        )
        if result.get("video_id") != identity or result.get("model") != MODELS["ai_video"]:
            raise ValueError("返回了不同的视频/模型")
        status = result.get("status")
        if status == "completed":
            url = result["metadata"]["url"]
            validate_url(url, self.download_hosts)
            return Observation("completed", url)
        if status in ("queued", "in_progress"):
            return Observation("running")
        if status == "failed":
            return Observation("failed")
        raise ValueError("未知视频状态")

    def download(self, result_key, max_bytes):
        # 下载永不带 Agnes Authorization，不把 API 凭据送至存储域名。
        status, headers, content = self.http.request(
            "GET",
            result_key,
            hosts=self.download_hosts,
            max_bytes=max_bytes,
            timeout=60,
            headers={"Accept-Encoding": "identity"},
        )
        mime = headers.get("content-type", "").split(";")[0]
        if status != 200 or mime not in ("image/png", "image/jpeg", "image/webp", "video/mp4"):
            raise ValueError("下载不是允许的媒体")
        return Download(content, mime)
