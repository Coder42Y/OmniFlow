"""审查缺陷回归：真实 API 交错与仍存活的合成 CLI，不调用供应商。"""

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from test_auth import PASSWORD, headers, post
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import accepted, create, read_run, snapshot, wait_for
from test_conversations import manager as manager

import omniflow.db as db_module
from omniflow.auth_security import now
from omniflow.auth_service import AuthService
from omniflow.db import MIGRATIONS, Database
from omniflow.run_manager import RunManager


def policy(admin, enabled):
    response = admin[0].patch(
        "/api/v1/admin/generation-policy",
        json={"text_enabled": enabled},
        headers=headers(admin[1]),
    )
    assert response.status_code == 200
    assert response.json()["text_enabled"] is enabled


def turns(session):
    return json.loads((session.directory / "synthetic-session.json").read_text())["turns"]


def test_pause_during_open_preserves_unsent_run_and_resumes_once(user, admin, manager, monkeypatch):
    cid = create(user)
    rid = accepted(user, cid, "暂停交错第一轮")["run"]["id"]
    second = accepted(user, cid, "后排不可提前可见")["run"]["id"]
    original = manager.provider.open

    def paused_open(**kwargs):
        session = original(**kwargs)
        policy(admin, False)
        return session

    with monkeypatch.context() as patch:
        patch.setattr(manager.provider, "open", paused_open)
        manager.execute_next()
    old = manager.provider.opened[0]["session"]
    assert turns(old) == []
    assert read_run(user, rid)["status"] == "queued"
    assert read_run(user, second)["status"] == "queued"
    mid = read_run(user, rid)["assistant_message_id"]
    assert snapshot(user, cid)["messages"][1]["status"] == "queued"
    assert old.process.poll() is not None
    assert not manager.execute_next()
    policy(admin, True)
    assert manager.execute_next()
    assert read_run(user, rid)["status"] == "completed"
    assert read_run(user, rid)["assistant_message_id"] == mid
    resumed = manager.provider.opened[-1]
    assert resumed["resume_id"] == old.session_id
    assert [m["content"] for m in turns(resumed["session"])[0]["history"]] == ["暂停交错第一轮"]
    assert manager.execute_next()
    assert len(turns(resumed["session"])) == 2
    assert [m["seq"] for m in snapshot(user, cid)["messages"]] == [1, 2, 3, 4]


def test_pause_before_reused_session_send_is_also_rechecked(user, admin, manager, monkeypatch):
    cid = create(user)
    accepted(user, cid, "首轮已完成")
    assert manager.execute_next()
    old = manager.provider.opened[0]["session"]
    rid = accepted(user, cid, "复用会话的下一轮")["run"]["id"]
    original = manager.claim

    def paused_claim():
        job = original()
        assert job is not None
        policy(admin, False)
        return job

    with monkeypatch.context() as patch:
        patch.setattr(manager, "claim", paused_claim)
        assert not manager.execute_next()
    assert len(manager.provider.opened) == 1
    assert len(turns(old)) == 1
    assert read_run(user, rid)["status"] == "queued"
    policy(admin, True)
    assert manager.execute_next()
    assert read_run(user, rid)["status"] == "completed"
    assert len(turns(manager.provider.opened[-1]["session"])) == 2


def test_pause_does_not_stop_already_submitted_turn(user, admin, manager):
    cid = create(user)
    rid = accepted(user, cid, "等待释放")["run"]["id"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.execute_next)
        try:
            wait_for(lambda: snapshot(user, cid)["messages"][-1]["content"] == "离线假CLI：")
            session = manager.provider.opened[0]["session"]
            policy(admin, False)
            assert session.process.poll() is None
            session.send_json({"release": True})
            assert future.result(timeout=5)
        finally:
            manager.shutdown.set()
    assert read_run(user, rid)["status"] == "completed"
    assert len(turns(session)) == 1


