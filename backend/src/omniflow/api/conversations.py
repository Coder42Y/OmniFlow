"""对话与轮次 HTTP 接口；SSE 只读持久事件，绝不启动调度器。"""

import json
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import StreamingResponse

from .. import conversation_models as m
from ..auth_security import fail
from ..conversations import ConversationService
from ..problems import ProblemError
from .auth import ERRORS, SESSION, WRITE, Cursor, Limit, SessionContext

router = APIRouter(responses={**ERRORS, 410: ERRORS[404], 507: ERRORS[503]})


def service(request: Request):
    return request.app.state.conversations


Service = Annotated[ConversationService, Depends(service)]


def idempotency_key(
    request: Request,
    value: Annotated[
        str,
        Header(
            alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
        ),
    ],
):
    if len(request.headers.getlist("idempotency-key")) != 1:
        fail(422, "VALIDATION_ERROR", "幂等键无效")
    return value


Key = Annotated[str, Depends(idempotency_key)]


def replay_response(response, result):
    data, replayed = result
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return data


@router.post(
    "/conversations",
    response_model=m.Conversation,
    status_code=201,
    operation_id="create_conversation",
    openapi_extra=WRITE,
)
def create(
    data: m.ConversationCreate,
    response: Response,
    context: SessionContext,
    service: Service,
    key: Key,
):
    return replay_response(response, service.create(data, key, context))


@router.get(
    "/conversations",
    response_model=m.ConversationPage,
    operation_id="list_conversations",
    openapi_extra=SESSION,
)
def conversations(
    context: SessionContext, service: Service, cursor: Cursor = None, limit: Limit = 20
):
    return service.page("conversations", context, cursor, limit)


@router.get(
    "/conversations/{conversation_id}",
    response_model=m.Conversation,
    operation_id="get_conversation",
    openapi_extra=SESSION,
)
def conversation(conversation_id: UUID, context: SessionContext, service: Service):
    return service.conversation(str(conversation_id), context)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=m.Conversation,
    operation_id="rename_conversation",
    openapi_extra=WRITE,
)
def rename(
    conversation_id: UUID, data: m.ConversationPatch, context: SessionContext, service: Service
):
    return service.rename(str(conversation_id), data, context)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    operation_id="delete_conversation",
    openapi_extra=WRITE,
)
def delete(conversation_id: UUID, context: SessionContext, service: Service):
    service.delete(str(conversation_id), context)
    return Response(status_code=204)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=m.MessageAccepted,
    status_code=202,
    operation_id="create_message",
    openapi_extra=WRITE,
)
def enqueue(
    conversation_id: UUID,
    data: m.MessageCreate,
    response: Response,
    context: SessionContext,
    service: Service,
    key: Key,
):
    return replay_response(response, service.enqueue(str(conversation_id), data, key, context))


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=m.MessagePage,
    operation_id="list_messages",
    openapi_extra=SESSION,
)
def messages(
    conversation_id: UUID,
    context: SessionContext,
    service: Service,
    cursor: Cursor = None,
    limit: Limit = 20,
):
    return service.page("messages", context, cursor, limit, str(conversation_id))


@router.get(
    "/conversations/{conversation_id}/runs",
    response_model=m.RunPage,
    operation_id="list_runs",
    openapi_extra=SESSION,
)
def runs(
    conversation_id: UUID,
    context: SessionContext,
    service: Service,
    cursor: Cursor = None,
    limit: Limit = 20,
):
    return service.page("runs", context, cursor, limit, str(conversation_id))


@router.get(
    "/conversations/{conversation_id}/snapshot",
    response_model=m.ConversationSnapshot,
    operation_id="get_conversation_snapshot",
    openapi_extra=SESSION,
)
def snapshot(conversation_id: UUID, context: SessionContext, service: Service):
    return service.snapshot(str(conversation_id), context)


@router.get("/runs/{run_id}", response_model=m.Run, operation_id="get_run", openapi_extra=SESSION)
def run(run_id: UUID, context: SessionContext, service: Service):
    return service.run(str(run_id), context)


@router.post(
    "/runs/{run_id}/cancel",
    response_model=m.Run,
    status_code=202,
    operation_id="cancel_run",
    openapi_extra=WRITE,
)
def cancel(run_id: UUID, context: SessionContext, service: Service):
    return service.cancel(str(run_id), context)


def control(code):
    return (
        "event: control\ndata: "
        + json.dumps(
            {"code": code, "message": "订阅已停止，请重新验证身份或获取快照。"}, ensure_ascii=True
        )
        + "\n\n"
    )


async def event_stream(request, service, cid, cursor, context):
    while True:
        if request.app.state.stream_shutdown.is_set():
            yield control("SERVICE_RESTARTING")
            return
        if await request.is_disconnected():
            return
        try:
            event = await anyio.to_thread.run_sync(service.next_event, cid, cursor, context)
        except ProblemError as exc:
            code = (
                exc.code
                if exc.code in ("AUTH_REQUIRED", "RESOURCE_NOT_FOUND", "EVENT_CURSOR_EXPIRED")
                else "SERVICE_RESTARTING"
            )
            yield control(code)
            return
        except Exception:
            # 已发送 200，关闭流而不是让服务器记录原始异常/数据。
            yield control("SERVICE_RESTARTING")
            return
        if event:
            cursor = int(event["event_id"])
            yield (
                f"id: {cursor}\nevent: {event['type']}\ndata: "
                + json.dumps(event, ensure_ascii=True, separators=(",", ":"))
                + "\n\n"
            )
        else:
            yield ": heartbeat\n\n"
            await anyio.sleep(service.settings.event_poll_seconds)


@router.get(
    "/conversations/{conversation_id}/events",
    operation_id="stream_events",
    openapi_extra=SESSION,
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def events(
    conversation_id: UUID,
    request: Request,
    context: SessionContext,
    service: Service,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after_event_id: Annotated[str | None, Query()] = None,
):
    # 手工校验值使坏游标为契约的 400，并让有效 header 完全覆盖坏 query。
    if len(request.headers.getlist("last-event-id")) > 1:
        fail(400, "INVALID_EVENT_CURSOR", "事件游标无效")
    raw = last_event_id if last_event_id is not None else after_event_id
    cid = str(conversation_id)
    cursor = await anyio.to_thread.run_sync(service.prepare_events, cid, raw, context)
    return StreamingResponse(
        event_stream(request, service, cid, cursor, context),
        media_type="text/event-stream",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
    )
