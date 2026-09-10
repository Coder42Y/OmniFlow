"""事务故障、调度竞争与边界回归；全部为合成对象。"""

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from test_auth import (
    PASSWORD,
    headers,
    post,
    problem,
)
from test_auth import (
    admin as admin,
)
from test_auth import (
    browsers as browsers,
)
from test_auth import (
    user as user,
)
from test_conversations import (
    accepted,
    create,
    read_run,
    send,
    snapshot,
    wait_for,
)
from test_conversations import (
    manager as manager,
)

import omniflow.db as db_module
from omniflow.auth_service import AuthService
from omniflow.conversations import require_tool_run
from omniflow.db import MIGRATIONS, Database
from omniflow.problems import ProblemError
from omniflow.run_manager import ProviderUnavailable, RunManager, TextDelta, TurnResult


def test_upgrade_v2_preserves_accounts_and_migration_history(settings, monkeypatch):
    database = Database(settings)
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "MIGRATIONS", MIGRATIONS[:2])
        database.migrate()
        user = AuthService(database).create_admin("synthetic_owner", PASSWORD)
        with database.snapshot() as connection:
            original_user = dict(
                connection.execute("SELECT * FROM users WHERE id=?", (str(user.id),)).fetchone()
            )
            history = [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")]
    assert database.migrate() == len(MIGRATIONS)
    with database.snapshot() as connection:
        assert (
            dict(connection.execute("SELECT * FROM users WHERE id=?", (str(user.id),)).fetchone())
            == original_user
        )
        assert [
            tuple(row)
            for row in connection.execute("SELECT * FROM schema_migrations ORDER BY version")
        ][:2] == history
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_message_event_failure_rolls_back_resource_sequence_and_idempotency(user, database):
    cid, key, mid = create(user), str(uuid4()), str(uuid4())
    with database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER synthetic_event_failure BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
    response = send(user, cid, key=key, client_id=mid)
    problem(response, 500, "INTERNAL_ERROR")
    assert "synthetic failure" not in response.text
    snap = snapshot(user, cid)
    assert snap["messages"] == snap["runs"] == [] and snap["last_event_id"] == "0"
    with database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT next_message_seq FROM conversations WHERE id=?", (cid,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM idempotency_records WHERE path LIKE '%/messages'"
            ).fetchone()[0]
            == 0
        )
        connection.execute("DROP TRIGGER synthetic_event_failure")
    result = accepted(user, cid, key=key, client_id=mid)
    assert result["message"]["seq"] == 1


@pytest.mark.parametrize("event", [TextDelta("原子增量"), TurnResult()])
def test_output_and_event_commit_or_rollback_together(user, database, manager, event):
    cid = create(user)
    accepted(user, cid)
    job = manager.claim()
    before = snapshot(user, cid)
    with database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER synthetic_output_failure BEFORE INSERT ON events "
            "BEGIN SELECT RAISE(ABORT, 'synthetic output failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        manager.accept(job, event)
    assert snapshot(user, cid) == before
    with database.transaction() as connection:
        connection.execute("DROP TRIGGER synthetic_output_failure")
    assert manager.accept(job, event)
    after = snapshot(user, cid)
    if isinstance(event, TextDelta):
        assert after["messages"][-1]["content"] == "原子增量"
    else:
        assert after["runs"] == []


def test_atomic_multi_manager_claim_and_nonterminal_unique_constraint(user, database):
    cid = create(user)
    accepted(user, cid, "第一轮")
    second = accepted(user, cid, "第二轮")
    barrier = threading.Barrier(6)
    managers = [RunManager(database) for _ in range(6)]

    def claim(manager):
        barrier.wait(timeout=5)
        return manager.claim()

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims = list(pool.map(claim, managers))
        assert sum(job is not None for job in claims) == 1
        job = next(job for job in claims if job is not None)
        assert [m["content"] for m in job["history"]] == ["第一轮"]
        with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
            connection.execute(
                "UPDATE runs SET status='running' WHERE id=?", (second["run"]["id"],)
            )
        with database.snapshot() as connection:
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        for manager in managers:
            manager.close()


