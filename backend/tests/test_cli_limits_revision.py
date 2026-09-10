"""独立审查缺陷的正式回归；仅合成 HTTP 和内存 MCP 管道。"""

import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from uuid import uuid4

import pytest
from test_agy_adapter import evidence, gate
from test_artifacts import image_bytes, uploaded
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import headers, problem
from test_auth import user as user
from test_conversations import create
from test_media_adapters import URL, Exchange, agnes, install, video_request
from test_tasks import accepted, action, due, get, image_request, step, submit
from test_tasks import media as media
from test_tool_gateway import image_args, scope_for

from omniflow import db
from omniflow.auth_service import AuthService
from omniflow.ffmpeg_adapter import FFmpegProvider, MediaRouter
from omniflow.problems import ProblemError
from omniflow.provider_gate import EvidenceGate
from omniflow.provider_limits import ProviderLimits, retry_deadline
from omniflow.safe_http import HTTPResponse, SafeHTTP
from omniflow.task_worker import TaskWorker
from omniflow.tool_gateway import ToolGateway


def test_provider_limit_pauses_new_and_queued_after_restart(user, app, database):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    cid = create(user)
    key = str(uuid4())
    first = accepted(user, video_request(cid), key=key)
    queued = accepted(user, video_request(cid))
    exchange.add({"detail": "synthetic-private-limit"}, status=429, headers={"Retry-After": "120"})
    step(database, adapter)
    assert get(user, first["id"])["status"] == "submission_unknown"
    restarted = agnes(database, exchange)
    install(app, database, restarted)
    response = submit(user, video_request(cid))
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "PROVIDER_LIMIT_REACHED"
    step(database, restarted)
    task = get(user, queued["id"])
    assert task["status"] == "queued"
    assert task["error"]["code"] == "PROVIDER_LIMIT_REACHED"
    assert len(exchange.requests) == 1
    assert "synthetic-private-limit" not in response.text + json.dumps(task)
    replay = submit(user, video_request(cid), key=key)
    assert replay.status_code == 202 and replay.json()["id"] == first["id"]
    assert replay.headers["Idempotency-Replayed"] == "true"
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 2


@pytest.mark.parametrize("task_id", [7, True, None, [], {}, "not-a-uuid", "\ud800"])
def test_malformed_read_task_keeps_stdio_alive(user, database, media, task_id):
    scope = scope_for(user, database)
    frames = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_task", "arguments": {"task_id": task_id}},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    reader = io.BytesIO(b"".join((json.dumps(frame) + "\n").encode() for frame in frames))
    writer = io.BytesIO()
    ToolGateway(database, media).serve_stdio(scope, reader, writer)
    responses = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert len(responses) == 2
    assert responses[0] == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32000, "message": "VALIDATION_ERROR"},
    }
    assert responses[1]["id"] == 2 and len(responses[1]["result"]["tools"]) == 5
    assert media.records() == []


@pytest.mark.parametrize("kind", ["image", "ai_video"])
def test_shared_limit_blocks_other_users_kinds_tools_and_policy_cannot_clear(
    user, admin, app, database, kind
):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    request = image_request if kind == "image" else video_request
    scope = scope_for(user, database)
    task = accepted(user, request(scope.conversation_id))
    exchange.add(b"synthetic-private-body", mime="text/html", status=429)
    step(database, adapter)
    assert get(user, task["id"])["status"] == "submission_unknown"
    other_cid = create(admin)
    for actor, cid in [(user, scope.conversation_id), (admin, other_cid)]:
        for factory in (image_request, video_request):
            problem(submit(actor, factory(cid)), 503, "PROVIDER_LIMIT_REACHED")
    for enabled in (False, True):
        response = admin[0].patch(
            "/api/v1/admin/generation-policy",
            json={"image_enabled": enabled, "ai_video_enabled": enabled},
            headers=headers(admin[1]),
        )
        assert response.status_code == 200
    problem(submit(user, image_request(scope.conversation_id)), 503, "PROVIDER_LIMIT_REACHED")
    with pytest.raises(ProblemError) as error:
        ToolGateway(database, agnes(database, exchange)).call(
            scope, 1, "submit_image", image_args()
        )
    assert error.value.code == "PROVIDER_LIMIT_REACHED"
    assert len(exchange.requests) == 1
    # 原任务没有可续查 ID，不提供借 recover 重发的后门；停止排队仍可用。
    problem(action(user, task["id"], "recover"), 409, "RECONCILIATION_REQUIRED")
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM provider_limits").fetchone()[0] == 1


