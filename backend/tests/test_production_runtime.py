"""真实 Settings/CLI/API 装配＋bwrap 隔离可执行替身＋签名证据，不访问真实账号。"""

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest
from test_agy_adapter import evidence
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import accepted, create, read_run, snapshot
from test_media_adapters import HOST, URL, Exchange
from test_tasks import due, image_request

from omniflow.app import create_app
from omniflow.config import Settings
from omniflow.db import Database
from omniflow.problems import ProblemError
from omniflow.run_manager import RunManager
from omniflow.runtime import Presence, get_runtime
from omniflow.runtime_config import canonical, load_config
from omniflow.runtime_launcher import image_digest
from omniflow.safe_http import SafeHTTP
from omniflow.task_worker import TaskWorker

REAL_CONNECT = socket.socket.connect


def private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value.encode() if isinstance(value, str) else value)
    path.chmod(0o600)
    return path


@pytest.fixture(scope="session")
def image(tmp_path_factory):
    root = tmp_path_factory.mktemp("runtime-image")
    root.chmod(0o755)

    def copy(source, target=None):
        destination = root / (target or source).lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o755)

    copy("/usr/bin/python3")
    stdlib = subprocess.check_output(
        ["/usr/bin/python3", "-c", "import sysconfig; print(sysconfig.get_path('stdlib'))"],
        text=True,
    ).strip()
    shutil.copytree(
        stdlib,
        root / stdlib.lstrip("/"),
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__", "test", "tests", "site-packages"),
    )
    binaries = [Path("/usr/bin/python3"), *Path(stdlib, "lib-dynload").glob("*.so")]
    for binary in binaries:
        output = subprocess.run(
            ["ldd", str(binary)], capture_output=True, text=True, check=False
        ).stdout
        for source in re.findall(r"(/[^\s]+) \(", output):
            copy(source)
    copy(str(Path(__file__).with_name("runtime_fake_agy.py")), "/usr/local/bin/agy")
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() or os.access(path, os.X_OK) else 0o644)
    return root


