"""素材与不可变版本的公开字段；不包含文件路径或供应商授权。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .problems import PageCursor, StrictModel

ArtifactKind = Literal["image", "video", "text"]
MediaType = Literal["image/png", "image/jpeg", "image/webp", "video/mp4", "text/plain"]
Engine = Literal["agnes-image-2.5-flash", "agnes-video-2.5-flash", "local-ffmpeg"]


class TextFields(StrictModel):
    content: str = Field(min_length=1, max_length=50000)
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def valid_text(self):
        self.content.encode("utf-8")
        if "conversation_id" in self.model_fields_set and self.conversation_id is None:
            raise ValueError("可选字段提供时不能为 null")
        return self


class TextArtifactCreate(TextFields):
    kind: Literal["text"]
    title: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def valid_title(self):
        self.title.encode("utf-8")
        return self


class TextVersionCreate(TextFields):
    base_version_id: UUID


class ReferenceConfirmationCreate(StrictModel):
    version_id: UUID
    purpose: Literal["video_first_frame"]


class ReferenceConfirmation(ReferenceConfirmationCreate):
    id: UUID
    conversation_id: UUID
    created_at: datetime


class Artifact(StrictModel):
    id: UUID
    kind: ArtifactKind
    title: str = Field(max_length=120)
    current_version_id: UUID
    version_count: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class ArtifactVersion(StrictModel):
    id: UUID
    artifact_id: UUID
    version_number: int = Field(ge=1)
    parent_version_id: UUID | None
    source_task_id: UUID | None
    execution_engine: Engine | None
    media_type: MediaType
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int | None = Field(ge=1)
    height: int | None = Field(ge=1)
    duration_seconds: float | None = Field(ge=0)
    fps: float | None = Field(gt=0)
    created_at: datetime
    content_url: str


class ArtifactCreated(StrictModel):
    artifact: Artifact
    version: ArtifactVersion


class ArtifactPage(StrictModel):
    items: list[Artifact]
    next_cursor: PageCursor | None


class ArtifactVersionPage(StrictModel):
    items: list[ArtifactVersion]
    next_cursor: PageCursor | None


class DeletionReceipt(StrictModel):
    artifact_id: UUID
    status: Literal["access_revoked"]
    purge_target_at: datetime


class Availability(StrictModel):
    available: bool
    reason: str | None


class TextCapability(StrictModel):
    model: Literal["gemini-3.8-flash-low"]
    availability: Availability


class ImageCapability(StrictModel):
    model: Literal["agnes-image-2.5-flash"]
    ratios: list[Literal["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "21:9"]]
    size_tiers: list[Literal["1K", "2K", "3K", "4K"]]
    reference_editing_enabled: bool
    availability: Availability


class VideoCapability(StrictModel):
    model: Literal["agnes-video-2.5-flash"]
    modes: list[Literal["keyframe", "text"]]
    ratios: list[Literal["9:16", "16:9"]]
    min_seconds: int = Field(ge=1)
    max_seconds: int = Field(ge=1)
    size_tiers: list[Literal["720P"]]
    availability: Availability


class SafetyLimits(StrictModel):
    max_message_chars: int = Field(ge=1)
    max_upload_bytes: int = Field(ge=1)
    max_image_pixels: int = Field(ge=1)
    max_message_attachments: int = Field(ge=1)
    max_image_references: int = Field(ge=1)
    max_page_size: int = Field(ge=1)


class Capabilities(StrictModel):
    text: TextCapability
    image: ImageCapability
    ai_video: VideoCapability
    local_motion: Availability
    business_quotas_enabled: Literal[False]
    limits: SafetyLimits
