"""统一错误边界；错误与日志绝不回显请求体、URL、凭据或异常堆栈。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

PageCursor = Annotated[str, Field(min_length=1, max_length=512)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class FieldError(StrictModel):
    field: str
    code: str
    message: str


class Problem(StrictModel):
    type: str = "about:blank"
    title: str
    status: int = Field(ge=400, le=599)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str
    request_id: UUID
    retryable: bool = False
    errors: list[FieldError] | None = None


class Health(StrictModel):
    status: Literal["ok", "unavailable"]


class ProblemError(Exception):
    """仅允许服务代码构造固定的、可公开的描述，不能传入原始提供方错误。"""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str,
        *,
        retryable: bool = False,
        errors: list[FieldError] | None = None,
        retry_after: int | None = None,
        content_range: str | None = None,
    ):
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.retryable = retryable
        self.errors = errors
        self.retry_after = retry_after
        self.content_range = content_range
        super().__init__(code)


def problem_response(
    request_id: str,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    retryable: bool = False,
    errors: list[FieldError] | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    problem = Problem(
        title=title,
        status=status,
        code=code,
        detail=detail,
        request_id=UUID(request_id),
        retryable=retryable,
        errors=errors,
    )
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type="application/problem+json",
        headers={"Retry-After": str(retry_after)} if retry_after is not None else None,
    )


async def handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
    response = problem_response(
        request.state.request_id,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        retryable=exc.retryable,
        errors=exc.errors,
        retry_after=exc.retry_after,
    )
    if exc.content_range is not None:
        response.headers["Content-Range"] = exc.content_range
    return response


async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 只接受静态字段名，绝不透传 input/ctx/msg/完整 loc 或未知字段名。
    allowed = {
        "username",
        "password",
        "invite_token",
        "token",
        "reset_token",
        "new_password",
        "expires_in_seconds",
        "verification_method",
        "note",
        "status",
        "limit",
        "cursor",
        "text_enabled",
        "image_enabled",
        "ai_video_enabled",
        "local_motion_enabled",
        "title",
        "content",
        "client_message_id",
        "attachment_version_ids",
        "selected_version_id",
        "reference_confirmation_id",
        "generation_permission",
    }
    fields = sorted(
        {e["loc"][1] for e in exc.errors() if len(e["loc"]) == 2 and e["loc"][1] in allowed}
    )
    return problem_response(
        request.state.request_id,
        status=422,
        code="VALIDATION_ERROR",
        title="请求参数无效",
        detail="请求结构或字段不符合要求。",
        errors=[FieldError(field=f, code="INVALID", message="字段不符合要求。") for f in fields]
        or None,
    )


async def handle_http(request: Request, exc: HTTPException) -> JSONResponse:
    codes = {
        400: ("VALIDATION_ERROR", "请求无效"),
        401: ("AUTH_REQUIRED", "请先登录"),
        403: ("FORBIDDEN", "操作不允许"),
        404: ("RESOURCE_NOT_FOUND", "资源不存在或当前用户不可访问"),
        405: ("METHOD_NOT_ALLOWED", "请求方法不允许"),
        413: ("UPLOAD_TOO_LARGE", "请求内容过大"),
        415: ("UNSUPPORTED_MEDIA_TYPE", "不支持此媒体类型"),
        429: ("RATE_LIMITED", "请求过于频繁"),
    }
    code, message = codes.get(exc.status_code, ("INTERNAL_ERROR", "请求暂时无法处理"))
    # 不透传 exc.detail/headers，避免其他库意外包含输入或凭据。
    return problem_response(
        request.state.request_id,
        status=exc.status_code,
        code=code,
        title=message,
        detail=message + "。",
    )