def test_tool_gate_shares_cancel_transaction_and_preserves_accepted_actions(
    user, manager, database
):
    cid = create(user)
    rid = accepted(user, cid)["run"]["id"]
    manager.claim()
    args = {
        "owner_id": user[2],
        "conversation_id": cid,
        "run_id": rid,
        "manager_token": manager.token,
    }
    with database.transaction() as connection:
        connection.execute("CREATE TABLE synthetic_accepted_actions (id TEXT PRIMARY KEY)")
        require_tool_run(connection, **args)
        connection.execute("INSERT INTO synthetic_accepted_actions VALUES ('accepted-before-stop')")
    assert post(user[0], f"/runs/{rid}/cancel", user[1]).json()["status"] == "stopping"
    with pytest.raises(ProblemError) as caught, database.transaction() as connection:
        require_tool_run(connection, **args)
        connection.execute("INSERT INTO synthetic_accepted_actions VALUES ('forbidden-after-stop')")
    assert caught.value.code == "RUN_ALREADY_TERMINAL"
    with database.snapshot() as connection:
        assert [r[0] for r in connection.execute("SELECT id FROM synthetic_accepted_actions")] == [
            "accepted-before-stop"
        ]
    # 真正媒体任务在阶段 04 接入；这里不把合成动作表称为真实媒体队列。


@pytest.mark.parametrize("violation", ["discuss_only", "lease", "owner", "conversation"])
def test_tool_gate_rejects_untrusted_scope_or_discussion_only(user, manager, database, violation):
    cid = create(user)
    rid = accepted(
        user,
        cid,
        generation_permission="discuss_only" if violation == "discuss_only" else "requested_only",
    )["run"]["id"]
    manager.claim()
    args = {
        "owner_id": user[2],
        "conversation_id": cid,
        "run_id": rid,
        "manager_token": manager.token,
    }
    if violation == "lease":
        args["manager_token"] = str(uuid4())
    elif violation == "owner":
        args["owner_id"] = str(uuid4())
    elif violation == "conversation":
        args["conversation_id"] = create(user)
    with pytest.raises(ProblemError) as caught, database.transaction() as connection:
        require_tool_run(connection, **args)
    assert (
        caught.value.code
        == {
            "discuss_only": "FORBIDDEN",
            "lease": "RUN_ALREADY_TERMINAL",
            "owner": "RESOURCE_NOT_FOUND",
            "conversation": "RESOURCE_NOT_FOUND",
        }[violation]
    )


def test_low_disk_rejects_new_work_not_replay_or_old_reads(user, app, monkeypatch):
    cid, key, mid = create(user), str(uuid4()), str(uuid4())
    original = accepted(user, cid, key=key, client_id=mid)

    def low_disk():
        raise ProblemError(507, "STORAGE_UNAVAILABLE", "空间不足", "暂停新增。")

    service = app.state.conversations
    with monkeypatch.context() as patch:
        patch.setattr(service, "storage_gate", low_disk)
        problem(send(user, cid), 507, "STORAGE_UNAVAILABLE")
        assert send(user, cid, key=key, client_id=mid).json() == original
        assert snapshot(user, cid)["messages"][0]["id"] == original["message"]["id"]
    assert accepted(user, cid)["run"]["id"] != original["run"]["id"]


def test_running_account_disable_stops_outputs(user, admin, manager, database):
    cid = create(user)
    rid = accepted(user, cid, "等待释放")["run"]["id"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.execute_next)
        try:
            wait_for(lambda: snapshot(user, cid)["messages"][-1]["content"] == "离线假CLI：")
            assert (
                admin[0]
                .patch(
                    f"/api/v1/admin/users/{user[2]}",
                    json={"status": "disabled"},
                    headers=headers(admin[1]),
                )
                .status_code
                == 200
            )
            assert future.result(timeout=5)
        finally:
            manager.shutdown.set()
    with database.snapshot() as connection:
        assert (
            connection.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()[0]
            == "canceled"
        )
        assert (
            connection.execute(
                "SELECT content FROM messages WHERE run_id=? AND role='assistant'", (rid,)
            ).fetchone()[0]
            == "离线假CLI："
        )


