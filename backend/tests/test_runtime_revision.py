"""09复审修复：仅视频的真实入口及凭据轮换，全部使用合成材料。"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import create
from test_media_adapters import Exchange, video_request
from test_production_runtime import (
    cli,
    configured,
    image,
    private,
    runtime_files,
    settings,
    sign,
)
from test_tasks import accepted as accepted_task
from test_tasks import due, get, image_request

from omniflow.db import Database
from omniflow.problems import ProblemError
from omniflow.provider_gate import MODELS
from omniflow.runtime import get_runtime
from omniflow.runtime_config import RuntimeConfig, RuntimeConfigurationError
from omniflow.safe_http import SafeHTTP
from omniflow.task_worker import TaskWorker

__all__ = ["configured", "image", "runtime_files", "settings"]


def video_only(settings):
    value = json.loads(settings.runtime_config.read_bytes())
    value["enabled_kinds"] = ["ai_video"]
    for key in ("rootfs", "rootfs_sha256", "auth_files", "google_hosts"):
        value.pop(key)
    private(settings.runtime_config, json.dumps(value))
    return value


def test_video_only_config_does_not_require_text_materials(settings):
    config = RuntimeConfig.model_validate(video_only(settings))
    assert config.enabled_kinds == ("ai_video",)


def test_credential_rotation_requires_new_evidence(user, configured, database):
    exchange = Exchange()
    configured.media.agnes.http = SafeHTTP(exchange=exchange, resolver=lambda host: ("8.8.8.8",))
    with configured.worker("media"):
        task = accepted_task(user, video_request(create(user)))
        private(configured.config.agnes_key_file, "another-synthetic-account")
        exchange.add({"model": MODELS["ai_video"], "video_id": "should-not-submit"})
        TaskWorker(database).execute_next()
        assert exchange.requests == []
        current = get(user, task["id"])
        assert current["status"] == "queued"
        assert current["error"]["code"] == "FREE_ACCESS_UNCONFIRMED"
        with pytest.raises(ProblemError):
            get_runtime(Database(database.settings)).gate.check("ai_video")


def test_video_ready_ignores_disabled_text(request, database, settings, runtime_files, monkeypatch):
    video_only(settings)
    sign(settings, runtime_files[1])
    # 视频无需生成本地运镜；未装配的文字、生图、本地运镜不可被本地管理员开关放行。
    monkeypatch.setattr("omniflow.ffmpeg_adapter.shutil.which", lambda name: None)
    runtime = get_runtime(database)
    user = request.getfixturevalue("user")
    with runtime.worker("media"):
        assert user[0].get("/api/v1/health/ready").status_code == 200
        capabilities = user[0].get("/api/v1/capabilities").json()
        assert capabilities["ai_video"]["availability"]["available"]
        for kind in ("text", "image", "local_motion"):
            availability = capabilities[kind]
            if kind != "local_motion":
                availability = availability["availability"]
            assert availability == {
                "available": False,
                "reason": "PROVIDER_UNAVAILABLE",
            }
        result = cli(settings, "runtime-check", once=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.count(": ready") == 1
    assert user[0].get("/api/v1/health/ready").status_code == 503
    assert cli(settings, "run-manager").returncode == 1
    assert not (runtime.presence.directory / "text.json").exists()


@pytest.mark.parametrize("kind", ["text", "image", "ai_video", "local_motion"])
def test_each_config_scope_requires_only_its_materials(settings, kind):
    value = json.loads(settings.runtime_config.read_bytes())
    value["enabled_kinds"] = [kind]
    if kind != "text":
        for key in ("rootfs", "rootfs_sha256", "auth_files", "google_hosts"):
            value.pop(key)
    if kind not in ("image", "ai_video"):
        for key in ("agnes_key_file", "download_hosts"):
            value.pop(key)
    config = RuntimeConfig.model_validate(value)
    assert config.enabled_kinds == (kind,)
    needed = "rootfs" if kind == "text" else "agnes_key_file"
    if kind != "local_motion":
        value.pop(needed)
        with pytest.raises(ValueError):
            RuntimeConfig.model_validate(value)


@pytest.mark.parametrize("scope", [[], ["unknown"], ["ai_video", "ai_video"]])
def test_invalid_scope_rejected(settings, scope):
    value = json.loads(settings.runtime_config.read_bytes())
    value["enabled_kinds"] = scope
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate(value)


def test_rotated_credentials_need_new_signature_and_restarted_workers(
    user, configured, database, settings, runtime_files
):
    with configured.worker("media"):
        task = accepted_task(user, video_request(create(user)))
    private(configured.config.agnes_key_file, "new-synthetic-agnes-key")
    restarted = get_runtime(Database(settings))
    assert restarted.fingerprint != configured.fingerprint
    with restarted.worker("media"):
        with pytest.raises(ProblemError) as caught:
            restarted.gate.check("ai_video")
        assert caught.value.code == "FREE_ACCESS_UNCONFIRMED"
    sign(settings, runtime_files[1])
    with configured.presence.worker("media"):
        with pytest.raises(ProblemError) as caught:
            restarted.gate.check("ai_video")
        assert caught.value.code == "PROVIDER_UNAVAILABLE"  # 旧心跳不能替新身份担保。
    exchange = Exchange()
    restarted.media.agnes.http = SafeHTTP(exchange=exchange, resolver=lambda host: ("8.8.8.8",))
    exchange.add({"model": MODELS["ai_video"], "video_id": "new-verified-account"})
    with restarted.worker("media"):
        assert TaskWorker(restarted.media.agnes.database).execute_next()
    assert get(user, task["id"])["status"] == "running"
    assert len(exchange.requests) == 1
    assert exchange.requests[0].headers["Authorization"] == "Bearer new-synthetic-agnes-key"
    assert configured.gate.evidence.evidence("ai_video") is None


def test_rotation_between_preflight_and_authorization_never_sends_new_key(
    user, configured, database, monkeypatch
):
    exchange = Exchange()
    configured.media.agnes.http = SafeHTTP(exchange=exchange, resolver=lambda host: ("8.8.8.8",))
    original = configured.media.agnes.request

    def rotate_then_request(*args, **kwargs):
        private(configured.config.agnes_key_file, "unverified-synthetic-key")
        return original(*args, **kwargs)

    monkeypatch.setattr(configured.media.agnes, "request", rotate_then_request)
    with configured.worker("media"):
        task = accepted_task(user, video_request(create(user)))
        assert TaskWorker(database).execute_next()
    assert exchange.requests == []
    # 本轮不更改已通过的 worker 异常状态机；无响应线索时保守阻止自动重发。
    assert get(user, task["id"])["status"] == "submission_unknown"


def test_no_second_credential_read_after_check(configured, monkeypatch):
    original = configured.credentials.read

    def read_then_rotate(provider):
        value = original(provider)
        private(configured.config.agnes_key_file, "unverified-synthetic-key")
        return value

    monkeypatch.setattr(configured.credentials, "read", read_then_rotate)
    assert configured.authorization() == "synthetic-agnes-only"
    with pytest.raises(RuntimeConfigurationError):
        configured.authorization()


def test_google_rotation_invalidates_text_but_not_live_agnes_evidence(configured, settings):
    private(configured.config.auth_files[0].source, "unverified-synthetic-google")
    assert configured.gate.evidence.evidence("text") is None
    assert configured.gate.evidence.evidence("ai_video") is not None
    with pytest.raises(RuntimeConfigurationError):
        configured.launcher.check()
    assert get_runtime(Database(settings)).fingerprint != configured.fingerprint


def test_google_mount_is_checked_snapshot_and_removed_on_exit(configured, monkeypatch):
    from test_agy_adapter import history, open_session

    with configured.worker("text"):
        # launcher最后一次checked后才改源；其余gate/check保持真实。
        from omniflow import runtime_launcher

        popen = runtime_launcher.IsolatedTransport
        descriptors = []

        def capture(argv, **kwargs):
            private(configured.config.auth_files[0].source, "unverified-synthetic-google")
            descriptors.extend(
                int(argv[i + 1]) for i, arg in enumerate(argv[:-1]) if arg == "--ro-bind-data"
            )
            assert tuple(descriptors) == kwargs["pass_fds"]
            for fd in descriptors:
                assert os.pread(fd, 65536, 0) == b"synthetic-google-only"
                with pytest.raises(PermissionError):
                    os.write(fd, b"cannot-change-sealed-snapshot")
            return popen(argv, **kwargs)

        monkeypatch.setattr(runtime_launcher, "IsolatedTransport", capture)
        session = open_session(configured.text)
        try:
            # 换源后每轮闸门拒绝，不能把旧进程当新账号使用。
            with pytest.raises(ProblemError):
                session.send(history("must-not-send"), "discuss_only")
            assert descriptors
            for fd in descriptors:
                with pytest.raises(OSError):
                    os.fstat(fd)  # 宿主的继承副本在spawn返回后即关闭。
        finally:
            assert session.close()


# 仅替换HTTP传输，在独立进程运行相同的 -m omniflow.cli；生产代码无测试导入。
MEDIA_BOOTSTRAP = """
import http.client, runpy, sys
from omniflow import safe_http
port = int(sys.argv[1])
def exchange(request):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(request.method, request.url, body=request.body, headers=request.headers)
        response = connection.getresponse()
        return safe_http.HTTPResponse(
            response.status, dict(response.getheaders()), [response.read()]
        )
    finally:
        connection.close()
