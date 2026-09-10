"""素材 HTTP 边界：先身份/CSRF，再有界 multipart；下载每次复核授权。"""

from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BeforeValidator
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import StreamingResponse

from .. import artifact_models as m
from ..artifacts import ArtifactService, TaskMediaService, version_owned
from ..auth_security import fail, require_session
from ..conversations import not_found
from ..media_storage import EXTENSIONS, byte_range
from ..problems import ProblemError
from .auth import ERRORS, SESSION, WRITE, Cursor, Limit, SessionContext
from .conversations import Key, replay_response

router = APIRouter(responses={**ERRORS, **{n: ERRORS[404] for n in (410, 413, 415, 416, 507)}})


def service(request: Request):
    return request.app.state.artifacts


Service = Annotated[ArtifactService, Depends(service)]


class BoundedMultipartParser(MultiPartParser):
    """使用库解析，补齐总字节/文件/头部上限和结束边界，不把文件写到全局临时区。"""

    def __init__(self, headers, stream, max_bytes):
        super().__init__(headers, stream, max_files=1, max_fields=1, max_part_size=128)
        self.spool_max_size = max_bytes + 1  # 单文件上限以内只在内存，随后进入本地私有临时区。
        self.max_bytes = max_bytes
        self.file_bytes = 0
        self.header_bytes = 0
        self.finished = False

    def on_part_data(self, data, start, end):
        if self._current_part.file is not None:
            self.file_bytes += end - start
            if self.file_bytes > self.max_bytes:
                fail(413, "UPLOAD_TOO_LARGE", "图片超过单次上传大小上限")
        super().on_part_data(data, start, end)

    def header_limit(self, size):
        self.header_bytes += size
        if self.header_bytes > 8192:
            fail(422, "VALIDATION_ERROR", "上传表单头部过长")

    def on_header_field(self, data, start, end):
        self.header_limit(end - start)
        super().on_header_field(data, start, end)

    def on_header_value(self, data, start, end):
        self.header_limit(end - start)
        super().on_header_value(data, start, end)

    def on_end(self):
        self.finished = True


UPLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["file"],
    "properties": {
        "file": {"type": "string", "format": "binary"},
        "conversation_id": {"type": "string", "format": "uuid"},
    },
}


@router.post(
    "/uploads",
    response_model=m.ArtifactCreated,
    status_code=201,
    operation_id="upload_image",
    openapi_extra={
        **WRITE,
        "requestBody": {
            "required": True,
            "content": {"multipart/form-data": {"schema": UPLOAD_SCHEMA}},
        },
    },
)
async def upload(
    request: Request, response: Response, context: SessionContext, service: Service, key: Key
):
    # 依赖在读取请求体前已鉴权并校验 CSRF；不使用会先解析完整表单的 File 依赖。
    if len(request.headers.getlist("content-type")) != 1:
        fail(415, "UNSUPPORTED_MEDIA_TYPE", "需要 multipart 图片上传")
    media_type, options = parse_options_header(request.headers.get("content-type", ""))
    if media_type != b"multipart/form-data" or not 1 <= len(options.get(b"boundary", b"")) <= 70:
        fail(415, "UNSUPPORTED_MEDIA_TYPE", "需要有效 multipart 图片上传")
    max_bytes = service.settings.max_upload_bytes

    async def bounded_stream():
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes + 16384:
                fail(413, "UPLOAD_TOO_LARGE", "上传请求超过单次大小上限")
            for offset in range(0, len(chunk), 65536):
                yield chunk[offset : offset + 65536]

    parser = BoundedMultipartParser(request.headers, bounded_stream(), max_bytes)
    try:
        form = await parser.parse()
    except (MultiPartException, MultipartParseError, ValueError):
        fail(422, "VALIDATION_ERROR", "上传表单无效")
    try:
        items = form.multi_items()
        if (
            not parser.finished
            or len(items) != len(form)
            or set(form) - {"file", "conversation_id"}
            or not isinstance(form.get("file"), UploadFile)
        ):
            fail(422, "VALIDATION_ERROR", "上传表单字段无效")
        file = form["file"]
        if len(file.headers.getlist("content-type")) != 1:
            fail(415, "UNSUPPORTED_MEDIA_TYPE", "需要唯一的图片内容类型")
        raw_cid = form.get("conversation_id")
        try:
            cid = str(UUID(raw_cid)) if raw_cid is not None else None
        except (ValueError, TypeError, AttributeError):
            fail(422, "VALIDATION_ERROR", "对话标识无效")
        content = await file.read(max_bytes + 1)
        result = await anyio.to_thread.run_sync(
            service.upload,
            content,
            file.content_type,
            cid,
            key,
            context,
        )
        return replay_response(response, result)
    finally:
        await form.close()
        # 不完整的 multipart 文件可能不在 form.items 内，也必须关闭。
        for stream in parser._files_to_close_on_error:
            stream.close()