def test_expired_idle_lease_cannot_resume_while_old_process_alive(user, manager, database):
    # 使用合法最短租约及真实时间，不将 PID 存活伪装成单纯数据库状态。
    manager.settings = manager.settings.model_copy(update={"run_lease_seconds": 3})
    cid = create(user)
    accepted(user, cid)
    assert manager.execute_next()
    old = manager.provider.opened[0]["session"]
    rid = accepted(user, cid, "过期后下一轮")["run"]["id"]
    rival = RunManager(database, manager.provider)
    try:
        time.sleep(3.2)
        assert old.process.poll() is None
        assert not rival.execute_next()
        assert len(manager.provider.opened) == 1
        assert read_run(user, rid)["status"] == "queued"
        # 独立 Python 管理器也不能仅凭过期租约恢复仍存活的 CLI。
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
from pathlib import Path
sys.path.insert(0, str(Path('backend/tests').resolve()))
from cli_double import FakeProvider
from omniflow.config import Settings
from omniflow.db import Database
from omniflow.run_manager import RunManager
manager = RunManager(Database(Settings(environment='test', data_dir=Path(sys.argv[1]))),
                     FakeProvider(Path(sys.argv[2])))
try:
    assert not manager.execute_next()
    assert manager.provider.opened == []
    print('independent-process-excluded')
finally:
    manager.close()
