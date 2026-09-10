"""完整 ASGI 流式交付测试：不使用会聚合无限 SSE 的 TestClient.stream。"""

import asyncio
import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import pytest
from test_auth import (
    admin as admin,
)
from test_auth import (
    browsers as browsers,
)
from test_auth import (
    headers,
    issue_reset,
    post,
    problem,
    schema,
)
from test_auth import (
    user as user,
)
from test_conversations import (
    accepted,
    context,
    create,
    snapshot,
)
from test_conversations import (
    manager as manager,
)

from omniflow.conversations import ConversationService
from omniflow.problems import ProblemError


class ASGIStream:
    def __init__(self, app, actor, cid, *, query=None, last_event_id=None):
        self.app = app
        self.actor = actor
        self.cid = cid
        self.query = query or {}
        self.last_event_id = last_event_id
        self.frames = queue.Queue()
        self.started = queue.Queue()
        self.error = None
        self.loop = None
        self.disconnect = None
        self.thread = threading.Thread(target=self.run)

    def run(self):
        try:
            asyncio.run(self.request())
        except BaseException as exc:
            self.error = exc
        finally:
            self.frames.put(None)

    async def request(self):
        self.loop = asyncio.get_running_loop()
        self.disconnect = asyncio.Event()
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await self.disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                self.started.put(message)
            elif message["type"] == "http.response.body" and message.get("body"):
                self.frames.put(message["body"].decode())

        cookie = "; ".join(f"{key}={value}" for key, value in self.actor[0].cookies.items())
        req_headers = [(b"host", b"localhost:8443"), (b"cookie", cookie.encode())]
        if self.last_event_id is not None:
            req_headers.append((b"last-event-id", self.last_event_id.encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": f"/api/v1/conversations/{self.cid}/events",
            "root_path": "",
            "query_string": urlencode(self.query).encode(),
            "headers": req_headers,
            "client": ("127.0.0.1", 45000),
            "server": ("localhost", 8443),
        }
        await self.app(scope, receive, send)

    def __enter__(self):
        self.thread.start()
        try:
            response = self.started.get(timeout=5)
            assert response["status"] == 200, response
            response_headers = dict(response["headers"])
            assert response_headers[b"content-type"].startswith(b"text/event-stream")
            assert response_headers[b"x-accel-buffering"] == b"no"
            assert response_headers[b"cache-control"] == b"private, no-store"
            return self
        except BaseException:
            self.close()
            raise

    def next(self):
        frame = self.frames.get(timeout=5)
        if self.error:
            raise self.error
        return frame

    def event(self):
        while True:
            frame = self.next()
            assert frame is not None, "事件流意外结束"
            if frame.startswith(":"):
                continue
            data = json.loads(frame.split("data: ", 1)[1])
            if frame.startswith("event: control"):
                schema("StreamControl", data)
                assert "id:" not in frame
            else:
                schema("ConversationEvent", data)
                assert frame.startswith("id: " + data["event_id"] + "\n")
            return data

    def close(self):
        if self.thread.is_alive() and self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.disconnect.set)
        self.thread.join(timeout=5)
        assert not self.thread.is_alive(), "测试必须回收自己的流线程"
        if self.error:
            raise self.error

    def __exit__(self, *args):
        self.close()


def test_snapshot_then_replay_reconnect_and_unicode_deltas(app, user, manager):
    cid = create(user)
    accepted(user, cid, "合成🌊汉字")
    snap = snapshot(user, cid)
    assert manager.execute_next()  # 快照取得后、订阅前发生输出也不能丢。
    latest = snapshot(user, cid)
    cursor = snap["last_event_id"]
    with ASGIStream(app, user, cid, query={"after_event_id": cursor}) as stream:
        first = stream.event()
        assert int(first["event_id"]) == int(cursor) + 1
        saved_cursor = first["event_id"]
    # 断线不取消文字轮次。header 优先，即使 query 本身错误也按 header 恢复。
    with ASGIStream(
        app, user, cid, query={"after_event_id": "invalid"}, last_event_id=saved_cursor
    ) as stream:
        collected = []
        while True:
            event = stream.event()
            collected.append(event)
            if event["event_id"] == latest["last_event_id"]:
                break
    ids = [int(e["event_id"]) for e in collected]
    assert ids == list(range(int(saved_cursor) + 1, int(latest["last_event_id"]) + 1))
    deltas = [e["data"] for e in collected if e["type"] == "message.delta"]
    assert [d["chunk_index"] for d in deltas] == [1, 2]
    assert "".join(d["delta"] for d in deltas) == latest["messages"][-1]["content"]
    final = [e for e in collected if e["type"] == "message.updated"][-1]
    assert final["data"]["content"] == "离线假CLI：合成🌊汉字🌊"
    assert len(manager.provider.opened) == 1


