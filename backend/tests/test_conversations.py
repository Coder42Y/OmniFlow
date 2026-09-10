"""实际 FastAPI + 临时 SQLite + 子进程假 CLI 的阶段 02 回归。"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from cli_double import FakeProvider
from test_auth import (
    CONTRACT,
    headers,
    login,
    post,
    problem,
    schema,
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

from omniflow.auth_security import AuthContext, digest
from omniflow.conversation_models import ConversationCreate, MessageCreate
from omniflow.conversations import ConversationService, encode_cursor
from omniflow.run_manager import LeaseLost, RunManager, TextDelta, TurnResult


def context(browser):
    raw = browser.cookies.get("__Host-omniflow_session")
    return AuthContext("session", digest("session", raw), raw)


def create(actor, title="合成创作对话", key=None):
    client, token, _ = actor
    response = client.post(
        "/api/v1/conversations",
        json={"title": title},
        headers={**headers(token), "Idempotency-Key": key or str(uuid4())},
    )
    assert response.status_code == 201, response.text
    schema("Conversation", response.json())
    return response.json()["id"]


def send(actor, cid, content="合成消息", *, key=None, client_id=None, **extra):
    client, token, _ = actor
    return client.post(
        f"/api/v1/conversations/{cid}/messages",
        json={"client_message_id": client_id or str(uuid4()), "content": content, **extra},
        headers={**headers(token), "Idempotency-Key": key or str(uuid4())},
    )


def accepted(actor, cid, content="合成消息", **extra):
    response = send(actor, cid, content, **extra)
    assert response.status_code == 202, response.text
    schema("MessageAccepted", response.json())
    return response.json()


def read_run(actor, rid):
    response = actor[0].get(f"/api/v1/runs/{rid}")
    assert response.status_code == 200, response.text
    schema("Run", response.json())
    return response.json()


def snapshot(actor, cid):
    response = actor[0].get(f"/api/v1/conversations/{cid}/snapshot")
    assert response.status_code == 200, response.text
    schema("ConversationSnapshot", response.json())
    return response.json()


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("合成状态未在时限内到达")


@pytest.fixture
def manager(database, tmp_path):
    manager = RunManager(database, FakeProvider(tmp_path / "synthetic-cli"))
    yield manager
    manager.close()


def test_create_is_persistent_independent_and_does_not_start_cli(user, manager, database):
    a, b = create(user), create(user)
    assert a != b
    assert snapshot(user, a)["messages"] == []
    assert manager.provider.opened == []
    with database.connect(readonly=True) as connection:
        assert [r[0] for r in connection.execute("SELECT cli_session_id FROM conversations")] == [
            None,
            None,
        ]
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    service = ConversationService(database)
    assert service.conversation(a, context(user[0]))["id"] == a


def test_conversation_idempotency_conflict_and_tombstone(user):
    key = str(uuid4())
    a = create(user, key=key)
    response = user[0].post(
        "/api/v1/conversations",
        json={"title": "合成创作对话"},
        headers={**headers(user[1]), "Idempotency-Key": key},
    )
    assert response.json()["id"] == a
    assert response.headers["Idempotency-Replayed"] == "true"
    conflict = user[0].post(
        "/api/v1/conversations",
        json={"title": "不同标题"},
        headers={**headers(user[1]), "Idempotency-Key": key},
    )
    problem(conflict, 409, "IDEMPOTENCY_CONFLICT")
    for _ in range(2):
        assert (
            user[0].delete(f"/api/v1/conversations/{a}", headers=headers(user[1])).status_code
            == 204
        )
    gone = user[0].post(
        "/api/v1/conversations",
        json={"title": "合成创作对话"},
        headers={**headers(user[1]), "Idempotency-Key": key},
    )
    problem(gone, 410, "RESOURCE_GONE")
    problem(user[0].get(f"/api/v1/conversations/{a}"), 404, "RESOURCE_NOT_FOUND")
    assert user[0].get("/api/v1/conversations").json()["items"] == []


def test_message_two_identities_replay_without_second_run(user, manager):
    cid, key, mid = create(user), str(uuid4()), str(uuid4())
    first = accepted(user, cid, key=key, client_id=mid)
    assert manager.execute_next()
    for replay_key in (key, str(uuid4())):
        replay = send(user, cid, key=replay_key, client_id=mid)
        assert replay.status_code == 202
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json() == first
    problem(send(user, cid, "不同内容", key=key, client_id=mid), 409, "IDEMPOTENCY_CONFLICT")
    problem(send(user, cid, "不同内容", client_id=mid), 409, "IDEMPOTENCY_CONFLICT")
    assert not manager.execute_next()
    assert len(manager.provider.opened) == 1
    assert len(user[0].get(f"/api/v1/conversations/{cid}/runs").json()["items"]) == 1
    assert (
        user[0].delete(f"/api/v1/conversations/{cid}", headers=headers(user[1])).status_code == 204
    )
    problem(send(user, cid, key=key, client_id=mid), 410, "RESOURCE_GONE")


@pytest.mark.parametrize("kind", ["conversation", "message"])
def test_idempotency_race(user, app, database, kind):
    cid = create(user)
    ctx = context(user[0])
    barrier = threading.Barrier(6)
    data = MessageCreate(client_message_id=uuid4(), content="并发合成动作")

    def action(index):
        service = ConversationService(database)
        barrier.wait(timeout=5)
        if kind == "conversation":
            return service.create(ConversationCreate(title="并发标题"), "same-action", ctx)
        # 交替两种 HTTP 键，client_message_id 也应唯一。
        return service.enqueue(cid, data, f"same-action-{index % 2}", ctx)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(action, range(6)))
    assert len({json.dumps(result[0], sort_keys=True) for result in results}) == 1
    assert sum(not result[1] for result in results) == 1
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == (kind == "message")


@pytest.mark.parametrize("path", ["", "/messages", "/runs", "/snapshot", "/events"])
def test_cross_user_and_admin_no_private_access(user, admin, path):
    cid = create(user)
    accepted(user, cid)
    problem(admin[0].get(f"/api/v1/conversations/{cid}{path}"), 404, "RESOURCE_NOT_FOUND")


def test_cross_user_writes_and_runs(user, admin):
    cid = create(user)
    run = accepted(user, cid)["run"]
    problem(send(admin, cid), 404, "RESOURCE_NOT_FOUND")
    problem(
        admin[0].patch(
            f"/api/v1/conversations/{cid}", json={"title": "越权"}, headers=headers(admin[1])
        ),
        404,
        "RESOURCE_NOT_FOUND",
    )
    problem(
        admin[0].delete(f"/api/v1/conversations/{cid}", headers=headers(admin[1])),
        404,
        "RESOURCE_NOT_FOUND",
    )
    problem(admin[0].get(f"/api/v1/runs/{run['id']}"), 404, "RESOURCE_NOT_FOUND")
    problem(post(admin[0], f"/runs/{run['id']}/cancel", admin[1]), 404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_id": "pretend-owner"},
        {"role": "assistant"},
        {"model": "auto"},
        {"cli_session_id": "secret"},
        {"shell": "echo"},
        {"selected_version_id": None},
        {"generation_permission": "all"},
    ],
)
def test_message_rejects_injected_fields(user, extra):
    problem(send(user, create(user), **extra), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("content", ["", "  \n\t", "x" * 16001, "\ud800"])
def test_bad_message_content(user, content):
    cid = create(user)
    # JSON 代理字符用 ASCII 转义，避免客户端在发送前就拒绝。
    response = user[0].post(
        f"/api/v1/conversations/{cid}/messages",
        content=json.dumps({"client_message_id": str(uuid4()), "content": content}),
        headers={
            **headers(user[1]),
            "Idempotency-Key": str(uuid4()),
            "Content-Type": "application/json",
        },
    )
    problem(response, 422, "VALIDATION_ERROR")
    assert snapshot(user, cid)["runs"] == []


def test_unknown_references_are_not_ignored(user):
    cid = create(user)
    for field in ("selected_version_id", "reference_confirmation_id", "attachment_version_ids"):
        value = str(uuid4())
        if field == "attachment_version_ids":
            value = [value]
        problem(send(user, cid, **{field: value}), 404, "RESOURCE_NOT_FOUND")
    vid = str(uuid4())
    problem(send(user, cid, attachment_version_ids=[vid, vid]), 422, "VALIDATION_ERROR")
    assert snapshot(user, cid)["messages"] == []


def test_csrf_idempotency_required_and_auth_before_replay(user):
    cid, key, mid = create(user), str(uuid4()), str(uuid4())
    accepted(user, cid, key=key, client_id=mid)
    response = user[0].post(
        "/api/v1/conversations",
        json={"title": "无令牌"},
        headers={"Origin": "https://localhost:8443", "Idempotency-Key": key},
    )
    problem(response, 403, "CSRF_INVALID")
    problem(
        user[0].post("/api/v1/conversations", json={}, headers=headers(user[1])),
        422,
        "VALIDATION_ERROR",
    )
    for value in ("short", "x" * 129, "bad key "):
        problem(
            user[0].post(
                "/api/v1/conversations",
                json={},
                headers={**headers(user[1]), "Idempotency-Key": value},
            ),
            422,
            "VALIDATION_ERROR",
        )
    assert post(user[0], "/auth/logout", user[1]).status_code == 204
    problem(send(user, cid, key=key, client_id=mid), 401, "AUTH_REQUIRED")


def test_serial_context_and_three_isolated_cli_processes(user, admin, manager):
    a, b, other = create(user), create(user), create(admin)
    first = accepted(user, a, "随机代号-A-4f59", generation_permission="discuss_only")
    second = accepted(user, a, "后排消息绝不能提前可见")
    accepted(user, b, "随机代号-B-83ff")
    accepted(admin, other, "其他用户-Z")
    for _ in range(4):
        assert manager.execute_next()
    assert not manager.execute_next()
    opened = manager.provider.opened
    assert len(opened) == 3
    assert len({entry["session"].process.pid for entry in opened}) == 3
    assert len({entry["session"].session_id for entry in opened}) == 3
    for entry in opened:
        assert entry["resume_id"] is None
        state = json.loads((entry["session"].directory / "synthetic-session.json").read_text())
        turns = state["turns"]
        if entry["conversation_id"] == a:
            assert len(turns) == 2
            assert [m["content"] for m in turns[0]["history"]] == ["随机代号-A-4f59"]
            assert turns[0]["generation_permission"] == "discuss_only"
            assert [m["role"] for m in turns[1]["history"]] == ["user", "assistant", "user"]
            assert turns[1]["history"][-1]["content"] == "后排消息绝不能提前可见"
        else:
            assert len(turns) == 1
            assert len(turns[0]["history"]) == 1
            assert "随机代号-A-4f59" not in json.dumps(turns, ensure_ascii=False)
    assert read_run(user, first["run"]["id"])["status"] == "completed"
    assert read_run(user, second["run"]["id"])["status"] == "completed"
    snap = snapshot(user, a)
    assert [message["seq"] for message in snap["messages"]] == [1, 2, 3, 4]
    assert snap["runs"] == []
    wire = json.dumps(snap)
    for entry in opened:
        assert entry["session"].session_id not in wire
        assert str(entry["session"].directory) not in wire


def test_explicit_resume_old_conversation_after_manager_restart(user, manager, database):
    cid = create(user)
    accepted(user, cid, "旧对话代号")
    assert manager.execute_next()
    old = manager.provider.opened[0]["session"]
    manager.close()
    assert old.process.poll() is not None
    accepted(user, cid, "回到旧对话")
    restarted = RunManager(database, manager.provider)
    try:
        assert restarted.execute_next()
        opened = manager.provider.opened[-1]
        assert opened["resume_id"] == old.session_id
        assert opened["session"].session_id == old.session_id
        assert opened["session"].process.pid != old.process.pid
        turns = json.loads((old.directory / "synthetic-session.json").read_text())["turns"]
        assert len(turns) == 2
        assert turns[-1]["history"][0]["content"] == "旧对话代号"
    finally:
        restarted.close()


def test_cancel_queued_and_running_keeps_partial_text_and_other_conversation(
    user, manager, database
):
    a, b = create(user), create(user)
    first = accepted(user, a, "等待释放")["run"]
    queued = accepted(user, a, "不应该启动")["run"]
    other = accepted(user, b, "独立进行")["run"]
    problem(
        user[0].delete(f"/api/v1/conversations/{a}", headers=headers(user[1])),
        409,
        "RESOURCE_IN_USE",
    )
    second_manager = RunManager(database, manager.provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.execute_next)
        try:
            wait_for(
                lambda: any(
                    m["role"] == "assistant" and m["content"] for m in snapshot(user, a)["messages"]
                )
            )
            for _ in range(2):
                response = post(user[0], f"/runs/{queued['id']}/cancel", user[1])
                assert response.json()["status"] == "canceled"
            assert second_manager.execute_next()
            assert read_run(user, other["id"])["status"] == "completed"
            response = post(user[0], f"/runs/{first['id']}/cancel", user[1])
            assert response.status_code == 202 and response.json()["status"] == "stopping"
            assert future.result(timeout=5)
        finally:
            manager.shutdown.set()
            second_manager.close()
    assert read_run(user, first["id"])["status"] == "canceled"
    partial = [m for m in snapshot(user, a)["messages"] if m["role"] == "assistant"][0]
    assert partial["content"] == "离线假CLI：" and partial["status"] == "interrupted"
    assert not manager.execute_next()
    problem(post(user[0], f"/runs/{other['id']}/cancel", user[1]), 409, "RUN_ALREADY_TERMINAL")


@pytest.mark.parametrize("pause_before_send", [False, True])
def test_two_managers_cannot_execute_same_conversation(
    user, manager, database, monkeypatch, pause_before_send
):
    cid = create(user)
    first = accepted(user, cid, "等待释放")["run"]["id"]
    second = accepted(user, cid, "下一轮")["run"]["id"]
    allow_send = threading.Event()
    original_open = manager.provider.open

    def gated_open(**kwargs):
        session = original_open(**kwargs)
        if pause_before_send:
            assert allow_send.wait(timeout=5)
        return session

    monkeypatch.setattr(manager.provider, "open", gated_open)
    rival = RunManager(database, manager.provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(manager.execute_next)
        try:
            wait_for(lambda: len(manager.provider.opened) == 1)
            assert not rival.execute_next()
            allow_send.set()
            # opened 只证明握手完成，不证明本轮已发送。等真实增量落盘后才发控制帧，
            # 否则 release 可能先于 history 到达假 CLI，使它崩溃并正确进入待核对。
            wait_for(lambda: snapshot(user, cid)["messages"][1]["content"] == "离线假CLI：")
            assert read_run(user, first)["status"] == "running"
            assert read_run(user, second)["status"] == "queued"
            assert not rival.execute_next()
            manager.provider.opened[0]["session"].send_json({"release": True})
            assert future.result(timeout=5)
            assert read_run(user, first)["status"] == "completed"
            assert not rival.execute_next()  # 常驻进程仍由原管理器持有。
            assert manager.execute_next()
            assert read_run(user, second)["status"] == "completed"
        finally:
            allow_send.set()
            manager.shutdown.set()
            rival.close()
    assert len(manager.provider.opened) == 1
    session = manager.provider.opened[0]["session"]
    turns = json.loads((session.directory / "synthetic-session.json").read_text())["turns"]
    assert len(turns) == 2
    assert [m["content"] for m in turns[0]["history"]] == ["等待释放"]
    assert [m["content"] for m in turns[1]["history"]] == [
        "等待释放",
        "离线假CLI：等待释放🌊",
        "下一轮",
    ]


@pytest.mark.parametrize("content", ["中途崩溃", "不可信日志"])
def test_uncertain_result_is_never_blindly_replayed(user, manager, database, content):
    cid = create(user)
    run = accepted(user, cid, content)["run"]
    accepted(user, cid, "后续消息暂缓")
    assert manager.execute_next()
    result = read_run(user, run["id"])
    assert result["status"] == "needs_reconciliation"
    assert result["error"]["code"] == "RECONCILIATION_REQUIRED"
    restarted = RunManager(database, manager.provider)
    try:
        for _ in range(3):
            assert not restarted.execute_next()
    finally:
        restarted.close()
    assert len(manager.provider.opened) == 1
    assert snapshot(user, cid)["messages"][1]["content"] == "离线假CLI："
    with database.connect(readonly=True) as connection:
        events = " ".join(row[0] for row in connection.execute("SELECT payload FROM events"))
    assert "SYNTHETIC-SECRET" not in events and "/synthetic/private" not in events


def test_lease_expiry_recovery_fences_late_output(user, manager, database):
    cid = create(user)
    rid = accepted(user, cid)["run"]["id"]
    job = manager.claim()
    assert manager.accept(job, TextDelta("已交付部分"))
    with database.transaction() as connection:
        connection.execute(
            "UPDATE conversations SET lease_until='2000-01-01T00:00:00.000000Z' WHERE id=?", (cid,)
        )
    rival = RunManager(database, manager.provider)
    try:
        assert not rival.execute_next()
        assert read_run(user, rid)["status"] == "needs_reconciliation"
        with pytest.raises(LeaseLost):
            manager.accept(job, TextDelta("过期写入"))
        with pytest.raises(LeaseLost):
            manager.accept(job, TurnResult())
    finally:
        rival.close()
    assert snapshot(user, cid)["messages"][-1]["content"] == "已交付部分"
    assert manager.provider.opened == []


def test_disabled_provider_is_truthful_not_mock(user, database):
    cid = create(user)
    rid = accepted(user, cid)["run"]["id"]
    manager = RunManager(database)
    try:
        assert manager.execute_next()
    finally:
        manager.close()
    run = read_run(user, rid)
    assert run["status"] == "failed" and run["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert snapshot(user, cid)["messages"][-1]["content"] == ""


def test_pause_checked_at_enqueue_and_dispatch_and_replay_still_valid(user, admin, manager):
    cid, key, mid = create(user), str(uuid4()), str(uuid4())
    original = accepted(user, cid, key=key, client_id=mid)
    response = admin[0].patch(
        "/api/v1/admin/generation-policy", json={"text_enabled": False}, headers=headers(admin[1])
    )
    assert response.status_code == 200
    problem(send(user, cid), 503, "GENERATION_PAUSED")
    assert send(user, cid, key=key, client_id=mid).json() == original
    assert not manager.execute_next()
    assert manager.provider.opened == []
    assert (
        admin[0]
        .patch(
            "/api/v1/admin/generation-policy",
            json={"text_enabled": True},
            headers=headers(admin[1]),
        )
        .status_code
        == 200
    )
    assert manager.execute_next()


def test_queue_backpressure_failure_does_not_consume_key(user, database):
    service = ConversationService(database)
    service.settings = database.settings.model_copy(update={"run_queue_capacity": 1})
    ctx, cid = context(user[0]), create(user)
    first = service.enqueue(
        cid, MessageCreate(client_message_id=uuid4(), content="第一轮"), "first-action", ctx
    )[0]
    next_data = MessageCreate(client_message_id=uuid4(), content="下一轮")
    from omniflow.problems import ProblemError

    with pytest.raises(ProblemError) as caught:
        service.enqueue(cid, next_data, "next-action", ctx)
    assert caught.value.code == "QUEUE_BACKPRESSURE" and caught.value.retry_after == 2
    service.cancel(first["run"]["id"], ctx)
    assert not service.enqueue(cid, next_data, "next-action", ctx)[1]


def test_disable_then_enable_does_not_resurrect_queued_work(user, admin, manager):
    cid = create(user)
    run = accepted(user, cid)["run"]
    for state in ("disabled", "active"):
        assert (
            admin[0]
            .patch(
                f"/api/v1/admin/users/{user[2]}", json={"status": state}, headers=headers(admin[1])
            )
            .status_code
            == 200
        )
    login(user[0], "alice")
    assert not manager.execute_next()
    assert read_run(user, run["id"])["status"] == "canceled"
    assert manager.provider.opened == []


def test_pagination_stable_on_rename_and_snapshot_recent_fifty(user):
    ids = [create(user, str(i)) for i in range(3)]
    page = user[0].get("/api/v1/conversations", params={"limit": 1}).json()
    assert page["items"][0]["id"] == ids[-1]
    assert (
        user[0]
        .patch(
            f"/api/v1/conversations/{ids[0]}", json={"title": "新标题"}, headers=headers(user[1])
        )
        .status_code
        == 200
    )
    seen = [page["items"][0]["id"]]
    while page["next_cursor"]:
        page = (
            user[0]
            .get("/api/v1/conversations", params={"limit": 1, "cursor": page["next_cursor"]})
            .json()
        )
        schema("ConversationPage", page)
        seen.extend(r["id"] for r in page["items"])
    assert seen == list(reversed(ids))
    cid = ids[0]
    for i in range(53):
        accepted(user, cid, str(i))
    snap = snapshot(user, cid)
    assert len(snap["messages"]) == 50 and len(snap["runs"]) == 53
    assert [int(m["content"]) for m in snap["messages"]] == list(range(3, 53))
    older = (
        user[0]
        .get(
            f"/api/v1/conversations/{cid}/messages", params={"cursor": snap["messages_next_cursor"]}
        )
        .json()
    )
    assert [m["content"] for m in older["items"]] == ["0", "1", "2"]
    problem(
        user[0].get(
            f"/api/v1/conversations/{ids[1]}/messages",
            params={"cursor": snap["messages_next_cursor"]},
        ),
        400,
        "VALIDATION_ERROR",
    )
    run_page = user[0].get(f"/api/v1/conversations/{cid}/runs", params={"limit": 17}).json()
    seen = [r["id"] for r in run_page["items"]]
    while run_page["next_cursor"]:
        run_page = (
            user[0]
            .get(
                f"/api/v1/conversations/{cid}/runs",
                params={"limit": 17, "cursor": run_page["next_cursor"]},
            )
            .json()
        )
        schema("RunPage", run_page)
        seen.extend(r["id"] for r in run_page["items"])
    assert len(seen) == len(set(seen)) == 53


@pytest.mark.parametrize(
    "boundary",
    [
        ["not-a-date", str(uuid4())],
        ["\ud800", str(uuid4())],
        ["2026-02-30T01:02:03.000000Z", str(uuid4())],
        [[], {}],
    ],
)
def test_bad_pagination_cursor_is_safe(user, boundary):
    value = encode_cursor(["conversations", user[2], None], boundary)
    problem(user[0].get("/api/v1/conversations", params={"cursor": value}), 400, "VALIDATION_ERROR")


def test_run_request_response_contract_subset(app):
    actual = app.openapi()
    for path, item in CONTRACT["paths"].items():
        if (
            not (path.startswith("/conversations") or path.startswith("/runs"))
            or "reference-confirmations" in path
        ):
            continue
        for method, operation in item.items():
            live = actual["paths"]["/api/v1" + path][method]
            assert live["operationId"] == operation["operationId"]
            assert live["security"] == operation["security"]
            if "requestBody" in operation:
                assert (
                    live["requestBody"]["content"]["application/json"]["schema"]
                    == operation["requestBody"]["content"]["application/json"]["schema"]
                )
            for status, response in operation["responses"].items():
                if status.startswith("2") and "content" in response:
                    assert live["responses"][status]["content"] == response["content"]
