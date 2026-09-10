"""账号与管理员 HTTP 边界；身份依赖复用，服务写事务再次校验当前权限。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from .. import auth_models as m
from ..auth_security import (
    AuthContext,
    check_csrf,
    cookie_context,
    require_admin,
    require_context,
    require_session,
)
from ..auth_service import AuthService, public_user
from ..problems import Problem

SESSION = {"security": [{"SessionCookie": []}]}
WRITE = {"security": [{"SessionCookie": [], "CsrfHeader": []}]}
FORM = {
    "security": [
        {"CsrfContextCookie": [], "CsrfHeader": []},
        {"SessionCookie": [], "CsrfHeader": []},
    ]
}
ERRORS = {
    code: {
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
        },
        "model": Problem,
    }
    for code in (400, 401, 403, 404, 409, 413, 422, 429, 500, 503)
}
router = APIRouter(responses=ERRORS)


def service(request: Request) -> AuthService:
    return request.app.state.auth


def form_context(request: Request) -> AuthContext:
    context = cookie_context(request)
    check_csrf(request, context)
    with request.app.state.database.connect(readonly=True) as connection:
        require_context(connection, context)
    peer = request.client.host if request.client else "unknown"
    service(request).rate_limit(peer)
    return context


def session_context(request: Request) -> AuthContext:
    context = cookie_context(request)
    with request.app.state.database.connect(readonly=True) as connection:
        require_session(connection, context)
    if request.method in ("POST", "PATCH", "DELETE"):
        check_csrf(request, context)
    return context


def admin_context(request: Request, context: Annotated[AuthContext, Depends(session_context)]):
    with request.app.state.database.connect(readonly=True) as connection:
        require_admin(connection, context)
    return context


FormContext = Annotated[AuthContext, Depends(form_context)]
SessionContext = Annotated[AuthContext, Depends(session_context)]
AdminContext = Annotated[AuthContext, Depends(admin_context)]
Service = Annotated[AuthService, Depends(service)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=512)]
Limit = Annotated[int, Query(ge=1, le=100)]


def set_cookie(response, name, raw, ttl):
    response.set_cookie(
        name, raw, max_age=ttl, secure=True, httponly=True, samesite="lax", path="/"
    )


def clear_cookies(response, settings):
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(name, secure=True, httponly=True, samesite="lax", path="/")


def login_response(response, auth, result):
    data, raw = result
    clear_cookies(response, auth.settings)
    set_cookie(response, auth.settings.session_cookie_name, raw, auth.settings.session_ttl_seconds)
    return data


@router.get("/auth/policy", response_model=m.AuthPolicy, operation_id="get_auth_policy")
def policy(auth: Service):
    settings = auth.settings
    return m.AuthPolicy(
        username_pattern=settings.username_pattern,
        username_normalization="trim_ascii_spaces_then_lowercase",
        password_min_length=settings.password_min_length,
        password_max_length=settings.password_max_length,
        session_ttl_seconds=settings.session_ttl_seconds,
        invitation_ttl_default_seconds=settings.invitation_ttl_seconds,
        password_reset_ttl_default_seconds=settings.password_reset_ttl_seconds,
    )


@router.get("/auth/csrf", response_model=m.CsrfToken, operation_id="get_csrf")
def csrf(request: Request, response: Response, auth: Service):
    auth.rate_limit(request.client.host if request.client else "unknown", scope="csrf")
    data, raw = auth.bootstrap_csrf(cookie_context(request))
    if raw is not None:
        clear_cookies(response, auth.settings)
        set_cookie(response, auth.settings.csrf_cookie_name, raw, auth.settings.csrf_ttl_seconds)
    return data


@router.post(
    "/auth/invitations/validate",
    response_model=m.InviteTokenValidity,
    operation_id="validate_invite",
    openapi_extra=FORM,
)
def validate_invite(data: m.TokenInput, context: FormContext, auth: Service):
    return auth.validate_token("invite", data.token, context)


@router.post(
    "/auth/password-resets/validate",
    response_model=m.TokenValidity,
    operation_id="validate_password_reset",
    openapi_extra=FORM,
)
def validate_reset(data: m.TokenInput, context: FormContext, auth: Service):
    return auth.validate_token("reset", data.token, context)


@router.post(
    "/auth/register",
    status_code=201,
    response_model=m.LoginResult,
    operation_id="register",
    openapi_extra=FORM,
)
def register(data: m.RegisterInput, response: Response, context: FormContext, auth: Service):
    return login_response(response, auth, auth.register(data, context))


@router.post("/auth/login", response_model=m.LoginResult, operation_id="login", openapi_extra=FORM)
def login(
    data: m.LoginInput, request: Request, response: Response, context: FormContext, auth: Service
):
    auth.rate_limit(
        request.client.host if request.client else "unknown", username=data.username, scope="login"
    )
    return login_response(response, auth, auth.login(data, context))


@router.get("/auth/me", response_model=m.User, operation_id="get_me", openapi_extra=SESSION)
def me(context: SessionContext, auth: Service):
    with auth.database.connect(readonly=True) as connection:
        return public_user(require_session(connection, context))


@router.post("/auth/logout", status_code=204, operation_id="logout", openapi_extra=WRITE)
def logout(context: SessionContext, auth: Service):
    auth.logout(context)
    response = Response(status_code=204)
    clear_cookies(response, auth.settings)
    return response


@router.post(
    "/auth/password-resets/complete",
    status_code=204,
    operation_id="complete_password_reset",
    openapi_extra=FORM,
)
def complete_reset(data: m.PasswordResetInput, context: FormContext, auth: Service):
    auth.complete_reset(data, context)
    # 不自动登录。若当前浏览器属于另一账号，不撤销/清除那个账号的会话。
    return Response(status_code=204)


@router.post(
    "/admin/invitations",
    status_code=201,
    response_model=m.InviteIssued,
    operation_id="admin_create_invite",
    openapi_extra=WRITE,
)
def issue_invite(data: m.InviteCreate, context: AdminContext, auth: Service):
    return auth.issue_invite(data, context)


@router.get(
    "/admin/invitations",
    response_model=m.InvitePage,
    operation_id="admin_list_invites",
    openapi_extra=SESSION,
)
def list_invites(context: AdminContext, auth: Service, cursor: Cursor = None, limit: Limit = 20):
    return auth.list_metadata("invites", cursor, limit, context)


@router.post(
    "/admin/invitations/{invitation_id}/revoke",
    response_model=m.Invite,
    operation_id="admin_revoke_invite",
    openapi_extra=WRITE,
)
def revoke_invite(invitation_id: UUID, context: AdminContext, auth: Service):
    return auth.revoke_invite(str(invitation_id), context)


@router.get(
    "/admin/users",
    response_model=m.UserPage,
    operation_id="admin_list_users",
    openapi_extra=SESSION,
)
def list_users(context: AdminContext, auth: Service, cursor: Cursor = None, limit: Limit = 20):
    return auth.list_metadata("users", cursor, limit, context)


@router.patch(
    "/admin/users/{user_id}",
    response_model=m.User,
    operation_id="admin_set_user_status",
    openapi_extra=WRITE,
)
def set_status(user_id: UUID, data: m.UserStatusPatch, context: AdminContext, auth: Service):
    return auth.set_user_status(str(user_id), data.status, context)


@router.post(
    "/admin/users/{user_id}/password-resets",
    status_code=201,
    response_model=m.PasswordResetIssued,
    operation_id="admin_issue_password_reset",
    openapi_extra=WRITE,
)
def issue_reset(user_id: UUID, data: m.PasswordResetIssue, context: AdminContext, auth: Service):
    return auth.issue_reset(str(user_id), data, context)


@router.get(
    "/admin/generation-policy",
    response_model=m.GenerationPolicy,
    operation_id="admin_get_generation_policy",
    openapi_extra=SESSION,
)
def get_generation_policy(context: AdminContext, auth: Service):
    return auth.generation_policy(context)


@router.patch(
    "/admin/generation-policy",
    response_model=m.GenerationPolicy,
    operation_id="admin_patch_generation_policy",
    openapi_extra=WRITE,
)
def patch_generation_policy(data: m.GenerationPolicyPatch, context: AdminContext, auth: Service):
    return auth.generation_policy(context, data)
