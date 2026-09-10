"""故障注入：持久提供方账本、硬退出、续查/下载恢复与租约交错。"""

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from test_artifacts import uploaded
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import headers, problem, schema
from test_auth import user as user
from test_conversations import accepted as accepted_run
from test_conversations import context, create, send, snapshot
from test_tasks import accepted, action, due, finish, get, image_request, step, submit
from test_tasks import media as media

from omniflow.auth_security import fail
from omniflow.db import Database
from omniflow.problems import ProblemError
from omniflow.run_manager import RunManager
from omniflow.task_models import ImageTaskCreate, VideoTaskCreate
from omniflow.task_worker import LeaseLost, TaskWorker
from omniflow.tasks import ToolAuthority, task_row


def expire(database, tid):
    with database.transaction() as connection:
        connection.execute(
            "UPDATE tasks SET lease_until=? WHERE id=? AND lease_token IS NOT NULL",
            ("2000-01-01T00:00:00.000000Z", tid),
        )


def process(database, media, mode="ok", expected=0):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("media_worker_process.py")),
            str(database.settings.data_dir),
            str(media.path),
            mode,
        ],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONWARNINGS": "error"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == expected, result.stderr
    assert "synthetic-secret" not in result.stdout + result.stderr
    return result


@pytest.mark.parametrize(
    "fault", ["lost_response", "crash_after_accept", "hard_crash_after_accept"]
)
def test_accepted_but_id_lost_never_posts_again(user, database, media, fault):
    cid, key = create(user), str(uuid4())
    task = accepted(user, image_request(cid), key)
    media.mode = fault
    if fault == "hard_crash_after_accept":
        process(database, media, fault, expected=77)
    elif fault == "crash_after_accept":
        with pytest.raises(SystemExit):
            step(database, media)
    else:
        step(database, media)
    assert len(media.records()) == 1
    expire(database, task["id"])
    media.mode = "ok"
    for _ in range(3):
        due(database)
        process(database, media)
    result = get(user, task["id"])
    assert result["status"] == "submission_unknown" and not result["can_recover"]
    problem(action(user, task["id"], "recover"), 409, "RECONCILIATION_REQUIRED")
    problem(action(user, task["id"], "cancel"), 409, "TASK_NOT_CANCELABLE")
    assert submit(user, image_request(cid), key).json() == task
    assert len(media.records()) == 1
    with database.snapshot() as connection:
        assert (
            connection.execute(
                "SELECT closed_at FROM task_media_scopes WHERE task_id=?", (task["id"],)
            ).fetchone()[0]
            is None
        )
    assert "synthetic-secret" not in json.dumps(snapshot(user, cid))


def test_crash_before_dispatch_marker_reclaims_without_duplicate(user, database, media):
    task = accepted(user, image_request(create(user)))
    first, second = TaskWorker(database, media), TaskWorker(database, media)
    old = first.claim()
    assert old["status"] == "submitting" and old["dispatched_at"] is None
    assert second.claim() is None
    problem(action(user, task["id"], "cancel"), 409, "TASK_NOT_CANCELABLE")
    expire(database, task["id"])
    new = second.claim()
    assert new["lease_token"] != old["lease_token"]
    with pytest.raises(LeaseLost):
        first.dispatch(old)
    second.dispatch(new)
    assert finish(database, media, user, task["id"])["status"] == "completed"
    assert len(media.records()) == 1


def test_all_steps_in_independent_processes(user, database, media):
    task = accepted(user, image_request(create(user)))
    for status in ("running", "saving", "completed"):
        due(database)
        process(database, media)
        assert get(user, task["id"])["status"] == status
    assert len(media.records()) == 1


@pytest.mark.parametrize("mode", ["poll_error", "download_error", "bad_media"])
def test_recovery_only_requeries_or_redownloads_original(user, database, media, mode):
    task = accepted(user, image_request(create(user)))
    step(database, media)
    provider_id = media.records()[0][0]
    if mode != "poll_error":
        step(database, media)
    media.mode = mode
    step(database, media)
    result = get(user, task["id"])
    assert result["status"] == ("running" if mode == "poll_error" else "saving")
    assert result["can_recover"] and result["error"] and result["output_version_ids"] == []
    assert action(user, task["id"], "recover").status_code == 202
    media.mode = "ok"
    result = finish(database, media, user, task["id"])
    assert result["status"] == "completed" and len(media.records()) == 1
    assert set(media.polls) <= {provider_id}
    assert not get(user, task["id"])["can_recover"]
    problem(action(user, task["id"], "recover"), 409, "RECONCILIATION_REQUIRED")


