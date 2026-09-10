"""agy 官方结构化结果契约：用户正文与内部动作必须分开。

工具参数仍由 ToolGateway 的任务模型及授权事务校验；这里的结构化结果
不是权限证明，也不能把 response 文本当作动作来源。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: Literal[
        "submit_image", "submit_video", "submit_local_motion", "read_task", "list_artifacts"
    ]
    arguments: dict


class StructuredReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(
        max_length=128000,
        description="仅面向用户的回复正文，不包含内部动作、工具参数、日志或思考。",
    )
    actions: list[Action] = Field(max_length=8)

    @field_validator("text")
    @classmethod
    def valid_text(cls, value):
        value.encode("utf-8")
        return value


def reply_schema():
    return StructuredReply.model_json_schema()
