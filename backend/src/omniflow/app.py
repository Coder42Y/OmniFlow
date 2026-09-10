"""工厂入口：uvicorn omniflow.app:create_app --factory（使用本地 CLI 更安全）。"""

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from . import __version__
from .api.artifacts import router as artifact_router
from .api.auth import router as auth_router
from .api.conversations import router as conversation_router
from .api.health import router as health_router
from .api.tasks import router as task_router
from .artifacts import ArtifactService
from .auth_service import AuthService
from .config import Settings
from .conversations import ConversationService
from .db import Database
from .middleware import SecurityBoundary
from .openapi import complete_schema
from .problems import ProblemError, handle_http, handle_problem, handle_validation
from .tasks import TaskService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 不自动迁移、导入旧库或调用提供方；未迁移时 live=200、ready=503。
        app.state.started = True
        app.state.stream_shutdown.clear()
        try:
            yield
        finally:
            app.state.stream_shutdown.set()
            app.state.started = False

    app = FastAPI(
        title="OmniFlow API",
        version=__version__,
        debug=False,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = Database(settings)
    app.state.auth = AuthService(app.state.database)
    app.state.conversations = ConversationService(app.state.database)
    app.state.artifacts = ArtifactService(app.state.database)
    app.state.tasks = TaskService(app.state.database)
    app.state.stream_shutdown = threading.Event()
    app.state.started = False
    app.add_exception_handler(ProblemError, handle_problem)
    app.add_exception_handler(RequestValidationError, handle_validation)
    app.add_exception_handler(HTTPException, handle_http)
    app.add_middleware(SecurityBoundary, settings=settings)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(conversation_router, prefix="/api/v1")
    app.include_router(artifact_router, prefix="/api/v1")
    app.include_router(task_router, prefix="/api/v1")
    original_openapi = app.openapi

    def account_openapi():
        schema = original_openapi()
        schema["components"]["securitySchemes"] = {
            "SessionCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": settings.session_cookie_name,
            },
            "CsrfContextCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": settings.csrf_cookie_name,
            },
            "CsrfHeader": {"type": "apiKey", "in": "header", "name": "X-CSRF-Token"},
            "MediaGrantToken": {"type": "apiKey", "in": "query", "name": "token"},
        }
        for path_name, path in schema["paths"].items():
            for method, operation in path.items():
                if path_name == "/api/v1/uploads" and method == "post":
                    multipart = operation["requestBody"]["content"]["multipart/form-data"]
                    if "$ref" not in multipart["schema"]:
                        schema["components"]["schemas"]["UploadInput"] = multipart["schema"]
                        multipart["schema"] = {"$ref": "#/components/schemas/UploadInput"}
                for code, response in operation.get("responses", {}).items():
                    headers = response.setdefault("headers", {})
                    headers.setdefault(
                        "X-Request-ID", {"schema": {"type": "string", "format": "uuid"}}
                    )
                    headers.setdefault("Cache-Control", {"schema": {"type": "string"}})
                    if code in ("201", "202") and path_name in {
                        "/api/v1/conversations",
                        "/api/v1/conversations/{conversation_id}/messages",
                        "/api/v1/uploads",
                        "/api/v1/tasks",
                        "/api/v1/artifacts",
                        "/api/v1/artifacts/{artifact_id}/text-versions",
                        "/api/v1/conversations/{conversation_id}/reference-confirmations",
                    }:
                        headers["Idempotency-Replayed"] = {"schema": {"type": "boolean"}}
                    if code == "429":
                        headers["Retry-After"] = {
                            "schema": {"type": "string", "pattern": "^[0-9]+$"}
                        }
                    if code in ("200", "201", "204") and path_name in {
                        "/api/v1/auth/csrf",
                        "/api/v1/auth/login",
                        "/api/v1/auth/register",
                        "/api/v1/auth/logout",
                    }:
                        headers["Set-Cookie"] = {"schema": {"type": "string"}}
                    if code.isdigit() and int(code) >= 400:
                        content = response.setdefault("content", {})
                        content.pop("application/json", None)
                        content["application/problem+json"] = {
                            "schema": {"$ref": "#/components/schemas/Problem"}
                        }
                    if path_name.endswith("/content"):
                        if code in ("200", "206"):
                            headers.update(
                                {
                                    "Content-Length": {"schema": {"type": "integer", "minimum": 0}},
                                    "Content-Type": {"schema": {"type": "string"}},
                                    "Accept-Ranges": {
                                        "schema": {"type": "string", "enum": ["bytes"]}
                                    },
                                    "Content-Disposition": {"schema": {"type": "string"}},
                                }
                            )
                        if code in ("206", "416"):
                            headers["Content-Range"] = {"schema": {"type": "string"}}
                        if method == "head":
                            response.pop("content", None)
        return complete_schema(schema)

    app.openapi = account_openapi
    return app