@pytest.mark.parametrize("hard_exit", [False, True])
def test_publish_before_commit_crash_reuses_stable_file(
    user, database, media, monkeypatch, hard_exit
):
    task = accepted(user, image_request(create(user)))
    step(database, media)
    step(database, media)
    if hard_exit:
        due(database)
        process(database, media, "hard_crash_after_publish", expected=78)
    else:
        worker = TaskWorker(database, media)
        original = worker.storage.publish

        def publish(stage, vid):
            original(stage, vid)
            raise SystemExit(78)

        monkeypatch.setattr(worker.storage, "publish", publish)
        due(database)
        with pytest.raises(SystemExit):
            worker.execute_next()
    with database.snapshot() as connection:
        row = task_row(connection, task["id"])
        vid = row["output_version_id"]
        assert row["status"] == "saving"
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE source_task_id=?", (task["id"],)
            ).fetchone()[0]
            == 0
        )
    path = database.settings.media_dir / (vid + ".blob")
    assert path.exists()
    original_inode = path.stat().st_ino
    expire(database, task["id"])
    media.mode = "download_error"  # 恢复已落盘文件不需要再次下载，更不应生成。
    result = finish(database, media, user, task["id"])
    assert result["status"] == "completed" and result["output_version_ids"] == [vid]
    assert path.stat().st_ino == original_inode
    assert len(media.records()) == 1
    with database.snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE source_task_id=?", (task["id"],)
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_provider_id_commit_failure_becomes_unknown_not_safe_retry(user, database, media):
    task = accepted(user, image_request(create(user)))
    with database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER synthetic_fail_id BEFORE UPDATE OF provider_task_id ON tasks "
            "WHEN NEW.provider_task_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'fault'); END"
        )
    step(database, media)
    assert get(user, task["id"])["status"] == "submission_unknown"
    assert len(media.records()) == 1
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER synthetic_fail_id")
    assert not TaskWorker(database, media).execute_next()


