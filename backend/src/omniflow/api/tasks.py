"""任务 HTTP 路由；202 仅表示持久受理，查询/订阅不执行工作。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from .. import task_models as m
from ..tasks import TaskService
from .auth import ERRORS, SESSION, WRITE, AdminContext, Cursor, Limit, SessionContext
from .conversations import Key, replay_response

router = APIRouter(responses={**ERRORS, 410: ERRORS[404], 507: ERRORS[503]})


def service(request: Request):
    return request.app.state.tasks


Service = Annotated[TaskService, Depends(service)]


@router.post(
    "/tasks",
    response_model=m.Task,
    status_code=202,
    operation_id="create_task",
    openapi_extra=WRITE,
)
def create(
    data: m.TaskCreate, response: Response, context: SessionContext, service: Service, key: Key
):
    return replay_response(response, service.create(data, key, context))


@router.get("/tasks", response_model=m.TaskPage, operation_id="list_tasks", openapi_extra=SESSION)
def page(
    context: SessionContext,
    service: Service,
    cursor: Cursor = None,
    limit: Limit = 20,
    conversation_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[m.TaskStatus | None, Query()] = None,
):
    return service.page(
        context, cursor, limit, cid=str(conversation_id) if conversation_id else None, status=status
    )


@router.get(
    "/tasks/{task_id}", response_model=m.Task, operation_id="get_task", openapi_extra=SESSION
)
def get(task_id: UUID, context: SessionContext, service: Service):
    return service.get(str(task_id), context)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=m.Task,
    operation_id="cancel_task",
    openapi_extra=WRITE,
)
def cancel(task_id: UUID, context: SessionContext, service: Service):
    return service.cancel(str(task_id), context)


@router.post(
    "/tasks/{task_id}/recover",
    response_model=m.Task,
    status_code=202,
    operation_id="recover_task",
    openapi_extra=WRITE,
)
def recover(task_id: UUID, context: SessionContext, service: Service):
    return service.recover(str(task_id), context)


@router.get(
    "/admin/tasks",
    response_model=m.OperationalTaskPage,
    operation_id="admin_list_operational_tasks",
    openapi_extra={**SESSION, "x-required-role": "admin"},
)
def operational(
    context: AdminContext,
    service: Service,
    cursor: Cursor = None,
    limit: Limit = 20,
    status: Annotated[m.TaskStatus | None, Query()] = None,
):
    return service.page(context, cursor, limit, status=status, admin=True)