def test_limit_does_not_block_local_motion_or_text(user, app, database):
    exchange = Exchange()
    remote = agnes(database, exchange)
    router = MediaRouter(remote, FFmpegProvider(database, gate=gate()))
    install(app, database, router)
    remote.limits.record("120")
    gate().check("text")
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
    assert task["execution_engine"] == "local-ffmpeg"
    problem(submit(user, image_request(cid)), 503, "PROVIDER_LIMIT_REACHED")
    canceled = action(user, task["id"], "cancel")
    assert canceled.status_code == 200 and canceled.json()["status"] == "canceled"
    assert exchange.requests == []


@pytest.mark.parametrize("limited_during", ["submit", "poll"])
def test_accepted_video_still_polls_and_saves_during_limit(user, app, database, limited_during):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    cid = create(user)
    task = accepted(user, video_request(cid))
    exchange.add({"model": "agnes-video-2.5-flash", "video_id": "synthetic-video"})
    step(database, adapter)
    if limited_during == "submit":
        # 原任务留在 running，另一个提交返回限流；不重新生成原任务。
        other = accepted(user, image_request(cid))
        with database.transaction() as connection:
            connection.execute(
                "UPDATE tasks SET next_attempt_at='9999-01-01' WHERE id=?", (task["id"],)
            )
        exchange.add({}, status=429, headers={"Retry-After": "120"})
        assert TaskWorker(database, adapter).execute_next()
        assert get(user, other["id"])["status"] == "submission_unknown"
    else:
        exchange.add({}, status=429, headers={"Retry-After": "120"})
        step(database, adapter)
        result = get(user, task["id"])
        assert result["status"] == "running" and result["can_recover"] is True
        assert result["error"]["code"] == "PROVIDER_LIMIT_REACHED"
    problem(submit(user, video_request(cid)), 503, "PROVIDER_LIMIT_REACHED")
    exchange.add(
        {
            "model": "agnes-video-2.5-flash",
            "video_id": "synthetic-video",
            "status": "completed",
            "metadata": {"url": URL},
        }
    )
    step(database, agnes(database, exchange))
    exchange.add(
        Path(__file__).with_name("fixtures").joinpath("synthetic.mp4").read_bytes(),
        mime="video/mp4",
    )
    step(database, agnes(database, exchange))
    assert get(user, task["id"])["status"] == "completed"
    assert sum(r.method == "POST" for r in exchange.requests) == (
        2 if limited_during == "submit" else 1
    )


def test_cooldown_requires_new_evidence_then_only_queued_task_resumes(user, app, database):
    clock = [1000.0]
    exchange = Exchange()
    adapter = agnes(database, exchange)
    proof = replace(evidence("image"), checked_at=990.0, expires_at=1290.0)
    adapter.gate = EvidenceGate(lambda kind: proof, clock=lambda: clock[0])
    adapter.limits = ProviderLimits(database, clock=lambda: clock[0])
    install(app, database, adapter)
    cid = create(user)
    unknown = accepted(user, image_request(cid))
    queued = accepted(user, image_request(cid))
    exchange.add({}, status=429, headers={"Retry-After": "120"})
    step(database, adapter)
    clock[0] = 1119.0
    proof = replace(proof, checked_at=1110.0, expires_at=1300.0)
    problem(submit(user, image_request(cid)), 503, "PROVIDER_LIMIT_REACHED")
    clock[0] = 1121.0
    proof = replace(proof, checked_at=990.0, expires_at=1290.0)  # 冷却已过，旧快照仍无效。
    problem(submit(user, image_request(cid)), 503, "PROVIDER_LIMIT_REACHED")
    step(database, adapter)
    assert get(user, queued["id"])["status"] == "queued"
    proof = replace(proof, checked_at=1121.0, expires_at=1320.0)
    exchange.add({"data": [{"url": URL}]})
    step(database, adapter)
    step(database, adapter)
    exchange.add(image_bytes(), mime="image/png")
    step(database, adapter)
    assert get(user, queued["id"])["status"] == "completed"
    assert get(user, unknown["id"])["status"] == "submission_unknown"
    assert sum(r.method == "POST" for r in exchange.requests) == 2
    # 真正的新用户动作可以继续；不设每日业务额度。
    assert accepted(user, image_request(cid))["status"] == "queued"


