"""真实装配的隔离、受限出口与配置真实性专项（只有合成数据）。"""

import json
import socket
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_agy_adapter import consume, history, open_session
from test_production_runtime import (
    REAL_CONNECT,
    configured,
    image,
    private,
    runtime_files,
    settings,
)

from omniflow.agy_adapter import launch_plan
from omniflow.problems import ProblemError
from omniflow.runtime_config import RuntimeConfig, RuntimeConfigurationError, private_read
from omniflow.runtime_egress import EgressBroker
from omniflow.runtime_launcher import AgyLauncher, image_digest

# 将导入 fixture 明确保留为本文件的测试依赖。
__all__ = ["configured", "image", "runtime_files", "settings"]


def test_real_namespace_hides_existing_host_files_and_other_conversations(configured, tmp_path):
    secret = private(tmp_path / "host-private-canary", "synthetic-host-only")
    a = launch_plan(configured.text.root, str(uuid4()), str(uuid4()), None)
    other = private(a.cwd / "another-conversation", "synthetic-other-conversation-only")
    with configured.worker("text"):
        session = open_session(configured.text)
        try:
            for forbidden in (secret, other):
                session.send(history("inspect:" + str(forbidden)), "discuss_only")
                result = consume(session)
                assert result[0].text.endswith("隔离检查通过")
        finally:
            assert session.close()
    assert secret.read_text() == "synthetic-host-only"
    assert other.read_text() == "synthetic-other-conversation-only"


def test_same_launcher_network_namespace_and_unix_https_proxy(configured, monkeypatch):
    from omniflow import runtime_egress

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(5)
    port = server.getsockname()[1]
    received = []

    def echo():
        try:
            stream, _ = server.accept()
            with stream:
                stream.settimeout(3)
                received.append(stream.recv(64))
                stream.sendall(b"SYNTHETIC-PONG")
        except OSError:
            return

    thread = threading.Thread(target=echo)
    thread.start()

    def synthetic_connection(address, timeout):
        assert address == ("8.8.8.8", 443)  # 生产仍只连接已经校验的公共 IP。
        stream = socket.socket()
        stream.settimeout(timeout)
        REAL_CONNECT(stream, ("127.0.0.1", port))
        return stream

    monkeypatch.setattr(runtime_egress, "resolve", lambda host: ("8.8.8.8",))
    monkeypatch.setattr(runtime_egress.socket, "create_connection", synthetic_connection)
    try:
        with configured.worker("text"):
            session = open_session(configured.text)
            try:
                session.send(history("proxy-check"), "discuss_only")
                assert consume(session)[0].text == "出口检查通过"
            finally:
                assert session.close()
        assert received == [b"SYNTHETIC-PING"]
    finally:
        server.close()
        thread.join(6)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    "frame,addresses",
    [
        (b"CONNECT other.example:443 HTTP/1.1\r\n\r\n", ("8.8.8.8",)),
        (b"CONNECT oauth2.googleapis.com:80 HTTP/1.1\r\n\r\n", ("8.8.8.8",)),
        (b"GET https://oauth2.googleapis.com HTTP/1.1\r\n\r\n", ("8.8.8.8",)),
        (b"CONNECT oauth2.googleapis.com:443 HTTP/1.1\r\n\r\n", ("127.0.0.1",)),
        (b"CONNECT oauth2.googleapis.com:443 HTTP/1.1\r\n\r\n", ("8.8.8.8", "10.0.0.1")),
        (b"CONNECT oauth2.googleapis.com:443 HTTP/1.1\r\n\r\n", ("::1",)),
        (b"CONNECT oauth2.googleapis.com:443 HTTP/1.1\r\n\r\n", ("64:ff9b::0808:0808",)),
    ],
)
def test_egress_rejects_unapproved_host_ports_and_private_dns(
    tmp_path, monkeypatch, frame, addresses
):
    from omniflow import runtime_egress

    monkeypatch.setattr(runtime_egress, "resolve", lambda host: addresses)
    calls = []
    monkeypatch.setattr(runtime_egress.socket, "create_connection", lambda *a, **k: calls.append(a))
    temporary = tempfile.TemporaryDirectory(prefix="omniflow-test-egress-")
    broker = EgressBroker(Path(temporary.name) / "socket", ("oauth2.googleapis.com",))
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
            stream.settimeout(3)
            REAL_CONNECT(stream, str(broker.path))
            stream.sendall(frame)
            assert stream.recv(128) == b""
        assert calls == []
    finally:
        broker.close()
        temporary.cleanup()
    assert not broker.path.exists()
    assert not broker.thread.is_alive()


@pytest.mark.parametrize("mutation", ["public", "symlink", "directory"])
def test_config_and_credentials_private_regular_files_only(tmp_path, mutation):
    path = private(tmp_path / "secret", "synthetic")
    if mutation == "public":
        path.chmod(0o644)
    elif mutation == "symlink":
        path = tmp_path / "link"
        path.symlink_to(tmp_path / "secret")
    else:
        path.unlink()
        path.mkdir()
    with pytest.raises((RuntimeConfigurationError, OSError)):
        private_read(path)


@pytest.mark.parametrize(
    "target",
    [
        "../.ssh/key",
        ".gemini/../other",
        ".gemini/settings.json",
        ".gemini/antigravity-cli/settings.json",
        ".gemini/",
        ".gemini/mcp_config.json",
    ],
)
def test_authorization_cannot_mount_directory_or_override_policy(configured, target):
    value = configured.config.model_dump(mode="json")
    value["auth_files"][0]["target"] = target
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate(value)


def test_image_digest_cannot_confuse_file_data_with_next_file_metadata(tmp_path):
    root = tmp_path / "digest-image"
    root.mkdir(mode=0o700)
    (root / "a").write_bytes(b"")
    (root / "b").write_bytes(b"content")
    (root / "a").chmod(0o644)
    (root / "b").chmod(0o644)
    before = image_digest(root)
    (root / "b").unlink()
    (root / "a").write_bytes(b"b\x00" + str(0o644).encode() + b"\x00content")
    assert image_digest(root) != before


def test_launcher_rejects_wrong_image_digest_before_creating_process(configured):
    launcher = AgyLauncher(configured.config.model_copy(update={"rootfs_sha256": "f" * 64}))
    with pytest.raises(RuntimeConfigurationError):
        launcher.check()


def test_dead_worker_pid_not_accepted(configured):
    with configured.worker("media") as worker:
        worker.stop.set()
        worker.thread.join(3)
        data = json.loads(worker.path.read_bytes())
        data["pid"] = 2147483647
        private(worker.path, json.dumps(data))
        with pytest.raises(ProblemError):
            configured.presence.check("media")


def test_bubblewrap_plan_not_mutable_home_policy(configured):
    plan = launch_plan(configured.text.root, str(uuid4()), str(uuid4()), None)
    with configured.worker("text"):
        transport = configured.launcher(plan)
        try:
            assert transport.read(2)["event"] == "init"
            argv = transport.process.args
            assert "--unshare-all" in argv and "--share-net" not in argv
            assert "--die-with-parent" in argv and "--new-session" in argv
            assert "--dangerously-skip-permissions" not in argv
            assert "--continue" not in argv
            assert "synthetic-google-only" not in str(argv)
        finally:
            assert transport.close()
    policy = plan.cwd.parent / "home/.gemini/antigravity-cli/settings.json"
    private(policy, "{}")
    with pytest.raises(RuntimeConfigurationError):
        configured.launcher(replace(plan))