@pytest.mark.parametrize("mode,status", [("reject", "failed"), ("provider_failed", "failed")])
def test_definitive_failure_closes_scope(user, database, media, mode, status):
    task = accepted(user, image_request(create(user)))
    media.mode = mode
    result = finish(database, media, user, task["id"])
    assert result["status"] == status
    with database.snapshot() as connection:
        assert connection.execute(
            "SELECT closed_at FROM task_media_scopes WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
    assert "synthetic-secret" not in json.dumps(result)


def test_cancel_claim_race_exactly_one_winner(user, database, media, app):
    for _ in range(5):
        task = accepted(user, image_request(create(user)))
        barrier = threading.Barrier(2)
        worker = TaskWorker(database, media)

        def cancel(barrier=barrier, task=task):
            barrier.wait()
            try:
                return app.state.tasks.cancel(task["id"], context(user[0]))["status"]
            except ProblemError as exc:
                return exc.code

        def claim(barrier=barrier, worker=worker):
            barrier.wait()
            return worker.claim()

        with ThreadPoolExecutor(max_workers=2) as pool:
            canceled = pool.submit(cancel)
            claimed = pool.submit(claim)
            status, row = canceled.result(), claimed.result()
        if row is None:
            assert status == "canceled"
        else:
            assert status == "TASK_NOT_CANCELABLE"
            worker.dispatch(row)
            assert finish(database, media, user, task["id"])["status"] == "completed"


def test_multiple_workers_submit_only_once_and_heartbeat_extends_lease(user, database, media):
    settings = database.settings.model_copy(update={"task_lease_seconds": 3})
    short_database = Database(settings)
    task = accepted(user, image_request(create(user)))
    entered, release = threading.Event(), threading.Event()

    def block(task, inputs):
        entered.set()
        assert release.wait(10)

    media.before_submit = block
    worker = TaskWorker(short_database, media)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(worker.execute_next)
        try:
            assert entered.wait(5)
            time.sleep(3.2)  # 超过真实租约长度，后台续租应仍持有；不是修改数据库伪造续租。
            assert not TaskWorker(short_database, media).execute_next()
            with short_database.snapshot() as connection:
                assert task_row(connection, task["id"])["status"] == "submitting"
        finally:
            release.set()
        assert pending.result(timeout=5)
    assert finish(database, media, user, task["id"])["status"] == "completed"
    assert len(media.records()) == 1


def test_late_submit_result_cannot_cross_expired_fence(user, database, media, monkeypatch):
    task = accepted(user, image_request(create(user)))
    worker = TaskWorker(database, media)
    original = media.submit

    def slow(task, inputs):
        accepted = original(task, inputs)
        expire(database, task["id"])
        assert TaskWorker(database, media).claim() is None
        return accepted

    monkeypatch.setattr(media, "submit", slow)
    worker.execute_next()
    result = get(user, task["id"])
    assert result["status"] == "submission_unknown" and not result["can_recover"]
    assert len(media.records()) == 1


def test_two_saving_workers_old_download_cannot_publish(user, database, media, monkeypatch):
    task = accepted(user, image_request(create(user)))
    step(database, media)
    step(database, media)
    due(database)
    first, second = TaskWorker(database, media), TaskWorker(database, media)
    old = first.claim()
    expire(database, task["id"])
    new = second.claim()
    assert new["status"] == "saving"
    second.save(new)
    with pytest.raises(LeaseLost):
        first.save(old)
    assert get(user, task["id"])["status"] == "completed"
    assert len(list(database.settings.media_dir.glob("*.blob"))) == 1
    assert len(media.records()) == 1


def test_policy_changed_after_claim_before_dispatch_and_resume(user, admin, database, media):
    task = accepted(user, image_request(create(user)))
    worker = TaskWorker(database, media)
    row = worker.claim()
    response = admin[0].patch(
        "/api/v1/admin/generation-policy", json={"image_enabled": False}, headers=headers(admin[1])
    )
    assert response.status_code == 200
    worker.dispatch(row)
    assert get(user, task["id"])["status"] == "queued"
    assert media.records() == []
    assert not TaskWorker(database, media).execute_next()
    assert (
        admin[0]
        .patch(
            "/api/v1/admin/generation-policy",
            json={"image_enabled": True},
            headers=headers(admin[1]),
        )
        .status_code
        == 200
    )
    assert finish(database, media, user, task["id"])["status"] == "completed"


@pytest.mark.parametrize("timing", ["queued", "claimed", "running"])
def test_disable_revokes_unsubmitted_but_accepted_still_saved(user, admin, database, media, timing):
    task = accepted(user, image_request(create(user)))
    worker = TaskWorker(database, media)
    row = worker.claim() if timing == "claimed" else None
    if timing == "running":
        step(database, media)
    assert (
        admin[0]
        .patch(
            f"/api/v1/admin/users/{user[2]}", json={"status": "disabled"}, headers=headers(admin[1])
        )
        .status_code
        == 200
    )
    if row:
        worker.dispatch(row)
    else:
        due(database)
        worker.execute_next()
    problem(user[0].get(f"/api/v1/tasks/{task['id']}"), 401, "AUTH_REQUIRED")
    if timing == "running":
        step(database, media)
    with database.snapshot() as connection:
        result = task_row(connection, task["id"])
        assert result["status"] == ("completed" if timing == "running" else "canceled")
    assert len(media.records()) == (1 if timing == "running" else 0)


def test_low_disk_pause_enqueue_and_saving_without_deleting(user, database, media, monkeypatch):
    from omniflow.conversations import ConversationService

    task = accepted(user, image_request(create(user)))
    step(database, media)
    step(database, media)
    original = ConversationService.storage_gate

    def low(self):
        fail(507, "STORAGE_UNAVAILABLE", "合成低磁盘")

    monkeypatch.setattr(ConversationService, "storage_gate", low)
    problem(submit(user, image_request(task["conversation_id"])), 507, "STORAGE_UNAVAILABLE")
    step(database, media)
    assert get(user, task["id"])["status"] == "saving"
    assert get(user, task["id"])["error"]["code"] == "STORAGE_UNAVAILABLE"
    monkeypatch.setattr(ConversationService, "storage_gate", original)
    assert finish(database, media, user, task["id"])["status"] == "completed"
    assert len(media.records()) == 1


def test_tool_task_survives_run_end_and_attaches_message_afterward(user, app, database, media):
    cid = create(user)
    run = accepted_run(user, cid)["run"]
    manager = RunManager(database)
    job = manager.claim()
    authority = ToolAuthority(user[2], cid, run["id"], manager.token, "synthetic-action-1")
    data = ImageTaskCreate(**image_request(cid))
    task, replay = app.state.tasks.create_tool(data, authority)
    assert not replay
    again, replay = app.state.tasks.create_tool(data, authority)
    assert replay and task == again
    assert user[0].get(f"/api/v1/runs/{run['id']}").json()["task_ids"] == [task["id"]]
    manager.end(job, "completed")
    result = finish(database, media, user, task["id"])
    assert result["status"] == "completed"
    state = snapshot(user, cid)
    assistant = next(m for m in state["messages"] if m["role"] == "assistant")
    assert assistant["artifact_version_ids"] == result["output_version_ids"]
    assert state["artifact_versions"][0]["id"] == result["output_version_ids"][0]
    with database.snapshot() as connection:
        events = [
            json.loads(r[0])
            for r in connection.execute(
                "SELECT payload FROM events WHERE conversation_id=?", (cid,)
            )
        ]
    updated = [e for e in events if e["type"] == "message.updated"][-1]
    schema("EventMessageUpdated", updated)
    assert updated["data"]["artifact_version_ids"] == result["output_version_ids"]


def test_run_cancel_rejects_new_tool_tasks_not_persisted_tasks(user, app, database, media):
    cid = create(user)
    run = accepted_run(user, cid)["run"]
    manager = RunManager(database)
    job = manager.claim()
    authority = ToolAuthority(user[2], cid, run["id"], manager.token, "synthetic-action-1")
    data = ImageTaskCreate(**image_request(cid))
    task, _ = app.state.tasks.create_tool(data, authority)
    assert (
        user[0].post(f"/api/v1/runs/{run['id']}/cancel", headers=headers(user[1])).status_code
        == 202
    )
    with pytest.raises(ProblemError) as exc:
        app.state.tasks.create_tool(
            data, ToolAuthority(user[2], cid, run["id"], manager.token, "synthetic-action-2")
        )
    assert exc.value.code == "RUN_ALREADY_TERMINAL"
    manager.end(job, "canceled")
    assert finish(database, media, user, task["id"])["status"] == "completed"


def test_tool_cannot_see_old_library_or_later_queued_references(user, app, database, media):
    cid = create(user)
    old = uploaded(user)
    run = accepted_run(user, cid)["run"]
    send(user, cid, attachment_version_ids=[old["version"]["id"]])
    manager = RunManager(database)
    manager.claim()
    authority = ToolAuthority(user[2], cid, run["id"], manager.token, "synthetic-action-1")
    data = ImageTaskCreate(**image_request(cid, reference_version_ids=[old["version"]["id"]]))
    with pytest.raises(ProblemError) as exc:
        app.state.tasks.create_tool(data, authority)
    assert exc.value.code == "RESOURCE_NOT_FOUND"
    data = VideoTaskCreate(
        conversation_id=cid,
        kind="ai_video",
        mode="text",
        prompt="合成画面",
        seconds=4,
        size_tier="720P",
        aspect_ratio="9:16",
    )
    with pytest.raises(ProblemError) as exc:
        app.state.tasks.create_tool(data, authority)
    assert exc.value.code == "REFERENCE_CONFIRMATION_REQUIRED"
    assert media.records() == []


def test_enqueue_event_failure_rolls_back_reservations_and_key(user, database, media):
    cid, key = create(user), str(uuid4())
    with database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER synthetic_event_failure BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT,'fault'); END"
        )
    problem(submit(user, image_request(cid), key), 500, "INTERNAL_ERROR")
    with database.transaction() as connection:
        for table in ("tasks", "task_idempotency", "task_media_scopes", "task_artifact_uses"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        connection.execute("DROP TRIGGER synthetic_event_failure")
    assert accepted(user, image_request(cid), key)["status"] == "queued"