""",
                str(database.settings.data_dir),
                str(manager.provider.root),
            ],
            cwd=Path(__file__).resolve().parents[2],
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert "independent-process-excluded" in result.stdout
        assert old.process.poll() is None
        manager.close()
        assert old.process.poll() is not None
        assert rival.execute_next()
        new = manager.provider.opened[-1]
        assert new["resume_id"] == old.session_id
        assert new["session"].process.pid != old.process.pid
        assert read_run(user, rid)["status"] == "completed"
    finally:
        rival.close()


@pytest.mark.parametrize("failure", ["exception", "false", "none"])
def test_unconfirmed_close_keeps_durable_exclusion(user, manager, database, monkeypatch, failure):
    cid = create(user)
    accepted(user, cid)
    assert manager.execute_next()
    old = manager.provider.opened[0]["session"]
    rid = accepted(user, cid, "停止未确认不能恢复")["run"]["id"]
    original = old.close

    def uncertain_close():
        if failure == "exception":
            raise TimeoutError("synthetic-close-timeout")
        return False if failure == "false" else None

    rival = RunManager(database, manager.provider)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(old, "close", uncertain_close)
            manager.drop_session(cid)
            manager.close()  # 全局收尾同样不能绕过失败的局部回收。
            assert old.process.poll() is None
            assert not rival.execute_next()
            assert len(manager.provider.opened) == 1
            assert read_run(user, rid)["status"] == "needs_reconciliation"
            with database.snapshot() as connection:
                hold = connection.execute(
                    "SELECT * FROM cli_process_holds WHERE conversation_id=?", (cid,)
                ).fetchone()
                assert hold["manager_token"] == manager.token
                assert hold["state"] == "uncertain"
            other = create(user)
            other_run = accepted(user, other, "另一对话仍可执行")["run"]["id"]
            assert rival.execute_next()
            assert read_run(user, other_run)["status"] == "completed"
        manager.close()  # 原持有者稍后确认退出才能释放占用；未知轮次仍不自动重放。
        assert old.process.poll() is not None
        assert not rival.execute_next()
    finally:
        original()
        manager.close()
        rival.close()


def test_cancel_paused_turn_updates_empty_assistant(user, admin, manager, monkeypatch):
    cid = create(user)
    rid = accepted(user, cid)["run"]["id"]
    original = manager.provider.open

    def paused_open(**kwargs):
        session = original(**kwargs)
        policy(admin, False)
        return session

    monkeypatch.setattr(manager.provider, "open", paused_open)
    assert not manager.execute_next()
    assert post(user[0], f"/runs/{rid}/cancel", user[1]).json()["status"] == "canceled"
    assistant = snapshot(user, cid)["messages"][-1]
    assert assistant["status"] == "interrupted" and assistant["content"] == ""
    policy(admin, True)
    assert not manager.execute_next()
    assert turns(manager.provider.opened[0]["session"]) == []


def test_hold_persisted_before_open_and_unknown_open_never_releases(
    user, manager, database, monkeypatch
):
    cid = create(user)
    rid = accepted(user, cid)["run"]["id"]
    original = manager.provider.open

    def lost_handle(**kwargs):
        with database.snapshot() as connection:
            hold = connection.execute(
                "SELECT * FROM cli_process_holds WHERE conversation_id=?", (cid,)
            ).fetchone()
            assert hold["state"] == "opening" and hold["manager_token"] == manager.token
        original(**kwargs)  # 合成启动已成功，但返回句柄之前失败。
        raise TimeoutError("synthetic-open-result-lost")

    rival = RunManager(database, manager.provider)
    try:
        monkeypatch.setattr(manager.provider, "open", lost_handle)
        assert manager.execute_next()
        old = manager.provider.opened[0]["session"]
        assert old.process.poll() is None
        assert cid not in manager.sessions
        manager.close()
        with database.snapshot() as connection:
            assert (
                connection.execute(
                    "SELECT state FROM cli_process_holds WHERE conversation_id=?", (cid,)
                ).fetchone()[0]
                == "uncertain"
            )
        assert read_run(user, rid)["status"] == "needs_reconciliation"
        accepted(user, cid, "不能重开")
        assert not rival.execute_next()
        assert len(manager.provider.opened) == 1
    finally:
        for opened in manager.provider.opened:
            opened["session"].close()  # 仅测试持有遗失句柄，用于回收自己的子进程。
        rival.close()


def test_close_confirmation_race_excludes_rival_until_exit(user, manager, database, monkeypatch):
    cid = create(user)
    accepted(user, cid)
    assert manager.execute_next()
    old = manager.provider.opened[0]["session"]
    rid = accepted(user, cid)["run"]["id"]
    original = old.close
    entered, release = threading.Event(), threading.Event()

    def delayed_close():
        entered.set()
        assert release.wait(timeout=5)
        return original()

    with database.transaction() as connection:
        connection.execute(
            "UPDATE conversations SET lease_until='2000-01-01T00:00:00.000000Z' WHERE id=?",
            (cid,),
        )
    rival = RunManager(database, manager.provider)
    try:
        with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as pool:
            patch.setattr(old, "close", delayed_close)
            future = pool.submit(manager.drop_session, cid)
            try:
                assert entered.wait(timeout=5)
                assert old.process.poll() is None
                assert not rival.execute_next()
                # 非持有者不能通过局部/全局收尾清掉别人的持久占用。
                rival.drop_session(cid)
                assert not rival.execute_next()
                assert len(manager.provider.opened) == 1
            finally:
                release.set()
            future.result(timeout=5)
        assert old.process.poll() is not None
        assert rival.execute_next()
        assert read_run(user, rid)["status"] == "completed"
    finally:
        release.set()
        rival.close()


def test_v3_upgrade_quarantines_unconfirmed_legacy_processes(settings, monkeypatch):
    database = Database(settings)
    cids = [str(uuid4()) for _ in range(3)]
    with monkeypatch.context() as patch:
        patch.setattr(db_module, "MIGRATIONS", MIGRATIONS[:3])
        database.migrate()
        owner = AuthService(database).create_admin("synthetic_legacy", PASSWORD)
        with database.transaction() as connection:
            for cid, cli_id, token in zip(
                cids, [str(uuid4()), None, None], [None, str(uuid4()), None], strict=True
            ):
                connection.execute(
                    "INSERT INTO conversations(id,owner_id,title,created_at,updated_at,"
                    "cli_session_id,manager_token) VALUES (?,?,?,?,?,?,?)",
                    (cid, str(owner.id), "合成升级", now(), now(), cli_id, token),
                )
            before = [tuple(row) for row in connection.execute("SELECT * FROM conversations")]
            history = [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")]
    assert database.migrate() == len(MIGRATIONS)
    with database.snapshot() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM conversations")] == before
        assert [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")][
            :3
        ] == history
        holds = connection.execute("SELECT * FROM cli_process_holds").fetchall()
        assert {row["conversation_id"] for row in holds} == set(cids[:2])
        assert {row["state"] for row in holds} == {"uncertain"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