@pytest.fixture
def runtime_files(tmp_path, image):
    signing = tmp_path / "signing.pem"
    public = tmp_path / "public.pem"
    subprocess.run(
        [
            "/usr/bin/openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(signing),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["/usr/bin/openssl", "pkey", "-in", str(signing), "-pubout", "-out", str(public)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    public.chmod(0o600)
    auth = private(tmp_path / "credentials/google.json", "synthetic-google-only")
    key = private(tmp_path / "credentials/agnes.key", "synthetic-agnes-only")
    config = {
        "version": 1,
        "rootfs": str(image),
        "rootfs_sha256": image_digest(image),
        "auth_files": [{"source": str(auth), "target": ".gemini/synthetic-auth.json"}],
        "agnes_key_file": str(key),
        "evidence_file": str(tmp_path / "proof.json"),
        "evidence_public_key": str(public),
        "download_hosts": [HOST],
        "google_hosts": ["oauth2.googleapis.com"],
    }
    path = private(tmp_path / "runtime.json", json.dumps(config))
    return path, signing


@pytest.fixture
def settings(tmp_path, runtime_files):
    return Settings(
        environment="production",
        provider_mode="real",
        data_dir=tmp_path / "data",
        runtime_config=runtime_files[0],
        min_free_disk_bytes=1,
    )


def sign(settings, signing, changes=None):
    config, fingerprint = load_config(settings)
    payload = {
        "config_sha256": fingerprint,
        "checks": {
            name: {"method": "account-holder-readonly", "record_sha256": "a" * 64}
            for name in ("billing", "overages", "isolation", "authorization", "protocol")
        },
        "evidence": {
            kind: {**asdict(evidence(kind)), **(changes or {})} for kind in config.enabled_kinds
        },
    }
    signature = subprocess.check_output(
        ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(signing)],
        input=canonical(payload),
    )
    private(
        config.evidence_file,
        canonical({"payload": payload, "signature": base64.b64encode(signature).decode()}),
    )
    return payload


@pytest.fixture
def configured(settings, runtime_files, database):
    sign(settings, runtime_files[1])
    return get_runtime(database)


def cli(settings, command, once=True):
    env = {
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "OMNIFLOW_ENVIRONMENT": "production",
        "OMNIFLOW_PROVIDER_MODE": "real",
        "OMNIFLOW_RUNTIME_CONFIG": str(settings.runtime_config),
        "OMNIFLOW_DATA_DIR": str(settings.data_dir),
        "OMNIFLOW_MIN_FREE_DISK_BYTES": "1",
        "SYNTHETIC_HOST_SECRET": "never-in-child",
    }
    return subprocess.run(
        [sys.executable, "-m", "omniflow.cli", command, *(["--once"] if once else [])],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_runtime_check_same_cli_reports_reasons_without_generation(settings, configured):
    result = cli(settings, "runtime-check", once=False)
    assert result.returncode == 1
    assert "PROVIDER_UNAVAILABLE" in result.stdout
    assert configured.fingerprint in result.stdout
    assert "synthetic-google-only" not in result.stdout + result.stderr
    with configured.worker("text"), configured.worker("media"):
        result = cli(settings, "runtime-check", once=False)
        assert result.returncode == 0
        assert result.stdout.count(": ready") == 4


def test_real_local_motion_cli_entry_not_test_provider(user, configured, settings, database):
    from test_artifacts import uploaded
    from test_tasks import accepted as accepted_task
    from test_tasks import get

    cid = create(user)
    version = uploaded(user, cid=cid)["version"]["id"]
    with configured.worker("media"):
        task = accepted_task(
            user,
            {
                "conversation_id": cid,
                "kind": "local_motion",
                "image_version_id": version,
                "motion_type": "dolly_in",
                "seconds": 1,
                "aspect_ratio": "16:9",
            },
        )
    for _ in range(3):
        result = cli(settings, "media-worker")
        assert result.returncode == 0, result.stderr
        due(database)
    current = get(user, task["id"])
    assert current["status"] == "completed", current
    assert current["execution_engine"] == "local-ffmpeg"
    assert not (configured.presence.directory / "media.json").exists()


def test_real_config_required_and_no_mock_injection(settings, database):
    with pytest.raises(ValueError):
        Settings(provider_mode="real")
    with pytest.raises(ValueError):
        TaskWorker(database, provider=object())
    assert create_app(settings).state.tasks.provider.__class__.__name__ == "RuntimeMedia"


def test_no_evidence_and_no_worker_do_not_advertise_ready(user, configured):
    configured.config.evidence_file.unlink()
    result = user[0].get("/api/v1/capabilities").json()
    assert result["text"]["availability"]["reason"] == "FREE_ACCESS_UNCONFIRMED"
    assert user[0].get("/api/v1/health/live").status_code == 200
    assert user[0].get("/api/v1/health/ready").status_code == 503


def test_signed_evidence_without_worker_is_not_model_ready(user, configured):
    value = user[0].get("/api/v1/capabilities").json()
    assert value["text"]["availability"] == {"available": False, "reason": "PROVIDER_UNAVAILABLE"}
    with configured.worker("text"), configured.worker("media"):
        assert user[0].get("/api/v1/health/ready").status_code == 200
        assert user[0].get("/api/v1/capabilities").json()["text"]["availability"]["available"]
    assert user[0].get("/api/v1/health/ready").status_code == 503


@pytest.mark.parametrize(
    "change,code",
    [
        ({"free": False}, "FREE_ACCESS_UNCONFIRMED"),
        ({"overages_disabled": False}, "FREE_ACCESS_UNCONFIRMED"),
        ({"expires_at": 0}, "FREE_ACCESS_UNCONFIRMED"),
        ({"checked_at": time.time() + 9999}, "FREE_ACCESS_UNCONFIRMED"),
        ({"isolation_verified": False}, "PROVIDER_UNAVAILABLE"),
        ({"limit_reached": True}, "PROVIDER_LIMIT_REACHED"),
    ],
)
def test_signed_but_invalid_conditions_denied(settings, runtime_files, configured, change, code):
    sign(settings, runtime_files[1], change)
    with pytest.raises(ProblemError) as caught:
        configured.gate.check("text")
    assert caught.value.code == code


def test_modified_signature_or_config_cannot_refresh(settings, configured):
    original = json.loads(configured.config.evidence_file.read_bytes())
    original["payload"]["evidence"]["text"]["expires_at"] += 100
    private(configured.config.evidence_file, canonical(original))
    assert configured.gate.evidence.evidence("text") is None
    config = json.loads(settings.runtime_config.read_bytes())
    config["reference_editing_enabled"] = True
    private(settings.runtime_config, canonical(config))
    other = get_runtime(Database(settings))
    assert other.fingerprint != configured.fingerprint
    assert other.gate.evidence.evidence("text") is None


def test_public_key_rotation_cannot_reuse_old_runtime_identity(configured, tmp_path):
    old = json.loads(configured.config.evidence_file.read_bytes())
    signing, public = tmp_path / "rotated-private.pem", tmp_path / "rotated-public.pem"
    subprocess.run(
        [
            "/usr/bin/openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(signing),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["/usr/bin/openssl", "pkey", "-in", str(signing), "-pubout", "-out", str(public)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    private(configured.config.evidence_public_key, public.read_bytes())
    signature = subprocess.check_output(
        ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(signing)],
        input=canonical(old["payload"]),
    )
    old["signature"] = base64.b64encode(signature).decode()
    private(configured.config.evidence_file, canonical(old))
    assert configured.gate.evidence.evidence("text") is None


def test_heartbeat_expires_and_exit_withdraws(configured):
    with configured.worker("media") as worker:
        configured.presence.check("media")
        worker.stop.set()
        worker.thread.join(3)
        data = json.loads(worker.path.read_bytes())
        data["updated_at"] -= Presence.ttl + 1
        private(worker.path, canonical(data))
        with pytest.raises(ProblemError):
            configured.presence.check("media")
    assert not worker.path.exists()


def test_real_cli_process_same_entry_isolated_and_exact_resume(user, configured, settings):
    a, b = create(user), create(user)
    first = accepted(user, a, "A随机代号")
    result = cli(settings, "run-manager")
    assert result.returncode == 0, result.stderr
    assert read_run(user, first["run"]["id"])["status"] == "completed"
    second = accepted(user, b, "B独立内容")
    assert cli(settings, "run-manager").returncode == 0
    assert read_run(user, second["run"]["id"])["status"] == "completed"
    assert "A随机代号" not in str(snapshot(user, b))
    third = accepted(user, a, "继续A")
    assert cli(settings, "run-manager").returncode == 0
    assert read_run(user, third["run"]["id"])["status"] == "completed"
    assert snapshot(user, a)["messages"][-1]["content"] == "A随机代号 / 继续A"
    assert not (configured.presence.directory / "text.json").exists()


def test_real_cli_bad_protocol_never_completes_or_replays(user, configured, settings):
    cid = create(user)
    action = accepted(user, cid, "bad-protocol")
    assert cli(settings, "run-manager").returncode == 0
    assert read_run(user, action["run"]["id"])["status"] == "needs_reconciliation"
    assert cli(settings, "run-manager").returncode == 0
    assert read_run(user, action["run"]["id"])["status"] == "needs_reconciliation"


def test_real_assembly_media_http_and_recreated_worker(user, configured, database):
    from test_artifacts import image_bytes
    from test_tasks import accepted as accepted_task
    from test_tasks import get

    exchange = Exchange()
    configured.media.agnes.http = SafeHTTP(exchange=exchange, resolver=lambda host: ("8.8.8.8",))
    # API 的单独 Database 实例也使用同一生产配置与证据，不注入 tests 提供方。
    cid = create(user)
    with configured.worker("media"):
        task = accepted_task(user, image_request(cid))
        exchange.add({"data": [{"url": URL}]})
        worker = TaskWorker(database)
        assert worker.execute_next()
        assert get(user, task["id"])["status"] == "running"
        due(database)
        assert TaskWorker(database).execute_next()
        due(database)
        exchange.add(image_bytes(), mime="image/png")
        assert TaskWorker(database).execute_next()
        assert get(user, task["id"])["status"] == "completed"
    assert len([r for r in exchange.requests if r.method == "POST"]) == 1
    assert exchange.requests[-1].headers.get("Authorization") is None


def test_direct_launch_protocol(configured, monkeypatch, tmp_path):
    from test_agy_adapter import consume, history, open_session

    from omniflow import process_transport

    popen = process_transport.subprocess.Popen
    with (tmp_path / "synthetic-stderr.log").open("wb") as diagnostics:

        def capture(*args, **kwargs):
            kwargs["stderr"] = diagnostics
            return popen(*args, **kwargs)

        monkeypatch.setattr(process_transport.subprocess, "Popen", capture)
        try:
            with configured.worker("text"):
                session = open_session(configured.text)
                try:
                    session.send(history("direct"), "discuss_only")
                    assert consume(session)[-1].__class__.__name__ == "TurnResult"
                finally:
                    assert session.close()
        except Exception:
            diagnostics.flush()
            raise AssertionError((tmp_path / "synthetic-stderr.log").read_text()) from None


def test_text_actions_use_real_task_service(user, configured, database):
    cid = create(user)
    action = accepted(user, cid, "请生成一张图片：合成蓝色方块")
    with configured.worker("text"), configured.worker("media"):
        manager = RunManager(database)
        try:
            assert manager.execute_next()
        finally:
            manager.close()
    result = read_run(user, action["run"]["id"])
    assert result["status"] == "completed", result
    assert len(result["task_ids"]) == 1