def test_subscription_without_cursor_only_new_events_never_generates(app, user, manager):
    cid = create(user)
    accepted(user, cid, "已排队但不启动")
    before = snapshot(user, cid)
    with ASGIStream(app, user, cid) as stream:
        assert stream.next().startswith(": heartbeat")
        accepted(user, cid, "新事件")
        event = stream.event()
        assert int(event["event_id"]) == int(before["last_event_id"]) + 1
        assert event["data"]["content"] == "新事件"
    assert manager.provider.opened == []
    assert all(run["status"] == "queued" for run in snapshot(user, cid)["runs"])


@pytest.mark.parametrize("cursor", ["-1", "01", "1.2", "", "９", "9" * 10000, "999"])
def test_event_cursor_errors_before_stream(user, cursor):
    cid = create(user)
    problem(
        user[0].get(f"/api/v1/conversations/{cid}/events", params={"after_event_id": cursor}),
        400,
        "INVALID_EVENT_CURSOR",
    )


def test_retention_boundary_and_live_expiry(app, user, database):
    cid = create(user)
    service = ConversationService(database)
    service.settings = database.settings.model_copy(update={"event_retention_count": 1})
    with ASGIStream(app, user, cid, query={"after_event_id": "0"}) as stream:
        assert stream.next().startswith(": heartbeat")
        # 单事务插入并裁剪，避免流先读到新事件影响边界测试。
        original = app.state.conversations
        with database.transaction() as connection:
            from omniflow.conversations import emit_conversation

            emit_conversation(connection, cid)
            emit_conversation(connection, cid)
            connection.execute("UPDATE conversations SET event_floor=1 WHERE id=?", (cid,))
            connection.execute("DELETE FROM events WHERE conversation_id=? AND seq<=1", (cid,))
        control = stream.event()
        assert control["code"] == "EVENT_CURSOR_EXPIRED"
        assert stream.next() is None
    problem(
        user[0].get(f"/api/v1/conversations/{cid}/events", params={"after_event_id": "0"}),
        410,
        "EVENT_CURSOR_EXPIRED",
    )
    snap = snapshot(user, cid)
    with ASGIStream(app, user, cid, query={"after_event_id": snap["last_event_id"]}) as stream:
        assert stream.next().startswith(": heartbeat")
    accepted(user, cid, "消息保留")
    service.prune_events(cid)
    assert snapshot(user, cid)["messages"][0]["content"] == "消息保留"
    assert original.prepare_events(cid, snapshot(user, cid)["last_event_id"], context(user[0])) >= 2


@pytest.mark.parametrize("revoke", ["logout", "disable", "reset", "expire"])
def test_revocation_closes_existing_stream_promptly(app, user, admin, database, revoke):
    cid = create(user)
    old_context = context(user[0])
    with ASGIStream(app, user, cid) as stream:
        assert stream.next().startswith(": heartbeat")
        if revoke == "logout":
            assert post(user[0], "/auth/logout", user[1]).status_code == 204
        elif revoke == "disable":
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
        elif revoke == "reset":
            raw = issue_reset(admin, user[2])
            response = post(
                user[0],
                "/auth/password-resets/complete",
                user[1],
                {"reset_token": raw, "new_password": "Synthetic New Password!"},
            )
            assert response.status_code == 204
        else:
            with database.transaction() as connection:
                connection.execute(
                    "UPDATE login_sessions SET expires_at='2000-01-01T00:00:00.000000Z' "
                    "WHERE token_hash=?",
                    (old_context.token_hash,),
                )
        assert stream.event()["code"] == "AUTH_REQUIRED"
        assert stream.next() is None
    with pytest.raises(ProblemError) as caught:
        app.state.conversations.next_event(cid, 0, old_context)
    assert caught.value.code == "AUTH_REQUIRED"


@pytest.mark.parametrize("reason", ["delete", "restart"])
def test_delete_or_restart_closes_stream_with_control(app, user, reason):
    cid = create(user)
    with ASGIStream(app, user, cid) as stream:
        assert stream.next().startswith(": heartbeat")
        if reason == "delete":
            assert (
                user[0].delete(f"/api/v1/conversations/{cid}", headers=headers(user[1])).status_code
                == 204
            )
        else:
            app.state.stream_shutdown.set()
        try:
            assert stream.event()["code"] == (
                "RESOURCE_NOT_FOUND" if reason == "delete" else "SERVICE_RESTARTING"
            )
            assert stream.next() is None
        finally:
            app.state.stream_shutdown.clear()


def test_snapshot_is_one_read_transaction_under_concurrent_enqueue(app, user, monkeypatch):
    cid = create(user)
    accepted(user, cid, "快照前")
    baseline = snapshot(user, cid)
    entered, release = threading.Event(), threading.Event()
    service = app.state.conversations
    original = service.message_page

    def paused(*args):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(service, "message_page", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.snapshot, cid, context(user[0]))
        try:
            assert entered.wait(timeout=5)
            accepted(user, cid, "快照期间提交")
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result == baseline
    assert (
        service.next_event(cid, int(result["last_event_id"]), context(user[0]))["data"]["content"]
        == "快照期间提交"
    )
