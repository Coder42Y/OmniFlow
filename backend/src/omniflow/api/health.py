import sqlite3

from fastapi import APIRouter, Request

from ..db import StorageError
from ..problems import Health, Problem, ProblemError

router = APIRouter(prefix="/health", tags=["系统"])
RESPONSE_HEADERS = {
    "X-Request-ID": {"schema": {"type": "string", "format": "uuid"}},
    "Cache-Control": {"schema": {"type": "string"}},
}
PROBLEM_RESPONSES = {
    status: {
        "description": "请求失败" if status != 503 else "本地必要组件未就绪",
        "headers": RESPONSE_HEADERS,
        "content": {"application/problem+json": {"schema": Problem.model_json_schema()}},
    }
    for status in (400, 429, 500, 503)
}


@router.get(
    "/live",
    response_model=Health,
    operation_id="health_live",
    summary="进程存活",
    responses={200: {"headers": RESPONSE_HEADERS}, **PROBLEM_RESPONSES},
)
def live() -> Health:
    return Health(status="ok")


@router.get(
    "/ready",
    response_model=Health,
    operation_id="health_ready",
    summary="数据库等必要组件就绪",
    responses={200: {"headers": RESPONSE_HEADERS}, **PROBLEM_RESPONSES},
)
def ready(request: Request) -> Health:
    try:
        request.app.state.database.check_ready()
    except (OSError, sqlite3.Error, StorageError):
        # 健康接口按契约使用 503；新增写入的磁盘保护后续使用 507。
        raise ProblemError(
            503,
            "STORAGE_UNAVAILABLE",
            "本地必要组件未就绪",
            "本地存储不可用或迁移尚未完成。",
            retryable=True,
        ) from None
    if request.app.state.settings.provider_mode == "real":
        from ..media_provider import check_generation
        from ..runtime import get_runtime

        runtime = get_runtime(request.app.state.database)
        for kind in runtime.config.enabled_kinds:
            check_generation(request.app.state.tasks.provider, kind)
    return Health(status="ok")
