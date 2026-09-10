"""只读取显式 OMNIFLOW_* 环境变量；绝不自动加载旧项目 .env。"""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OMNIFLOW_",
        env_file=None,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    data_dir: Path = Field(default_factory=lambda: Path.cwd() / "backend" / "var" / "data")
    public_origin: str = "https://localhost:8443"
    development_origins: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    docs_enabled: bool = False
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=1, le=60000)
    min_free_disk_bytes: int = Field(default=512 * 1024 * 1024, ge=1)

    # 这不是提供方就绪声明；real 模式仍须签名证据、独立 worker 和运行条件。
    text_model: Literal["gemini-3.8-flash-low"] = "gemini-3.8-flash-low"
    image_model: Literal["agnes-image-2.5-flash"] = "agnes-image-2.5-flash"
    video_model: Literal["agnes-video-2.5-flash"] = "agnes-video-2.5-flash"
    provider_mode: Literal["disabled", "mock", "real"] = "disabled"
    runtime_config: Path | None = None
    free_access_verified: Literal[False] = False
    paid_fallback_enabled: Literal[False] = False
    business_quotas_enabled: Literal[False] = False

    session_cookie_name: Literal["__Host-omniflow_session"] = "__Host-omniflow_session"
    csrf_cookie_name: Literal["__Host-omniflow_csrf"] = "__Host-omniflow_csrf"
    cookie_secure: Literal[True] = True
    cookie_httponly: Literal[True] = True
    cookie_samesite: Literal["lax"] = "lax"
    cookie_path: Literal["/"] = "/"

    username_pattern: Literal["^[a-z0-9_]{3,32}$"] = "^[a-z0-9_]{3,32}$"
    password_min_length: int = Field(default=6, ge=6, le=128)
    password_max_length: int = Field(default=128, ge=6, le=128)
    session_ttl_seconds: int = Field(default=604800, ge=1)
    invitation_ttl_seconds: int = Field(default=604800, ge=1)
    password_reset_ttl_seconds: int = Field(default=1800, ge=1)
    csrf_ttl_seconds: int = Field(default=3600, ge=1, le=86400)
    auth_rate_window_seconds: int = Field(default=300, ge=1, le=3600)
    auth_rate_ip_attempts: int = Field(default=60, ge=1, le=1000)
    auth_rate_username_attempts: int = Field(default=10, ge=1, le=100)
    artifact_cleanup_seconds: int = Field(default=86400, ge=1, le=604800)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    max_image_pixels: int = Field(default=25_000_000, ge=1, le=50_000_000)
    media_grant_ttl_seconds: int = Field(default=300, ge=1, le=900)
    media_grant_window_seconds: int = Field(default=1800, ge=1, le=3600)
    max_generated_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=500 * 1024 * 1024)
    task_queue_capacity: int = Field(default=1000, ge=1, le=100000)
    task_lease_seconds: int = Field(default=15, ge=3, le=300)
    task_poll_seconds: float = Field(default=1, ge=0.01, le=60)
    task_retry_seconds: int = Field(default=30, ge=1, le=3600)
    run_queue_capacity: int = Field(default=1000, ge=1, le=100000)
    run_lease_seconds: int = Field(default=15, ge=3, le=300)
    run_poll_seconds: float = Field(default=0.2, ge=0.01, le=1)
    run_max_seconds: int = Field(default=600, ge=1, le=3600)
    run_output_max_chars: int = Field(default=128000, ge=1, le=1000000)
    event_poll_seconds: float = Field(default=0.2, ge=0.01, le=1)
    event_retention_count: int = Field(default=10000, ge=1, le=1000000)

    @field_validator(
        "cookie_secure",
        "cookie_httponly",
        "free_access_verified",
        "paid_fallback_enabled",
        "business_quotas_enabled",
        mode="before",
    )
    @classmethod
    def boolean_environment_literals(cls, value: object) -> object:
        # 环境变量是字符串；先解析再由 Literal 校验，绝不放开安全常量。
        if isinstance(value, str):
            if value.lower() in ("true", "1"):
                return True
            if value.lower() in ("false", "0"):
                return False
        return value

    @field_validator("data_dir")
    @classmethod
    def absolute_data_dir(cls, value: Path) -> Path:
        # 不 resolve 符号链接，存储层须能识别并拒绝它。
        return value.absolute()

    @field_validator("allowed_hosts")
    @classmethod
    def exact_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not host or any(c in host for c in "*/:@?# \\\r\n") for host in value):
            raise ValueError("allowed_hosts 必须是精确主机名，不允许通配或端口")
        return value

    @model_validator(mode="after")
    def secure_defaults(self) -> "Settings":
        if self.provider_mode == "real" and (
            self.runtime_config is None or not self.runtime_config.is_absolute()
        ):
            raise ValueError("真实运行要求显式绝对路径的受保护配置")
        if self.password_min_length > self.password_max_length:
            raise ValueError("密码最小长度不能大于最大长度")
        if self.environment == "production" and (
            self.docs_enabled or self.development_origins or self.provider_mode == "mock"
        ):
            raise ValueError("生产模式禁止调试文档、开发来源和 mock")
        for origin in self.origins:
            parsed = urlsplit(origin)
            try:
                port = parsed.port
            except ValueError:
                raise ValueError("来源端口无效") from None
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
                or any(c.isspace() for c in origin)
                or "\\" in origin
                or (port is not None and port < 1)
                or origin != f"{parsed.scheme}://{parsed.netloc}"
            ):
                raise ValueError("来源必须为不带路径或凭据的精确 http(s) origin")
            local = parsed.hostname in ("localhost", "127.0.0.1")
            if parsed.scheme != "https" and (not local or self.environment == "production"):
                raise ValueError("仅非生产模式的回环来源允许 HTTP")
            if origin in self.development_origins and not local:
                raise ValueError("开发来源必须为回环地址")
        return self

    @property
    def origins(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.public_origin, *self.development_origins)))

    @property
    def database_path(self) -> Path:
        return self.data_dir / "omniflow.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"
