from pathlib import Path

import pytest
from pydantic import ValidationError

from omniflow.config import Settings


def test_defaults_are_fail_closed(settings):
    assert settings.text_model == "gemini-3.8-flash-low"
    assert settings.image_model == "agnes-image-2.5-flash"
    assert settings.video_model == "agnes-video-2.5-flash"
    assert settings.provider_mode == "disabled"
    assert not settings.free_access_verified
    assert not settings.paid_fallback_enabled
    assert not settings.business_quotas_enabled
    assert not settings.docs_enabled
    assert settings.cookie_secure and settings.cookie_httponly
    assert settings.cookie_samesite == "lax" and settings.cookie_path == "/"
    assert settings.session_cookie_name == "__Host-omniflow_session"
    assert settings.csrf_cookie_name == "__Host-omniflow_csrf"
    assert settings.session_ttl_seconds == settings.invitation_ttl_seconds == 604800
    assert settings.password_reset_ttl_seconds == 1800
    assert (settings.password_min_length, settings.password_max_length) == (6, 128)
    assert settings.artifact_cleanup_seconds == 86400
    assert settings.allowed_hosts == ("localhost", "127.0.0.1")
    assert not settings.data_dir.exists()


@pytest.mark.parametrize(
    "values",
    [
        {"text_model": "auto"},
        {"text_model": "coding-model"},
        {"provider_mode": "real"},
        {"paid_fallback_enabled": True},
        {"free_access_verified": True},
        {"business_quotas_enabled": True},
        {"cookie_secure": False},
        {"cookie_httponly": False},
        {"allowed_hosts": ("*",)},
        {"allowed_hosts": ("*.example.test",)},
        {"allowed_hosts": ()},
        {"allowed_hosts": ("localhost:8765",)},
        {"public_origin": "*"},
        {"public_origin": "https://localhost/"},
        {"public_origin": "https://localhost/path"},
        {"public_origin": "https://user:synthetic@localhost"},
        {"public_origin": "https://localhost?token=synthetic"},
        {"public_origin": "https://localhost#fragment"},
        {"public_origin": "https://localhost:99999"},
        {"public_origin": "http://remote.example.test"},
        {"public_origin": "null"},
        {"public_origin": "https://local host"},
        {"development_origins": ("https://remote.example.test",)},
        {"environment": "production", "public_origin": "http://localhost"},
        {"environment": "production", "docs_enabled": True},
        {"environment": "production", "provider_mode": "mock"},
        {"environment": "production", "development_origins": ("http://localhost:5173",)},
        {"password_min_length": 100, "password_max_length": 90},
        {"password_min_length": 5},
        {"password_max_length": 5},
        {"sqlite_busy_timeout_ms": 0},
        {"min_free_disk_bytes": 0},
        {"unknown_setting": "synthetic"},
    ],
)
def test_reject_insecure_or_invalid_configuration(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_only_explicit_prefixed_environment_is_loaded(monkeypatch, tmp_path):
    # 本测试创建的虚构配置，不读取项目真实 .env。
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OMNIFLOW_TEXT_MODEL=unwanted-model\n")
    monkeypatch.setenv("TEXT_MODEL", "unwanted-model")
    monkeypatch.setenv("OMNIFLOW_DATA_DIR", str(tmp_path / "isolated"))
    monkeypatch.setenv("OMNIFLOW_PUBLIC_ORIGIN", "https://localhost:9443")
    monkeypatch.setenv("OMNIFLOW_ALLOWED_HOSTS", '["localhost"]')
    settings = Settings()
    assert settings.text_model == "gemini-3.8-flash-low"
    assert settings.data_dir == tmp_path / "isolated"
    assert settings.public_origin == "https://localhost:9443"
    assert settings.allowed_hosts == ("localhost",)
    assert not settings.data_dir.exists()


def test_settings_frozen_and_relative_paths_are_absolute(settings, monkeypatch, tmp_path):
    with pytest.raises(ValidationError):
        settings.cookie_secure = False
    monkeypatch.chdir(tmp_path)
    value = Settings(data_dir=Path("isolated"))
    assert value.data_dir == tmp_path / "isolated"
    assert Settings().data_dir == tmp_path / "backend" / "var" / "data"


@pytest.mark.parametrize(
    "name, safe, unsafe",
    [
        ("COOKIE_SECURE", "true", "false"),
        ("COOKIE_HTTPONLY", "1", "0"),
        ("PAID_FALLBACK_ENABLED", "false", "true"),
        ("FREE_ACCESS_VERIFIED", "0", "1"),
        ("BUSINESS_QUOTAS_ENABLED", "false", "true"),
    ],
)
def test_environment_boolean_literals_preserve_safety(monkeypatch, name, safe, unsafe):
    monkeypatch.setenv(f"OMNIFLOW_{name}", safe)
    Settings()
    monkeypatch.setenv(f"OMNIFLOW_{name}", unsafe)
    with pytest.raises(ValidationError):
        Settings()


def test_explicit_mock_and_loopback_development_origin():
    settings = Settings(
        provider_mode="mock",
        public_origin="http://127.0.0.1:8765",
        development_origins=("http://localhost:5173",),
    )
    assert settings.provider_mode == "mock"
    assert settings.origins == ("http://127.0.0.1:8765", "http://localhost:5173")
    assert settings.cookie_secure  # 不为本地开发降级 Cookie。
