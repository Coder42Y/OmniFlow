"""复审回归：结构化正文出口及本地发布/receipt 之间的进程崩溃。"""

import json
import os
import subprocess
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from test_agy_adapter import gate, provider
from test_artifacts import uploaded
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversation_events import ASGIStream
from test_conversations import accepted as accepted_run
from test_conversations import create, snapshot
from test_media_adapters import install
from test_tasks import accepted, due, get, step
from test_tasks import media as media

from omniflow.agy_adapter import AgySession, StructuredActions, launch_plan
from omniflow.agy_schema import reply_schema
from omniflow.auth_security import expiry
from omniflow.ffmpeg_adapter import FFmpegProvider
from omniflow.provider_gate import MODELS
from omniflow.run_manager import RunManager, TextDelta
from omniflow.task_worker import TaskWorker
from omniflow.tool_gateway import ToolGateway


def test_structured_response_only_public_text_reaches_messages_snapshot_and_sse(
    user, app, database, media, tmp_path
):
    cid = create(user)
    secret = "SYNTHETIC_INTERNAL_ARGUMENT_ONLY"
    args = {"prompt": secret, "aspect_ratio": "9:16", "size_tier": "1K"}
    adapter, launcher = provider(
        tmp_path, {"actions": [{"name": "submit_image", "arguments": args}]}
    )
    run = accepted_run(user, cid, "请生成一张图片：合成方块")["run"]
    before = snapshot(user, cid)
    manager = RunManager(database, adapter, tool_gateway=ToolGateway(database, media))
    try:
        assert manager.execute_next()
        current = user[0].get(f"/api/v1/runs/{run['id']}").json()
        assert current["status"] == "completed" and len(current["task_ids"]) == 1
        argv = launcher.opened[0][0].argv
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        assert set(schema["required"]) == {"text", "actions"}
        latest = snapshot(user, cid)
        messages = user[0].get(f"/api/v1/conversations/{cid}/messages").json()
        expected = "合成回复：请生成一张图片：合成方块"
        assert messages["items"][-1]["content"] == expected
        assert latest["messages"][-1]["content"] == expected
        with ASGIStream(
            app, user, cid, query={"after_event_id": before["last_event_id"]}
        ) as stream:
            events = []
            while True:
                event = stream.event()
                events.append(event)
                if event["event_id"] == latest["last_event_id"]:
                    break
        # task.updated 按契约含 requested_parameters；本缺陷的边界是助手正文，
        # 不能把合法的任务参数字段也改成不返回以伪造通过。
        body_events = [e for e in events if e["type"].startswith("message.")]
        assert secret not in json.dumps([messages, latest["messages"], body_events])
        assert (
            "".join(e["data"]["delta"] for e in body_events if e["type"] == "message.delta")
            == expected
        )
        assert all("arguments" not in e["data"].get("content", "") for e in body_events)
    finally:
        manager.close()


def local_task(user, app, database):
    local = FFmpegProvider(database, gate=gate())
    install(app, database, local)
    cid = create(user)
    source = uploaded(user, cid=cid)
    task = accepted(
        user,
        {
            "conversation_id": cid,
            "kind": "local_motion",
            "image_version_id": source["version"]["id"],
            "motion_type": "dolly_in",
            "seconds": 1,
            "aspect_ratio": "16:9",
        },
    )
    return local, task


def expire(database):
    with database.transaction() as connection:
        connection.execute(
            "UPDATE tasks SET lease_until='2000-01-01T00:00:00.000000Z',"
            "next_attempt_at='2000-01-01T00:00:00.000000Z'"
        )


def crash_local_publish(user, app, database, monkeypatch):
    local, task = local_task(user, app, database)
    publish = local.storage.publish

    def crash(stage, vid):
        publish(stage, vid)
        raise SystemExit(78)

    monkeypatch.setattr(local.storage, "publish", crash)
    with pytest.raises(SystemExit):
        step(database, local)
    with database.snapshot() as connection:
        row = dict(connection.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
        assert connection.execute("SELECT count(*) FROM adapter_receipts").fetchone()[0] == 0
    return task, local.storage.path(row["output_version_id"])


def no_generation(*args, **kwargs):
    raise AssertionError("恢复不得再次运镜或下载")


def test_local_publish_before_receipt_restores_same_file_once(user, app, database, monkeypatch):
    task, path = crash_local_publish(user, app, database, monkeypatch)
    inode = path.stat().st_ino
    expire(database)
    for _ in range(3):
        local = FFmpegProvider(database, runner=no_generation)
        local.download = no_generation
        due(database)
        TaskWorker(database, local).execute_next()
    result = get(user, task["id"])
    assert result["status"] == "completed"
    assert path.stat().st_ino == inode and len(result["output_version_ids"]) == 1
    with database.snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE source_task_id=?", (task["id"],)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT result_key FROM adapter_receipts WHERE task_id=?", (task["id"],)
            ).fetchone()[0]
            == result["output_version_ids"][0]
        )