@pytest.mark.parametrize("reason", ["shutdown", "unconfirmed_stop"])
def test_interrupted_or_unconfirmed_stop_remains_truthful(user, manager, monkeypatch, reason):
    cid = create(user)
    rid = accepted(user, cid, "等待释放")["run"]["id"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.execute_next)
        try:
            wait_for(lambda: snapshot(user, cid)["messages"][-1]["content"] == "离线假CLI：")
            if reason == "unconfirmed_stop":
                session = manager.provider.opened[0]["session"]
                original = session.stop

                def uncertain():
                    original()  # 测试仍回收自己的真实子进程，但不向产品承诺停止成功。
                    return False

                monkeypatch.setattr(session, "stop", uncertain)
                post(user[0], f"/runs/{rid}/cancel", user[1])
            else:
                manager.shutdown.set()
            assert future.result(timeout=5)
        finally:
            manager.shutdown.set()
    assert read_run(user, rid)["status"] == (
        "interrupted" if reason == "shutdown" else "needs_reconciliation"
    )


def test_cli_duplicate_session_id_cannot_bind_another_conversation(user, manager, monkeypatch):
    a, b = create(user), create(user)
    accepted(user, a)
    assert manager.execute_next()
    old_id = manager.provider.opened[0]["session"].session_id
    rid = accepted(user, b)["run"]["id"]
    original = manager.provider.open

    def duplicate(**kwargs):
        session = original(**kwargs)
        session.session_id = old_id
        return session

    monkeypatch.setattr(manager.provider, "open", duplicate)
    assert manager.execute_next()
    assert read_run(user, rid)["status"] == "needs_reconciliation"
    new_session = manager.provider.opened[-1]["session"]
    assert json.loads((new_session.directory / "synthetic-session.json").read_text())["turns"] == []
    assert new_session.process.poll() is not None


def test_provider_unavailable_after_send_is_unknown_not_safe_failure(user, manager, monkeypatch):
    cid = create(user)
    rid = accepted(user, cid)["run"]["id"]
    original = manager.provider.open

    def unavailable_later(**kwargs):
        session = original(**kwargs)

        def receive(timeout):
            raise ProviderUnavailable

        monkeypatch.setattr(session, "receive", receive)
        return session

    monkeypatch.setattr(manager.provider, "open", unavailable_later)
    assert manager.execute_next()
    assert read_run(user, rid)["status"] == "needs_reconciliation"


def test_deleted_conversation_erases_online_text_but_keeps_tombstones(user, database):
    cid = create(user, "合成删除标题")
    rid = accepted(user, cid, "合成待删除正文")["run"]["id"]
    assert post(user[0], f"/runs/{rid}/cancel", user[1]).status_code == 202
    assert (
        user[0].delete(f"/api/v1/conversations/{cid}", headers=headers(user[1])).status_code == 204
    )
    with database.snapshot() as connection:
        assert (
            connection.execute("SELECT content FROM messages WHERE run_id=?", (rid,)).fetchone()[0]
            == ""
        )
        assert (
            connection.execute("SELECT input_json FROM runs WHERE id=?", (rid,)).fetchone()[0]
            == "{}"
        )
        assert all(
            row[0] == "{}"
            for row in connection.execute(
                "SELECT response_json FROM idempotency_records WHERE conversation_id=?", (cid,)
            )
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM events WHERE conversation_id=?", (cid,)
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_switching_worker_conversation_releases_old_idle_process(user, manager):
    a, b = create(user), create(user)
    accepted(user, a, "A 第一轮")
    assert manager.execute_next()
    old = manager.provider.opened[0]["session"]
    assert old.process.poll() is None
    accepted(user, b, "B 第一轮")
    assert manager.execute_next()
    assert old.process.poll() is not None
    accepted(user, a, "A 再次打开")
    assert manager.execute_next()
    assert manager.provider.opened[-1]["resume_id"] == old.session_id
    assert manager.provider.opened[-1]["session"].process.pid != old.process.pid


def test_owner_scoped_idempotency_key_is_not_global(user, admin):
    a = create(user, key="shared-action")
    b = create(admin, key="shared-action")
    assert a != b
    assert create(user, key="shared-action") == a
    assert create(admin, key="shared-action") == b


def test_real_loopback_sse_signal_shutdown_and_persistent_run():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "backend/tools/smoke_conversations.py"],
        cwd=root,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS：" in result.stdout and "自管进程已退出" in result.stdout