@router.post(
    "/artifacts",
    response_model=m.ArtifactCreated,
    status_code=201,
    operation_id="create_text_artifact",
    openapi_extra=WRITE,
)
def create_text(
    data: m.TextArtifactCreate,
    response: Response,
    context: SessionContext,
    service: Service,
    key: Key,
):
    return replay_response(response, service.save_text(data, key, context))


@router.post(
    "/artifacts/{artifact_id}/text-versions",
    response_model=m.ArtifactCreated,
    status_code=201,
    operation_id="create_text_version",
    openapi_extra=WRITE,
)
def text_version(
    artifact_id: UUID,
    data: m.TextVersionCreate,
    response: Response,
    context: SessionContext,
    service: Service,
    key: Key,
):
    return replay_response(response, service.save_text(data, key, context, str(artifact_id)))


@router.get(
    "/artifacts",
    response_model=m.ArtifactPage,
    operation_id="list_artifacts",
    openapi_extra=SESSION,
)
def artifacts(
    context: SessionContext,
    service: Service,
    cursor: Cursor = None,
    limit: Limit = 20,
    conversation_id: UUID | None = None,
    kind: m.ArtifactKind | None = None,
):
    return service.page(
        context, cursor, limit, cid=str(conversation_id) if conversation_id else None, kind=kind
    )


@router.get(
    "/artifacts/{artifact_id}",
    response_model=m.Artifact,
    operation_id="get_artifact",
    openapi_extra=SESSION,
)
def artifact(artifact_id: UUID, context: SessionContext, service: Service):
    return service.get(str(artifact_id), context)


@router.get(
    "/artifacts/{artifact_id}/versions",
    response_model=m.ArtifactVersionPage,
    operation_id="list_artifact_versions",
    openapi_extra=SESSION,
)
def versions(
    artifact_id: UUID,
    context: SessionContext,
    service: Service,
    cursor: Cursor = None,
    limit: Limit = 20,
):
    return service.page(context, cursor, limit, aid=str(artifact_id))


@router.get(
    "/artifacts/{artifact_id}/versions/{version_id}",
    response_model=m.ArtifactVersion,
    operation_id="get_artifact_version",
    openapi_extra=SESSION,
)
def version(artifact_id: UUID, version_id: UUID, context: SessionContext, service: Service):
    return service.get(str(artifact_id), context, str(version_id))


@router.delete(
    "/artifacts/{artifact_id}",
    response_model=m.DeletionReceipt,
    status_code=202,
    operation_id="delete_artifact",
    openapi_extra=WRITE,
)
def delete(artifact_id: UUID, context: SessionContext, service: Service):
    return service.delete(str(artifact_id), context)


@router.post(
    "/conversations/{conversation_id}/reference-confirmations",
    response_model=m.ReferenceConfirmation,
    status_code=201,
    operation_id="confirm_reference",
    openapi_extra=WRITE,
)
def confirm(
    conversation_id: UUID,
    data: m.ReferenceConfirmationCreate,
    response: Response,
    context: SessionContext,
    service: Service,
    key: Key,
):
    return replay_response(response, service.confirm(str(conversation_id), data, key, context))


@router.get(
    "/capabilities",
    response_model=m.Capabilities,
    operation_id="get_capabilities",
    openapi_extra=SESSION,
)
def capabilities(request: Request, context: SessionContext, service: Service):
    return service.capabilities(context, request.app.state.tasks.provider)