@pytest.mark.parametrize(
    "damage", ["missing", "invalid", "wrong_duration", "symlink", "public", "undispatched"]
)
def test_local_recovery_rejects_untrusted_or_incomplete_file(
    user, app, database, monkeypatch, damage
):
    task, path = crash_local_publish(user, app, database, monkeypatch)
    if damage == "missing":
        path.unlink()
    elif damage == "invalid":
        path.write_bytes(b"synthetic damaged video")
    elif damage == "wrong_duration":
        path.write_bytes(
            Path(__file__).with_name("fixtures").joinpath("synthetic.mp4").read_bytes()
        )
    elif damage == "symlink":
        other = path.with_suffix(".test-target")
        path.rename(other)
        path.symlink_to(other)
    elif damage == "public":
        path.chmod(0o644)
    else:
        with database.transaction() as connection:
            connection.execute("UPDATE tasks SET dispatched_at=NULL WHERE id=?", (task["id"],))
    expire(database)
    local = FFmpegProvider(database, runner=no_generation)
    for _ in range(2):
        due(database)
        TaskWorker(database, local).execute_next()
    assert get(user, task["id"])["status"] != "completed"
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM adapter_receipts").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE source_task_id=?", (task["id"],)
            ).fetchone()[0]
            == 0
        )


class ScriptedTransport:
    def __init__(self, plan):
        self.cid = str(uuid4())
        self.frames = deque(
            [
                {
                    "event": "init",
                    "conversation_id": self.cid,
                    "init": {
                        "model": MODELS["text"],
                        "permission_mode": "request-review",
                        "cwd": str(plan.cwd),
                        "json_schema": reply_schema(),
                    },
                }
            ]
        )

    def read(self, timeout):
        return self.frames.popleft()

    def write(self, value):
        pass


@pytest.mark.parametrize(
    "invalid",
    [
        "old_actions_only",
        "missing_structured",
        "missing_schema",
        "bad_schema",
        "extra",
        "bad_text",
        "bad_action",
        "too_many",
        "mismatched_response",
        "duplicate_field",
        "bad_stats",
    ],
)
def test_invalid_structured_result_never_publishes_buffered_json(tmp_path, invalid):
    plan = launch_plan(tmp_path / "agy", str(uuid4()), str(uuid4()), None)
    transport = ScriptedTransport(plan)
    session = AgySession(transport, plan, None, gate())
    session.send([{"role": "user", "content": "测试"}], "requested_only")
    structured = {
        "text": "仅用户正文",
        "actions": [
            {
                "name": "submit_image",
                "arguments": {"prompt": "SYNTHETIC_INTERNAL"},
            }
        ],
    }
    if invalid == "old_actions_only":
        structured.pop("text")
    elif invalid == "extra":
        structured["reasoning"] = "SYNTHETIC_INTERNAL"
    elif invalid == "bad_text":
        structured["text"] = {"secret": "SYNTHETIC_INTERNAL"}
    elif invalid == "bad_action":
        structured["actions"][0]["name"] = "run_command"
    elif invalid == "too_many":
        structured["actions"] *= 9
    response = json.dumps(structured)
    if invalid == "duplicate_field":
        response = response[:-1] + ',"text":"SYNTHETIC_INTERNAL"}'
    payload = {
        "conversation_id": transport.cid,
        "status": "SUCCESS",
        "num_turns": 1,
        "response": response,
        "structured_output": structured,
        "json_schema": reply_schema(),
    }
    if invalid == "missing_structured":
        payload.pop("structured_output")
    elif invalid == "missing_schema":
        payload.pop("json_schema")
    elif invalid == "bad_schema":
        payload["json_schema"] = {}
    elif invalid == "bad_stats":
        payload["usage"] = {"input_tokens": -1}
    elif invalid == "mismatched_response":
        payload["structured_output"] = {"text": "不同对象", "actions": []}
    # 每个字符单独成片，避免只处理某种恰好的 JSON 分块才不泄露。
    for fragment in response:
        transport.frames.append(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": transport.cid,
                    "state": "ACTIVE",
                    "step_type": "agent_response",
                    "text_delta": fragment,
                },
            }
        )
    transport.frames.append({"event": "result", "result": payload})
    seen = []
    with pytest.raises(ValueError):
        while transport.frames:
            seen.append(session.receive(0.1))
    assert not any(isinstance(e, (TextDelta, StructuredActions)) for e in seen)
    assert not session.pending