@pytest.mark.parametrize(
    "header,seconds",
    [
        (None, 300),
        ("garbage", 300),
        ("-1", 300),
        ("NaN", 300),
        ("1.5", 300),
        ("0", 1),
        ("120", 120),
        (" 120 ", 120),
        ("9" * 40, int("9" * 40)),
    ],
)
def test_retry_after_safe_defaults_and_no_shortening_large_delay(header, seconds):
    assert retry_deadline(header, 1000.0) == 1000.0 + seconds


def test_retry_after_http_date_and_concurrent_observations_never_shorten(database):
    header = format_datetime(datetime.fromtimestamp(1120, UTC), usegmt=True)
    assert retry_deadline(header, 1000.0) == 1120.0
    assert retry_deadline(header, 1200.0) == 1500.0  # 过期的日期不是有效新授权。
    limits = ProviderLimits(database, clock=lambda: 1000.0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(limits.record, ["120", "600", "1", header]))
    with database.snapshot() as connection:
        row = connection.execute("SELECT * FROM provider_limits").fetchone()
        assert row["retry_at"] == 1600.0 and row["observed_at"] == 1000.0
    later = ProviderLimits(database, clock=lambda: 1100.0)
    later.record("1")
    with database.snapshot() as connection:
        row = connection.execute("SELECT * FROM provider_limits").fetchone()
        assert row["retry_at"] == 1600.0 and row["observed_at"] == 1100.0


def test_429_recorded_even_when_body_would_fail_capacity_decode_or_timeout(database):
    closed = []

    def chunks():
        raise AssertionError("限流错误页不应读取")
        yield b""  # 仅构造惰性迭代器。

    http = SafeHTTP(
        exchange=lambda request: HTTPResponse(
            429,
            {
                "Retry-After": "120",
                "Content-Length": "999999999",
                "Content-Encoding": "gzip",
            },
            chunks(),
            lambda: closed.append(True),
        ),
        resolver=lambda host: ("8.8.8.8",),
    )
    adapter = agnes(database, Exchange())
    adapter.http = http
    with pytest.raises(ProblemError) as error:
        adapter.request("POST", "/v1/videos", {})
    assert error.value.code == "PROVIDER_LIMIT_REACHED"
    with pytest.raises(ProblemError) as error:
        agnes(database, Exchange()).check("image")
    assert error.value.code == "PROVIDER_LIMIT_REACHED"
    assert closed == [True]


def test_injected_transport_429_also_persists_and_expired_evidence_stays_denied(database):
    class RawTransport:
        def request(self, *args, **kwargs):
            return 429, {"Retry-After": "1"}, b"private-invalid-json"

    clock = [1000.0]
    adapter = agnes(database, Exchange())
    adapter.http = RawTransport()
    adapter.limits = ProviderLimits(database, clock=lambda: clock[0])
    with pytest.raises(ProblemError) as error:
        adapter.request("POST", "/v1/videos", {})
    assert error.value.code == "PROVIDER_LIMIT_REACHED"
    clock[0] = 1010.0
    for proof in (
        None,
        replace(evidence("image"), checked_at=1002.0, expires_at=1009.0),
        replace(evidence("image"), checked_at=1002.0, expires_at=1100.0, free=False),
    ):
        adapter.gate = EvidenceGate(lambda kind, proof=proof: proof, clock=lambda: clock[0])
        with pytest.raises(ProblemError) as error:
            adapter.check("image")
        assert error.value.code == "FREE_ACCESS_UNCONFIRMED"


def test_claimed_before_limit_is_paused_before_dispatch_fact(user, app, database):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    task = accepted(user, image_request(create(user)))
    worker = TaskWorker(database, adapter)
    row = worker.claim()
    assert row["status"] == "submitting"
    adapter.limits.record("120")
    worker.dispatch(row)
    current = get(user, task["id"])
    assert current["status"] == "queued" and current["error"]["code"] == "PROVIDER_LIMIT_REACHED"
    assert exchange.requests == []
    with database.snapshot() as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone()
        assert row["dispatched_at"] is None and row["lease_token"] is None


