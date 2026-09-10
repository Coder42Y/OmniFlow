"""可注入媒体边界；没有网络传输或假冒真实模型的默认实现。

阶段 05 的适配器负责目标地址/重定向/响应容量限制、免费权益的真实核验，
以及有界调用。check 是本地已核验状态的快照读取，不得在数据库写锁内联网。
submit 抛普通异常表示可能已提交；只有明确未受理才允许抛 SubmissionRejected。
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from .auth_security import fail
from .problems import ProblemError


@dataclass(frozen=True)
class Accepted:
    task_id: str


@dataclass(frozen=True)
class Observation:
    status: Literal["running", "completed", "failed"]
    result_key: str | None = None


@dataclass(frozen=True)
class Download:
    content: bytes
    media_type: str


class SubmissionRejected(Exception):
    """适配器明确证明未受理；异常正文不公开。"""


class MediaProvider(Protocol):
    reference_editing_enabled: bool

    def check(self, kind: str) -> None:
        """检查本地可信权益、限额、提供方可用性；拒绝时抛固定 ProblemError。"""
        ...

    def submit(self, task: dict, inputs: tuple[str, ...]) -> Accepted:
        """参数为后端构建的任务/精确版本ID；不接受模型 URL 或主机路径。"""
        ...

    def poll(self, task_id: str) -> Observation: ...

    def download(self, result_key: str, max_bytes: int) -> Download: ...


class DisabledMediaProvider:
    reference_editing_enabled = False

    def check(self, kind):
        fail(503, "PROVIDER_UNAVAILABLE", "媒体提供方未接入，不能新增生成")

    def submit(self, task, inputs):
        raise SubmissionRejected

    def poll(self, task_id):
        fail(503, "PROVIDER_UNAVAILABLE", "媒体提供方未接入，保留原任务等待续查")

    def download(self, result_key, max_bytes):
        fail(503, "PROVIDER_UNAVAILABLE", "媒体提供方未接入，保留原结果等待保存")


def safe_code(exc):
    allowed = {
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_LIMIT_REACHED",
        "FREE_ACCESS_UNCONFIRMED",
        "GENERATION_PAUSED",
        "STORAGE_UNAVAILABLE",
        "ACCOUNT_DISABLED",
    }
    return (
        exc.code
        if isinstance(exc, ProblemError) and exc.code in allowed
        else "PROVIDER_UNAVAILABLE"
    )


def check_generation(provider, kind):
    try:
        provider.check(kind)
    except Exception as exc:
        # gate 也只接受稳定类别；不得把提供方错误 detail/URL 透传到 HTTP。
        fail(503, safe_code(exc), "提供方条件未满足，已暂停新增生成")


def provider_for(database, provider=None):
    if provider is not None:
        if database.settings.environment == "production" or (
            database.settings.environment != "test" and database.settings.provider_mode != "mock"
        ):
            raise ValueError("本阶段注入提供方仅允许测试或显式 mock 开发环境")
        return provider
    if database.settings.provider_mode == "real":
        from .runtime import get_runtime

        return get_runtime(database).media
    return DisabledMediaProvider()