def test_disconnect_after_structured_fragments_keeps_public_message_empty(
    user, app, database, media, tmp_path
):
    cid = create(user)
    adapter, _ = provider(
        tmp_path,
        {
            "eof_after_deltas": True,
            "actions": [
                {
                    "name": "submit_image",
                    "arguments": {"prompt": "SYNTHETIC_INTERNAL"},
                }
            ],
        },
    )
    run = accepted_run(user, cid, "请生成一张图片：合成方块")["run"]
    before = snapshot(user, cid)
    manager = RunManager(database, adapter, tool_gateway=ToolGateway(database, media))
    try:
        assert manager.execute_next()
        latest = snapshot(user, cid)
        assert latest["messages"][-1]["content"] == ""
        current = user[0].get(f"/api/v1/runs/{run['id']}").json()
        assert current["status"] == "needs_reconciliation" and current["task_ids"] == []
        with ASGIStream(
            app, user, cid, query={"after_event_id": before["last_event_id"]}
        ) as stream:
            while True:
                event = stream.event()
                assert "SYNTHETIC_INTERNAL" not in json.dumps(event)
                assert event["type"] != "message.delta"
                if event["event_id"] == latest["last_event_id"]:
                    break
    finally:
        manager.close()


def test_local_recovery_rechecks_renewed_lease_after_validation(user, app, database, monkeypatch):
    task, _ = crash_local_publish(user, app, database, monkeypatch)
    worker = TaskWorker(database, FFmpegProvider(database, runner=no_generation))
    # 未过期不检查、更不接管已发布结果。
    worker.recover_local_publications()
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM adapter_receipts").fetchone()[0] == 0
    expire(database)
    original = worker.existing_file

    def renew_during_check(row):
        # 可在这里取得写事务，证明视频检查不在写锁中。
        with database.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET lease_until=? WHERE id=?", (expiry(60), task["id"])
            )
        return original(row)

    monkeypatch.setattr(worker, "existing_file", renew_during_check)
    worker.recover_local_publications()
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM adapter_receipts").fetchone()[0] == 0
    assert get(user, task["id"])["status"] == "submitting"


def test_two_recovery_scanners_attach_one_receipt_and_one_version(user, app, database, monkeypatch):
    task, path = crash_local_publish(user, app, database, monkeypatch)
    inode = path.stat().st_ino
    expire(database)
    barrier = Barrier(2)

    def recover(_):
        worker = TaskWorker(database, FFmpegProvider(database, runner=no_generation))
        original = worker.existing_file

        def synchronized(row):
            result = original(row)
            barrier.wait(timeout=5)
            return result

        worker.existing_file = synchronized
        worker.recover_local_publications()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(recover, range(2)))
    local = FFmpegProvider(database, runner=no_generation)
    step(database, local)
    step(database, local)
    assert get(user, task["id"])["status"] == "completed" and path.stat().st_ino == inode
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM adapter_receipts").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE source_task_id=?", (task["id"],)
            ).fetchone()[0]
            == 1
        )


def test_hard_process_exit_local_publish_before_receipt(user, app, database, tmp_path):
    _, task = local_task(user, app, database)
    marker = tmp_path / "renders.log"
    script = Path(__file__).with_name("local_recovery_process.py")
    command = [sys.executable, str(script), str(database.settings.data_dir), str(marker)]
    env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
    crash = subprocess.run([*command, "crash"], env=env, capture_output=True, timeout=60)
    assert crash.returncode == 78, crash.stderr.decode()
    with database.snapshot() as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
        path = TaskWorker(database).storage.path(row["output_version_id"])
        assert connection.execute("SELECT count(*) FROM adapter_receipts").fetchone()[0] == 0
    inode = path.stat().st_ino
    expire(database)
    for _ in range(3):
        due(database)
        restart = subprocess.run([*command, "recover"], env=env, capture_output=True, timeout=30)
        assert restart.returncode == 0, restart.stderr.decode()
    assert get(user, task["id"])["status"] == "completed"
    assert marker.read_text().splitlines() == ["render"] and path.stat().st_ino == inode
    with database.snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM artifact_versions WHERE source_task_id=?", (task["id"],)
            ).fetchone()[0]
            == 1
        )
