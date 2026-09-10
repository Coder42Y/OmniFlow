"""纯 ASGI 安全边界，不缓冲响应体，后续 SSE 可复用。"""

import logging
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings
from .problems import problem_response

logger = logging.getLogger("omniflow.requests")
# 大于契约最大文案（50000 个非 BMP 字符全部转义约 600KB）。
# 字段校验发生在 JSON 解析之后，不能代替解析前的总容量保护。
MAX_JSON_BODY_BYTES = 1024 * 1024
UNSAFE_METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))
CORS_METHODS = ("GET", "HEAD", "POST", "PATCH", "DELETE", "OPTIONS")
CORS_HEADERS = frozenset(
    (
        "content-type",
        "x-csrf-token",
        "idempotency-key",
        "last-event-id",
        "range",
        "accept",
        "accept-language",
        "content-language",
    )
)


class SecurityBoundary:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    def _valid_host(self, headers: Headers) -> bool:
        hosts = headers.getlist("host")
        if len(hosts) != 1:
            return False
        host = hosts[0]
        try:
            parsed = urlsplit("//" + host)
            port = parsed.port
        except ValueError:
            return False
        return bool(
            parsed.hostname in self.settings.allowed_hosts
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and not any(c.isspace() for c in host)
            and "\\" not in host
            and (port is None or port > 0)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        headers = Headers(scope=scope)
        origins = headers.getlist("origin")
        origin = origins[0] if len(origins) == 1 else None
        trusted_origin = origin is not None and origin in self.settings.origins
        started = False

        async def secure_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                response_headers = MutableHeaders(scope=message)
                response_headers["X-Request-ID"] = request_id
                response_headers["Cache-Control"] = "private, no-store"
                response_headers["Referrer-Policy"] = "no-referrer"
                response_headers["X-Content-Type-Options"] = "nosniff"
                response_headers["X-Frame-Options"] = "DENY"
                if self.settings.environment == "production":
                    response_headers["Strict-Transport-Security"] = "max-age=31536000"
                response_headers.add_vary_header("Origin")
                if trusted_origin:
                    response_headers["Access-Control-Allow-Origin"] = origin
                    response_headers["Access-Control-Allow-Credentials"] = "true"
                    response_headers["Access-Control-Expose-Headers"] = (
                        "X-Request-ID, Idempotency-Replayed, Retry-After, Content-Range, "
                        "Content-Disposition"
                    )
            if scope["method"] == "HEAD" and message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        async def reject(status: int, code: str, message: str) -> None:
            await problem_response(
                request_id, status=status, code=code, title=message, detail=message + "。"
            )(scope, receive, secure_send)

        if not self._valid_host(headers):
            await reject(400, "VALIDATION_ERROR", "请求主机不允许")
            return
        if scope["method"] in UNSAFE_METHODS and not trusted_origin:
            await reject(403, "CSRF_INVALID", "请求来源无效")
            return
        if scope["method"] == "OPTIONS" and "access-control-request-method" in headers:
            requested_headers = {
                h.strip().lower()
                for h in headers.get("access-control-request-headers", "").split(",")
                if h.strip()
            }
            if (
                not trusted_origin
                or headers["access-control-request-method"] not in CORS_METHODS
                or not requested_headers <= CORS_HEADERS
            ):
                await reject(403, "CSRF_INVALID", "跨域请求不允许")
                return
            await secure_send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [
                        (b"access-control-allow-methods", ", ".join(CORS_METHODS).encode()),
                        (b"access-control-allow-headers", ", ".join(sorted(CORS_HEADERS)).encode()),
                        (b"access-control-max-age", b"600"),
                    ],
                }
            )
            await secure_send({"type": "http.response.body", "body": b""})
            return
        bounded_receive = receive
        if scope["path"] != "/api/v1/uploads":
            # 图片上传由端点先鉴权、再按实际 max_upload_bytes 有界读取；
            # 其余路径不因省略/伪造 Content-Type 或 Content-Length 绕过保护。
            length = headers.get("content-length", "")
            if (
                length.isascii()
                and length.isdigit()
                and (len(length) > 20 or int(length) > MAX_JSON_BODY_BYTES)
            ):
                await reject(413, "UPLOAD_TOO_LARGE", "请求内容超过单次大小上限")
                return
            received = 0

            async def bounded_receive() -> Message:
                nonlocal received
                message = await receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > MAX_JSON_BODY_BYTES:
                        # HTTPException 由统一 Problem 处理器处理；不能被 FastAPI
                        # 的正文解析异常捕获改成 400，更不能回显原始请求。
                        raise HTTPException(413)
                return message

        try:
            await self.app(scope, bounded_receive, secure_send)
        except Exception:
            logger.error("request_failed request_id=%s", request_id)
            if started:
                # 已开始的流不能改成另一个 HTTP 响应；让服务器关闭该流。
                raise
            await reject(500, "INTERNAL_ERROR", "服务暂时无法处理请求")
