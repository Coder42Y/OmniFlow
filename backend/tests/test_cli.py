import pytest

from omniflow.cli import main
from omniflow.db import MIGRATIONS


def test_explicit_cli_migration_and_check(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OMNIFLOW_DATA_DIR", str(tmp_path / "data"))
    assert main(["check"]) == 1
    assert not (tmp_path / "data").exists()
    assert main(["migrate"]) == 0
    assert main(["migrate"]) == 0
    assert main(["check"]) == 0
    output = capsys.readouterr()
    assert f"迁移完成，当前版本 {len(MIGRATIONS)}" in output.out
    assert "未验证真实提供方" in output.out
    assert str(tmp_path) not in output.err


def test_serve_is_loopback_only_without_access_log_or_proxy_trust(monkeypatch):
    calls = []
    monkeypatch.setattr("omniflow.http_server.LocalServer.run", lambda self: calls.append(self))
    assert main(["serve", "--port", "18765"]) == 0
    server = calls[0]
    config = server.config
    assert config.host == "127.0.0.1" and config.port == 18765
    assert not config.access_log and not config.proxy_headers and not config.server_header
    assert config.timeout_graceful_shutdown == 5
    assert not config.app.state.stream_shutdown.is_set()
    # 在 Uvicorn 等待现有连接退出之前关闭 SSE，不能只等 lifespan 结束。
    import signal

    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit and config.app.state.stream_shutdown.is_set()


@pytest.mark.parametrize("port", ["0", "80", "65536", "not-a-port"])
def test_invalid_port_never_starts_server(port):
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--port", port])
    assert exc.value.code == 2


def test_config_error_does_not_echo_environment_values(monkeypatch, capsys):
    monkeypatch.setenv("OMNIFLOW_TEXT_MODEL", "synthetic-sensitive-value")
    assert main(["check"]) == 1
    output = capsys.readouterr()
    assert "synthetic-sensitive-value" not in output.err
    assert "ValidationError" not in output.err
