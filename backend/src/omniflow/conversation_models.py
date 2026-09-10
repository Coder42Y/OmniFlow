"""对话公开字段白名单；内部 CLI 映射、租约和原始输出不进入响应。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .artifact_models import ArtifactVersion
from .problems import PageCursor, StrictModel
from .task_models import Task

Title = Annotated[str, Field(min_length=1, max_length=120)]
EventId = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]*)$")]
RunStatus = Literal[
    "queued",
    "running",
    "stopping",
    "completed",
    "failed",
    "canceled",
    "interrupted",
    "needs_reconciliation",
]


class ConversationCreate(StrictModel):
    title: Title = "新对话"

    @field_validator("title")
    @classmethod
    def valid_text(cls, value):
        value.encode("utf-8")  # 拒绝孤立代理字符，不能送到 SQLite 后才报 500。
        if not value.strip():
            raise ValueError("标题不可为空白")
        return value


class ConversationPatch(ConversationCreate):
    title: Title


class MessageCreate(StrictModel):
    client_message_id: UUID
    content: str = Field(max_length=16000)
    attachment_version_ids: list[UUID] = Field(
        default_factory=list, max_length=8, json_schema_extra={"uniqueItems": True}
    )
    selected_version_id: UUID | None = None
    reference_confirmation_id: UUID | None = None
    generation_permission: Literal["requested_only", "discuss_only"] = "requested_only"

    @model_validator(mode="after")
    def valid_content(self):
        self.content.encode("utf-8")
        if not self.content.strip() and not self.attachment_version_ids:
            raise ValueError("内容与附件不可同时为空")
        if len(set(self.attachment_version_ids)) != len(self.attachment_version_ids):
            raise ValueError("附件不能重复")
        for name in ("selected_version_id", "reference_confirmation_id"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError("可选字段提供时不能为 null")
        return self


class Conversation(StrictModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    last_event_id: EventId
    active_run_id: UUID | None


class Message(StrictModel):
    id: UUID
    conversation_id: UUID
    seq: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str
    status: Literal["queued", "streaming", "completed", "interrupted", "failed"]
    run_id: UUID | None
    client_message_id: UUID | None
    attachment_version_ids: list[UUID]
    selected_version_id: UUID | None
    artifact_version_ids: list[UUID] = Field(json_schema_extra={"uniqueItems": True})
    created_at: datetime
    updated_at: datetime


class ResourceError(StrictModel):
    code: str
    message: str
    retryable: bool


class Run(StrictModel):
    id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    status: RunStatus
    task_ids: list[UUID]
    error: ResourceError | None
    created_at: datetime
    updated_at: datetime


class MessageAccepted(StrictModel):
    message: Message
    run: Run


class ConversationPage(StrictModel):
    items: list[Conversation]
    next_cursor: PageCursor | None


class MessagePage(StrictModel):
    items: list[Message]
    next_cursor: PageCursor | None


class RunPage(StrictModel):
    items: list[Run]
    next_cursor: PageCursor | None


class ConversationSnapshot(StrictModel):
    conversation: Conversation
    messages: list[Message]
    messages_next_cursor: PageCursor | None
    runs: list[Run]
    tasks: list[Task]
    artifact_versions: list[ArtifactVersion]
    last_event_id: EventId


class MessageDelta(StrictModel):
    message_id: UUID
    run_id: UUID
    chunk_index: int = Field(ge=1)
    delta: str


EVENT_DATA = {
    "message.created": Message,
    "message.updated": Message,
    "message.delta": MessageDelta,
    "run.updated": Run,
    "conversation.updated": Conversation,
    "artifact.ready": ArtifactVersion,
    "task.updated": Task,
}
