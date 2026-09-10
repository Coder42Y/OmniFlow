"""附加边界：迁移保留、恢复租约、授权代数、背压和提供方畸形返回。"""

import hashlib
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import headers, issue_reset, problem, schema
from test_auth import user as user
from test_conversations import accepted as accepted_run
from test_conversations import create
from test_tasks import accepted, action, due, finish, get, image_request, step, submit
from test_tasks import media as media

from omniflow import db
from omniflow.artifacts import ArtifactService
from omniflow.auth_security import fail, now
from omniflow.auth_service import AuthService
from omniflow.cli import main
from omniflow.media_provider import Accepted
from omniflow.media_storage import MediaInfo, MediaStorage
from omniflow.media_validation import inspect_output
from omniflow.problems import ProblemError
from omniflow.run_manager import RunManager
from omniflow.task_models import ImageTaskCreate
from omniflow.task_worker import TaskWorker
from omniflow.tasks import TaskService, ToolAuthority, task_row


def test_v5_upgrade_preserves_accounts_artifacts_and_history(settings, monkeypatch):
    database = db.Database(settings)
    migrations = db.MIGRATIONS
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", migrations[:5])
        assert database.migrate() == 5
    auth = AuthService(database)
    user = auth.create_admin("synthetic", "Synthetic Password 123!")
    aid, vid, timestamp = str(uuid4()), str(uuid4()), now()
    raw = b"synthetic v5 text"
    storage = MediaStorage(settings)
    with storage.stage(raw) as stage, database.transaction() as connection:
        connection.execute(
            "INSERT INTO artifacts(id,owner_id,kind,title,current_version_id,version_count,"
            "created_at,updated_at) VALUES (?,?,'text','synthetic',?,1,?,?)",
            (aid, str(user.id), vid, timestamp, timestamp),
        )
        storage.publish(stage, vid)
        ArtifactService.append_version(
            connection, aid, vid, MediaInfo("text/plain", len(raw), hashlib.sha256(raw).hexdigest())
        )
    with database.snapshot() as connection:
        old_version = dict(connection.execute("SELECT * FROM artifact_versions").fetchone())
        before_user = dict(
            connection.execute("SELECT * FROM users WHERE id=?", (str(user.id),)).fetchone()
        )
        history = [tuple(r) for r in connection.execute("SELECT * FROM schema_migrations")]
    assert database.migrate() == migrations[-1].version
    assert database.migrate() == migrations[-1].version
    with database.snapshot() as connection:
        assert (
            dict(connection.execute("SELECT * FROM users WHERE id=?", (str(user.id),)).fetchone())
            == before_user
        )
        assert [tuple(r) for r in connection.execute("SELECT * FROM schema_migrations")][
            :5
        ] == history
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert dict(connection.execute("SELECT * FROM artifact_versions").fetchone()) == old_version
    assert storage.path(vid).read_bytes() == raw