safe_http.PinnedHTTPSExchange = lambda: exchange
original_http = safe_http.SafeHTTP
safe_http.SafeHTTP = lambda **kwargs: original_http(**kwargs, resolver=lambda host: ("8.8.8.8",))
sys.argv = ["omniflow.cli", "media-worker", "--once"]
runpy.run_module("omniflow.cli", run_name="__main__")
"""


def test_video_only_real_cli_submit_restart_download_and_scope_denial(
    request, database, settings, runtime_files
):
    from test_artifacts import confirmation, uploaded
    from test_media_adapters import URL

    video_only(settings)
    sign(settings, runtime_files[1])
    runtime = get_runtime(database)
    user = request.getfixturevalue("user")
    cid = create(user)
    version = uploaded(user, cid=cid)["version"]["id"]
    confirmed = confirmation(user, cid, version)
    assert confirmed.status_code == 201
    confirmation_id = confirmed.json()["id"]
    with runtime.worker("media"):
        task = accepted_task(
            user, video_request(cid, mode="keyframe", reference_confirmation_id=confirmation_id)
        )
        # 默认管理员开关本来就是全部开启，仍不能越过装配范围。
        response = user[0].post(
            "/api/v1/tasks",
            json=image_request(cid),
            headers={
                "Origin": settings.public_origin,
                "X-CSRF-Token": user[1],
                "Idempotency-Key": "disabled-image-attempt",
            },
        )
        assert response.status_code == 503
        assert response.json()["code"] == "PROVIDER_UNAVAILABLE"
    calls = []
    media = Path(__file__).with_name("fixtures").joinpath("synthetic.mp4").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(("POST", self.path, dict(self.headers), body))
            self.reply({"model": MODELS["ai_video"], "video_id": "video-only-synthetic"})

        def do_GET(self):
            calls.append(("GET", self.path, dict(self.headers), None))
            if self.path == URL:
                self.reply(media, "video/mp4")
            else:
                self.reply(
                    {
                        "model": MODELS["ai_video"],
                        "video_id": "video-only-synthetic",
                        "status": "completed",
                        "metadata": {"url": URL},
                    }
                )

        def reply(self, value, mime="application/json"):
            body = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header("content-type", mime)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "OMNIFLOW_ENVIRONMENT": "production",
        "OMNIFLOW_PROVIDER_MODE": "real",
        "OMNIFLOW_RUNTIME_CONFIG": str(settings.runtime_config),
        "OMNIFLOW_DATA_DIR": str(settings.data_dir),
        "OMNIFLOW_MIN_FREE_DISK_BYTES": "1",
    }
    try:
        for expected in ("running", "saving", "completed"):
            result = subprocess.run(
                [sys.executable, "-c", MEDIA_BOOTSTRAP, str(server.server_port)],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "synthetic-agnes-only" not in result.stdout + result.stderr
            assert get(user, task["id"])["status"] == expected
            assert not (runtime.presence.directory / "media.json").exists()
            due(database)
            if expected == "running":
                # 已受理任务重启后即使证据过期仍续查/保存；新增视频必须拒绝。
                sign(settings, runtime_files[1], {"expires_at": 0})
                with runtime.worker("media"):
                    assert user[0].get("/api/v1/health/ready").status_code == 503
                    capability = user[0].get("/api/v1/capabilities").json()["ai_video"]
                    assert capability["availability"]["reason"] == "FREE_ACCESS_UNCONFIRMED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    assert not thread.is_alive()
    assert [call[0] for call in calls] == ["POST", "GET", "GET"]
    assert calls[0][3]["model"] == MODELS["ai_video"]
    assert "/media-grants/" in calls[0][3]["first_frame"]
    assert calls[-1][2].get("Authorization") is None
    current = get(user, task["id"])
    assert current["execution_engine"] == MODELS["ai_video"]
    assert len(current["output_version_ids"]) == 1