class ProtectedFileResponse(StreamingResponse):
    def __init__(self, stream, *args, **kwargs):
        self.stream = stream
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.stream.close()  # 包含客户端在首次迭代前断开，而不只依靠迭代器 finally。


def file_response(request, service, authorize, *, download=False, allow_range=True):
    with service.database.snapshot() as connection:
        row = authorize(connection)
        stream = service.storage.open(row)
    try:
        size = row["byte_size"]
        raw = None if request.method == "HEAD" or not allow_range else request.headers.get("range")
        if raw is not None and len(request.headers.getlist("range")) != 1:
            raw = "invalid"
        selection = byte_range(raw, size)
        if selection is None:
            raise ProblemError(
                416,
                "RANGE_NOT_SATISFIABLE",
                "文件范围无效",
                "只支持可满足的单区间。",
                content_range=f"bytes */{size}",
            )
        start, end, status = selection
        length = max(0, end - start + 1)
        disposition = "attachment" if download else "inline"
        filename = f"{row['id']}.{EXTENSIONS[row['media_type']]}"
        headers = {
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'{disposition}; filename="{filename}"',
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if request.method == "HEAD":
            stream.close()
            return Response(b"", media_type=row["media_type"], headers=headers)
        stream.seek(start)

        def chunks():
            remaining = length
            while remaining:
                try:
                    with service.database.snapshot() as connection:
                        authorize(connection)
                except ProblemError:
                    # 已发送的副本不可撤回；撤销后不再交付下一块，不输出错误秘密。
                    return
                data = stream.read(min(65536, remaining))
                if not data:
                    return
                remaining -= len(data)
                yield data

        return ProtectedFileResponse(
            stream, chunks(), status_code=status, media_type=row["media_type"], headers=headers
        )
    except BaseException:
        stream.close()
        raise


BINARY_CONTENT = {kind: {"schema": {"type": "string", "format": "binary"}} for kind in EXTENSIONS}
FILE_RESPONSES = {200: {"content": BINARY_CONTENT}, 206: {"content": BINARY_CONTENT}}


@router.get(
    "/artifacts/{artifact_id}/versions/{version_id}/content",
    response_class=Response,
    responses=FILE_RESPONSES,
    operation_id="get_artifact_content",
    openapi_extra=SESSION,
)
def content(
    artifact_id: UUID,
    version_id: UUID,
    request: Request,
    context: SessionContext,
    service: Service,
    download: bool = False,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
):
    def authorize(connection):
        owner = require_session(connection, context)["id"]
        return version_owned(connection, str(version_id), owner, aid=str(artifact_id))

    return file_response(request, service, authorize, download=download)


@router.head(
    "/artifacts/{artifact_id}/versions/{version_id}/content",
    response_class=Response,
    operation_id="head_artifact_content",
    openapi_extra=SESSION,
)
def content_head(
    artifact_id: UUID,
    version_id: UUID,
    request: Request,
    context: SessionContext,
    service: Service,
    download: bool = False,
):
    return content(artifact_id, version_id, request, context, service, download)


GRANT = {"security": [{"MediaGrantToken": []}]}


def grant_identity(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        not_found()


GrantId = Annotated[UUID, BeforeValidator(grant_identity)]


@router.get(
    "/media-grants/{grant_id}/content",
    response_class=Response,
    responses={
        200: {
            "content": {
                key: value for key, value in BINARY_CONTENT.items() if key.startswith("image/")
            }
        }
    },
    operation_id="get_provider_reference",
    openapi_extra=GRANT,
)
def grant_content(
    grant_id: GrantId,
    request: Request,
    service: Service,
    token: Annotated[str | None, Query(include_in_schema=False)] = None,
):
    if len(request.query_params.getlist("token")) != 1:
        not_found()
    grants = TaskMediaService(service.database)

    def authorize(connection):
        return grants.grant_version(connection, str(grant_id), token)

    return file_response(request, service, authorize, allow_range=False)


@router.head(
    "/media-grants/{grant_id}/content",
    response_class=Response,
    operation_id="head_provider_reference",
    openapi_extra=GRANT,
)
def grant_head(
    grant_id: GrantId,
    request: Request,
    service: Service,
    token: Annotated[str | None, Query(include_in_schema=False)] = None,
):
    return grant_content(grant_id, request, service, token)