def test_local_media_worker_cli_once_only_deferred_default(
    user, media, database, monkeypatch, capsys
):
    task = accepted(user, image_request(create(user)))
    monkeypatch.setenv("OMNIFLOW_DATA_DIR", str(database.settings.data_dir))
    assert main(["media-worker", "--once"]) == 0
    assert get(user, task["id"])["status"] == "queued"
    assert get(user, task["id"])["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert media.records() == []
    assert "synthetic" not in capsys.readouterr().err


def test_recover_keeps_active_lease_and_original_result(user, media, database):
    task = accepted(user, image_request(create(user)))
    step(database, media)
    step(database, media)
    due(database)
    worker = TaskWorker(database, media)
    row = worker.claim()
    assert row["status"] == "saving"
    for _ in range(3):
        assert action(user, task["id"], "recover").status_code == 202
    with database.snapshot() as connection:
        after = task_row(connection, task["id"])
        assert after["lease_token"] == row["lease_token"]
        assert after["result_key"] == row["result_key"]
    assert not TaskWorker(database, media).execute_next()
    worker.save(row)
    assert get(user, task["id"])["status"] == "completed" and len(media.records()) == 1


@pytest.mark.parametrize("with_result", [False, True])
def test_reconciliation_with_clues_only_resumes_same_task(user, media, database, with_result):
    task = accepted(user, image_request(create(user)))
    step(database, media)
    if with_result:
        step(database, media)
    with database.transaction() as connection:
        original = dict(task_row(connection, task["id"]))
        connection.execute(
            "UPDATE tasks SET status='needs_reconciliation' WHERE id=?", (task["id"],)
        )
    assert get(user, task["id"])["can_recover"]
    assert action(user, task["id"], "recover").json()["status"] == (
        "saving" if with_result else "running"
    )
    assert finish(database, media, user, task["id"])["status"] == "completed"
    with database.snapshot() as connection:
        after = task_row(connection, task["id"])
        assert after["provider_task_id"] == original["provider_task_id"]
        assert after["output_version_id"] == original["output_version_id"]
    assert len(media.records()) == 1


def test_reset_after_claim_cannot_revive_old_task_authority(user, admin, media, database, browsers):
    task = accepted(user, image_request(create(user)))
    worker = TaskWorker(database, media)
    claimed = worker.claim()
    issued = issue_reset(admin, user[2])
    from test_auth import NEW_PASSWORD, csrf, post

    anonymous = browsers()
    response = post(
        anonymous,
        "/auth/password-resets/complete",
        csrf(anonymous),
        {"reset_token": issued, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 204, response.text
    worker.dispatch(claimed)
    with database.snapshot() as connection:
        assert task_row(connection, task["id"])["status"] == "canceled"
    assert media.records() == []


def test_policy_change_during_provider_gate_rechecked_in_final_transaction(
    user, admin, media, database
):
    task = accepted(user, image_request(create(user)))

    def gate(kind):
        response = admin[0].patch(
            "/api/v1/admin/generation-policy",
            json={"image_enabled": False},
            headers=headers(admin[1]),
        )
        assert response.status_code == 200

    media.gate = gate
    step(database, media)
    assert get(user, task["id"])["status"] == "queued" and media.records() == []


def test_queue_backpressure_is_concurrency_not_daily_quota(user, media, app, database):
    settings = database.settings.model_copy(update={"task_queue_capacity": 1})
    app.state.tasks = TaskService(db.Database(settings), media)
    cid = create(user)
    task = accepted(user, image_request(cid))
    blocked = submit(user, image_request(cid))
    problem(blocked, 429, "QUEUE_BACKPRESSURE")
    assert blocked.headers["Retry-After"] == "2"
    assert action(user, task["id"], "cancel").status_code == 200
    assert accepted(user, image_request(cid))["status"] == "queued"
    response = user[0].get("/api/v1/capabilities")
    schema("Capabilities", response.json())
    assert not response.json()["business_quotas_enabled"]


@pytest.mark.parametrize(
    "value", [None, True, {"task_id": "untrusted"}, Accepted(""), Accepted("\ud800")]
)
def test_unrecognized_acceptance_never_treated_as_safe_retry(
    user, media, database, monkeypatch, value
):
    task = accepted(user, image_request(create(user)))
    monkeypatch.setattr(media, "submit", lambda task, inputs: value)
    step(database, media)
    assert get(user, task["id"])["status"] == "submission_unknown"
    problem(action(user, task["id"], "recover"), 409, "RECONCILIATION_REQUIRED")


def test_gate_raw_secret_is_not_public(user, media):
    cid = create(user)

    def gate(kind):
        fail(503, "FREE_ACCESS_UNCONFIRMED", "synthetic-secret-raw-provider-url")

    media.gate = gate
    response = submit(user, image_request(cid))
    problem(response, 503, "FREE_ACCESS_UNCONFIRMED")
    assert "synthetic-secret" not in response.text
    response = user[0].get("/api/v1/capabilities")
    schema("Capabilities", response.json())
    assert "synthetic-secret" not in response.text


def test_discuss_only_tool_rejects_media(user, media, app, database):
    cid = create(user)
    run = accepted_run(user, cid, generation_permission="discuss_only")["run"]
    manager = RunManager(database)
    manager.claim()
    with pytest.raises(ProblemError) as exc:
        app.state.tasks.create_tool(
            ImageTaskCreate(**image_request(cid)),
            ToolAuthority(user[2], cid, run["id"], manager.token, "synthetic-action-1"),
        )
    assert exc.value.code == "FORBIDDEN"
    assert user[0].get("/api/v1/tasks").json()["items"] == []


@pytest.mark.parametrize("failure", ["missing", "timeout", "bad_metadata"])
def test_video_probe_failure_not_success(settings, monkeypatch, failure):
    raw = (Path(__file__).parent / "fixtures/synthetic.mp4").read_bytes()

    def probe(*args, **kwargs):
        if failure == "missing":
            raise FileNotFoundError
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ffprobe", 15)
        return subprocess.CompletedProcess(args[0], 0, b'{"streams":[]}')

    monkeypatch.setattr(subprocess, "run", probe)
    with pytest.raises(ProblemError):
        inspect_output(raw, "video/mp4", "ai_video", settings)


def test_generated_byte_limit_rejects_before_probe(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("不能在容量检查前启动解析")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ProblemError):
        inspect_output(
            b"x" * 20,
            "video/mp4",
            "ai_video",
            settings.model_copy(update={"max_generated_bytes": 10}),
        )
