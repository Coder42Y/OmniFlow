"""公开字段白名单；秘密字段不参与 repr，不接受未声明字段。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from .problems import StrictModel

Token = Annotated[str, Field(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
Password = Annotated[str, Field(min_length=1, max_length=128)]
Username = Annotated[str, Field(min_length=1, max_length=128)]


class AuthPolicy(StrictModel):
    username_pattern: str
    username_normalization: str
    password_min_length: int = Field(ge=1)
    password_max_length: int = Field(ge=1)
    session_ttl_seconds: int = Field(ge=1)
    invitation_ttl_default_seconds: int = Field(ge=1)
    password_reset_ttl_default_seconds: int = Field(ge=1)


class CsrfToken(StrictModel):
    csrf_token: str = Field(min_length=32, repr=False)
    expires_at: datetime


class TokenInput(StrictModel):
    token: Token = Field(repr=False, json_schema_extra={"writeOnly": True})


class LoginInput(StrictModel):
    username: Username
    password: Password = Field(repr=False, json_schema_extra={"writeOnly": True})


class RegisterInput(LoginInput):
    invite_token: Token = Field(repr=False, json_schema_extra={"writeOnly": True})


class PasswordResetInput(StrictModel):
    reset_token: Token = Field(repr=False, json_schema_extra={"writeOnly": True})
    new_password: Password = Field(repr=False, json_schema_extra={"writeOnly": True})


class TokenValidity(StrictModel):
    valid: bool
    expires_at: datetime


class InviteTokenValidity(StrictModel):
    valid: bool
    expires_at: datetime | None


class User(StrictModel):
    id: UUID
    username: str
    role: Literal["user", "admin"]
    status: Literal["active", "disabled"]
    created_at: datetime


class LoginResult(StrictModel):
    user: User
    session_expires_at: datetime
    csrf_token: str = Field(min_length=32, repr=False)


class InviteCreate(StrictModel):
    # 未提供时由服务读取 Settings；schema 不宣称一个固定部署默认值。
    expires_in_seconds: int | None = Field(default_factory=lambda: 604800, ge=1, strict=True)


class Invite(StrictModel):
    id: UUID
    status: Literal["active", "used", "revoked", "expired"]
    created_at: datetime
    expires_at: datetime | None


class InviteIssued(StrictModel):
    invite: Invite
    invite_url: str = Field(repr=False, json_schema_extra={"format": "uri"})


PageCursor = Annotated[str, Field(min_length=1, max_length=512)]


class UserPage(StrictModel):
    items: list[User]
    next_cursor: PageCursor | None


class InvitePage(StrictModel):
    items: list[Invite]
    next_cursor: PageCursor | None


class UserStatusPatch(StrictModel):
    status: Literal["active", "disabled"]


class PasswordResetIssue(StrictModel):
    verification_method: Literal["trusted_existing_contact", "in_person"]
    # 自由备注可能误填密码/链接；接受但不持久化、不日志记录，核验方式单独存储。
    note: str = Field(default="", max_length=200, repr=False)


class PasswordResetIssued(StrictModel):
    id: UUID
    expires_at: datetime
    reset_url: str = Field(repr=False, json_schema_extra={"format": "uri"})


class GenerationPolicy(StrictModel):
    text_enabled: bool
    image_enabled: bool
    ai_video_enabled: bool
    local_motion_enabled: bool
    updated_at: datetime


class GenerationPolicyPatch(StrictModel):
    model_config = {"json_schema_extra": {"minProperties": 1}}
    text_enabled: StrictBool = Field(default_factory=lambda: True)
    image_enabled: StrictBool = Field(default_factory=lambda: True)
    ai_video_enabled: StrictBool = Field(default_factory=lambda: True)
    local_motion_enabled: StrictBool = Field(default_factory=lambda: True)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个开关")
        return self
