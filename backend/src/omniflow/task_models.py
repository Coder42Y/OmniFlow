"""三类任务契约；请求不接受运行身份、模型、路径或提供方地址。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_serializer, model_validator

from .problems import PageCursor, StrictModel

TaskStatus = Literal[
    "queued",
    "submitting",
    "running",
    "saving",
    "completed",
    "failed",
    "canceled",
    "submission_unknown",
    "needs_reconciliation",
]
TaskKind = Literal["image", "ai_video", "local_motion"]
Engine = Literal["agnes-image-2.5-flash", "agnes-video-2.5-flash", "local-ffmpeg"]
VideoRatio = Literal["9:16", "16:9"]


class TaskBase(StrictModel):
    conversation_id: UUID
    target_artifact_id: UUID | None = None
    base_version_id: UUID | None = None
    regenerate_from_task_id: UUID | None = None

    @model_serializer(mode="wrap")
    def serialize_request(self, handler):
        # Task 内嵌请求不得由响应序列化器补出契约禁止的显式 null。
        return {key: value for key, value in handler(self).items() if value is not None}

    @model_validator(mode="after")
    def paired_target(self):
        if bool(self.target_artifact_id) != bool(self.base_version_id):
            raise ValueError("目标作品和基础版本必须成对提供")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError("可选字段提供时不能为 null")
        return self


class PromptTask(TaskBase):
    prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("prompt")
    @classmethod
    def valid_prompt(cls, value):
        value.encode("utf-8")
        if not value.strip():
            raise ValueError("提示不可为空白")
        return value


class ImageTaskCreate(PromptTask):
    kind: Literal["image"]
    aspect_ratio: Literal["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "21:9"]
    size_tier: Literal["1K", "2K", "3K", "4K"]
    reference_version_ids: list[UUID] = Field(
        default_factory=list, max_length=5, json_schema_extra={"uniqueItems": True}
    )

    @field_validator("reference_version_ids")
    @classmethod
    def unique_references(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("参考版本不可重复")
        return value


class VideoTaskCreate(PromptTask):
    kind: Literal["ai_video"]
    mode: Literal["keyframe", "text"]
    reference_confirmation_id: UUID | None = None
    seconds: int = Field(ge=4, le=12, strict=True)
    size_tier: Literal["720P"]
    aspect_ratio: VideoRatio

    @model_validator(mode="after")
    def confirmation_mode(self):
        if (self.mode == "keyframe") != bool(self.reference_confirmation_id):
            raise ValueError("首帧模式必须提供确认，文字模式不得提供确认")
        return self


class LocalMotionTaskCreate(TaskBase):
    kind: Literal["local_motion"]
    image_version_id: UUID
    motion_type: Literal["dolly_in", "pan_left", "pan_right", "dynamic_float"]
    seconds: float = Field(ge=1, le=30, strict=True, allow_inf_nan=False)
    aspect_ratio: VideoRatio


TaskCreate = Annotated[
    ImageTaskCreate | VideoTaskCreate | LocalMotionTaskCreate, Field(discriminator="kind")
]


class TaskError(StrictModel):
    code: str
    message: str
    retryable: bool


class Task(StrictModel):
    id: UUID
    conversation_id: UUID
    run_id: UUID | None
    kind: TaskKind
    status: TaskStatus
    requested_parameters: TaskCreate
    execution_engine: Engine
    output_version_ids: list[UUID]
    can_cancel: bool
    can_recover: bool
    error: TaskError | None
    created_at: datetime
    updated_at: datetime


class TaskPage(StrictModel):
    items: list[Task]
    next_cursor: PageCursor | None


class OperationalTask(StrictModel):
    id: UUID
    user_id: UUID
    kind: TaskKind
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    error_code: str | None


class OperationalTaskPage(StrictModel):
    items: list[OperationalTask]
    next_cursor: PageCursor | None