def test_v7_to_v8_preserves_history_and_account(settings, monkeypatch):
    # 使用另一份合成 v7 库，绝不降级或改写当前已迁移数据库。
    old_settings = settings.model_copy(update={"data_dir": settings.data_dir.parent / "v7"})
    old = db.Database(old_settings)
    migrations = db.MIGRATIONS
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", migrations[:7])
        assert old.migrate() == 7
    account = AuthService(old).create_admin("synthetic", "Synthetic Password 123!")
    with old.snapshot() as connection:
        history = [tuple(r) for r in connection.execute("SELECT * FROM schema_migrations")]
        before = dict(
            connection.execute("SELECT * FROM users WHERE id=?", (str(account.id),)).fetchone()
        )
    assert old.migrate() == migrations[-1].version
    assert old.migrate() == migrations[-1].version
    with old.snapshot() as connection:
        assert [tuple(r) for r in connection.execute("SELECT * FROM schema_migrations")][
            :7
        ] == history
        assert (
            dict(
                connection.execute("SELECT * FROM users WHERE id=?", (str(account.id),)).fetchone()
            )
            == before
        )
        assert connection.execute("SELECT count(*) FROM provider_limits").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    "bad_line",
    [
        b"{bad}\n",
        b"[]\n",
        b"null\n",
        b"\xff\n",
        b'{"id":1,"id":2}\n',
        b'{"params":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}\n",
    ],
    ids=["syntax", "array", "null", "utf8", "duplicate", "depth"],
)
def test_complete_malformed_mcp_frame_does_not_discard_next_request(
    user, database, media, bad_line
):
    scope = scope_for(user, database)
    writer = io.BytesIO()
    ToolGateway(database, media).serve_stdio(
        scope, io.BytesIO(bad_line + b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'), writer
    )
    results = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert results[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "VALIDATION_ERROR"},
    }
    assert results[1]["id"] == 2 and len(results[1]["result"]["tools"]) == 5


def test_read_task_strict_errors_preserve_ownership_and_live_scope(user, admin, database, media):
    scope = scope_for(user, database)
    gateway = ToolGateway(database, media)
    mine = gateway.call(scope, 1, "submit_image", image_args())
    assert gateway.call(scope, 2, "read_task", {"task_id": mine["id"]})["id"] == mine["id"]
    other = accepted(admin, image_request(create(admin)))
    for tid in (other["id"], str(uuid4())):
        with pytest.raises(ProblemError) as error:
            gateway.call(scope, 3, "read_task", {"task_id": tid})
        assert error.value.code == "RESOURCE_NOT_FOUND"
    for arguments in ({}, {"task_id": 7}, {"task_id": mine["id"], "owner_id": admin[2]}):
        with pytest.raises(ProblemError) as error:
            gateway.call(scope, 3, "read_task", arguments)
        assert error.value.code == "VALIDATION_ERROR"
    with database.transaction() as connection:
        connection.execute("UPDATE runs SET status='stopping' WHERE id=?", (scope.run_id,))
    with pytest.raises(ProblemError) as error:
        gateway.call(scope, 2, "read_task", {"task_id": mine["id"]})
    assert error.value.code == "RUN_ALREADY_TERMINAL"


def test_independent_worker_process_observes_limit_and_leaves_queued(user, app, database, tmp_path):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    cid = create(user)
    unknown = accepted(user, video_request(cid))
    queued = accepted(user, video_request(cid))
    exchange.add({}, status=429, headers={"Retry-After": "120"})
    step(database, adapter)
    marker = tmp_path / "unexpected-submission.txt"
    due(database)
    process = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("provider_limit_process.py")),
            str(database.settings.data_dir),
            str(marker),
        ],
        capture_output=True,
        timeout=20,
        check=False,
        env={"PATH": str(Path(sys.executable).parent)},
    )
    assert process.returncode == 0, process.stderr.decode()
    assert not marker.exists()
    task = get(user, queued["id"])
    assert task["status"] == "queued" and task["error"]["code"] == "PROVIDER_LIMIT_REACHED"
    assert get(user, unknown["id"])["status"] == "submission_unknown"
    assert len(exchange.requests) == 1
