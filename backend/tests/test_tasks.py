"""阶段 04 实际 API、事务、归属、幂等和三类任务的离线闭环。"""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from media_double import FakeMediaProvider
from test_artifacts import confirmation, delete, uploaded
from test_auth import CONTRACT, headers, problem, schema
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import context, create, snapshot

from omniflow.artifacts import ArtifactService
from omniflow.auth_security import fail
from omniflow.media_provider import DisabledMediaProvider
from omniflow.task_models import ImageTaskCreate
from omniflow.task_worker import TaskWorker
from omniflow.tasks import TaskService


@pytest.fixture
def media(app, database, tmp_path):
    provider = FakeMediaProvider(tmp_path / "synthetic-provider.sqlite3")
    app.state.tasks = TaskService(database, provider)
    return provider


def image_request(cid, **extra):
    return {
        "conversation_id": cid,
        "kind": "image",
        "prompt": "合成蓝色方块，无品牌",
        "aspect_ratio": "9:16",
        "size_tier": "1K",
        "reference_version_ids": [],
        **extra,
    }


def submit(actor, data, key=None):
    return actor[0].post(
        "/api/v1/tasks",
        content=json.dumps(data, ensure_ascii=True),
        headers={
            **headers(actor[1]),
            "Content-Type": "application/json",
            "Idempotency-Key": key or str(uuid4()),
        },
    )


def accepted(actor, data, key=None):
    response = submit(actor, data, key)
    assert response.status_code == 202, response.text
    schema("Task", response.json())
    return response.json()


def get(actor, tid):
    response = actor[0].get(f"/api/v1/tasks/{tid}")
    assert response.status_code == 200, response.text
    schema("Task", response.json())
    return response.json()


def action(actor, tid, operation):
    return actor[0].post(f"/api/v1/tasks/{tid}/{operation}", headers=headers(actor[1]))


def due(database):
    # 仅合成数据库的确定性调度时钟，不影响租约/安全判断。
    with database.transaction() as connection:
        connection.execute("UPDATE tasks SET next_attempt_at='2000-01-01T00:00:00.000000Z'")


def step(database, provider):
    due(database)
    assert TaskWorker(database, provider).execute_next()


def finish(database, provider, actor, tid):
    for _ in range(6):
        result = get(actor, tid)
        if result["status"] in ("completed", "failed", "canceled", "submission_unknown"):
            return result
        step(database, provider)
    raise AssertionError(get(actor, tid))


def test_default_provider_denies_no_tasks_created(user, database):
    cid = create(user)
    problem(submit(user, image_request(cid)), 503, "PROVIDER_UNAVAILABLE")
    with database.snapshot() as connection:
        for table in ("tasks", "task_idempotency", "task_media_scopes"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert not TaskWorker(database).execute_next()


@pytest.mark.parametrize("kind", ["image", "ai_video", "local_motion"])
def test_three_kinds_restart_query_download_requested_actual(user, admin, database, media, kind):
    cid = create(user)
    if kind == "image":
        request = image_request(cid)
    elif kind == "ai_video":
        request = {
            "conversation_id": cid,
            "kind": kind,
            "mode": "text",
            "prompt": "合成蓝色画面",
            "seconds": 4,
            "size_tier": "720P",
            "aspect_ratio": "9:16",
        }
    else:
        image = uploaded(user, cid=cid)
        request = {
            "conversation_id": cid,
            "kind": kind,
            "image_version_id": image["version"]["id"],
            "motion_type": "dolly_in",
            "seconds": 4,
            "aspect_ratio": "9:16",
        }
    task = accepted(user, request)
    assert task["status"] == "queued" and task["can_cancel"] and not task["can_recover"]
    assert media.records() == []
    assert snapshot(user, cid)["tasks"] == [task]
    step(database, media)  # 每步均创建新的独立 worker 对象，不能依赖内存任务表。
    assert get(user, task["id"])["status"] == "running"
    problem(action(user, task["id"], "cancel"), 409, "TASK_NOT_CANCELABLE")
    step(database, media)
    assert get(user, task["id"])["status"] == "saving"
    step(database, media)
    result = get(user, task["id"])
    assert result["status"] == "completed" and len(result["output_version_ids"]) == 1
    assert len(media.records()) == 1
    assert result["requested_parameters"]["aspect_ratio"] == "9:16"
    with database.snapshot() as connection:
        row = connection.execute(
            "SELECT * FROM artifact_versions WHERE id=?", (result["output_version_ids"][0],)
        ).fetchone()
        assert row["source_task_id"] == task["id"]
        assert row["execution_engine"] == result["execution_engine"]
        assert connection.execute(
            "SELECT closed_at FROM task_media_scopes WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
        url = f"/api/v1/artifacts/{row['artifact_id']}/versions/{row['id']}/content"
        if kind != "image":
            assert result["requested_parameters"]["seconds"] == 4
            assert row["duration_seconds"] == 1.25 and row["fps"] == 12
            assert (row["width"], row["height"]) == (32, 48)
        else:
            assert (row["width"], row["height"]) == (12, 8)
        events = [
            json.loads(r[0])
            for r in connection.execute(
                "SELECT payload FROM events WHERE conversation_id=?", (cid,)
            )
        ]
    for event in events:
        if event["type"] == "task.updated":
            schema("EventTaskUpdated", event)
    assert [e["data"]["status"] for e in events if e["type"] == "task.updated"][-1] == "completed"
    assert user[0].head(url).status_code == 200
    assert user[0].get(url, headers={"Range": "bytes=0-7"}).status_code == 206
    assert admin[0].head(url).status_code == 404
    assert user[0].get(url).content
    assert snapshot(user, cid)["tasks"] == []
    assert not TaskWorker(database, media).execute_next()


def test_keyframe_confirmation_pins_version_and_grant_lifecycle(user, database, media):
    cid = create(user)
    source = uploaded(user, cid=cid)
    vid = source["version"]["id"]
    confirmed = confirmation(user, cid, vid).json()["id"]
    request = {
        "conversation_id": cid,
        "kind": "ai_video",
        "mode": "keyframe",
        "prompt": "合成测试",
        "seconds": 4,
        "size_tier": "720P",
        "aspect_ratio": "9:16",
        "reference_confirmation_id": confirmed,
    }
    task = accepted(user, request)
    problem(delete(user, source), 409, "RESOURCE_IN_USE")
    problem(
        user[0].delete(f"/api/v1/conversations/{cid}", headers=headers(user[1])),
        409,
        "RESOURCE_IN_USE",
    )
    grants = []

    def at_submit(task, inputs):
        assert inputs == (vid,)
        from omniflow.artifacts import TaskMediaService

        with database.transaction() as connection:
            grants.append(TaskMediaService(database).issue(connection, task["id"], vid))

    media.before_submit = at_submit
    step(database, media)
    gid, token = grants[0]
    grant_url = f"/api/v1/media-grants/{gid}/content?token={token}"
    assert user[0].get(grant_url).status_code == 200
    done = finish(database, media, user, task["id"])
    assert done["status"] == "completed"
    assert user[0].get(grant_url).status_code == 404
    assert token not in json.dumps(done)
    assert delete(user, source).status_code == 202


def test_task_idempotency_concurrency_and_tombstones(user, database, media, app):
    cid, key = create(user), str(uuid4())
    data = ImageTaskCreate(**image_request(cid))
    auth = context(user[0])
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: app.state.tasks.create(data, key, auth), range(6)))
    assert sum(not replay for _, replay in results) == 1
    assert all(r == results[0][0] for r, _ in results)
    task = results[0][0]
    problem(
        submit(user, image_request(cid, prompt="不同合成内容"), key), 409, "IDEMPOTENCY_CONFLICT"
    )
    result = finish(database, media, user, task["id"])
    replay = submit(user, image_request(cid), key)
    assert replay.json() == task and replay.headers["Idempotency-Replayed"] == "true"
    with database.snapshot() as connection:
        aid = connection.execute(
            "SELECT artifact_id FROM artifact_versions WHERE id=?",
            (result["output_version_ids"][0],),
        ).fetchone()[0]
    assert user[0].delete(f"/api/v1/artifacts/{aid}", headers=headers(user[1])).status_code == 202
    problem(submit(user, image_request(cid), key), 410, "RESOURCE_GONE")
    assert len(media.records()) == 1


def test_task_cross_account_csrf_filters_and_admin_redaction(user, admin, browsers, media):
    cid = create(user)
    task = accepted(user, image_request(cid, prompt="私密合成提示不能进入管理员列表"))
    tid = task["id"]
    for operation in ("cancel", "recover"):
        problem(action(admin, tid, operation), 404, "RESOURCE_NOT_FOUND")
        problem(
            user[0].post(
                f"/api/v1/tasks/{tid}/{operation}", headers={"Origin": "https://localhost:8443"}
            ),
            403,
            "CSRF_INVALID",
        )
    problem(admin[0].get(f"/api/v1/tasks/{tid}"), 404, "RESOURCE_NOT_FOUND")
    problem(browsers().get(f"/api/v1/tasks/{tid}"), 401, "AUTH_REQUIRED")
    problem(
        admin[0].get("/api/v1/tasks", params={"conversation_id": cid}), 404, "RESOURCE_NOT_FOUND"
    )
    problem(submit(admin, image_request(cid)), 404, "RESOURCE_NOT_FOUND")
    assert admin[0].get("/api/v1/tasks").json()["items"] == []
    problem(user[0].get("/api/v1/admin/tasks"), 403, "FORBIDDEN")
    report = admin[0].get("/api/v1/admin/tasks").json()
    schema("OperationalTaskPage", report)
    assert report["items"][0]["user_id"] == user[2]
    assert "私密合成提示" not in json.dumps(report, ensure_ascii=False)
    assert set(report["items"][0]) == {
        "id",
        "user_id",
        "kind",
        "status",
        "created_at",
        "updated_at",
        "error_code",
    }


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_id": str(uuid4())},
        {"model": "auto"},
        {"first_frame": "http://127.0.0.1/secret"},
        {"prompt": " "},
        {"prompt": "\ud800"},
        {"aspect_ratio": "auto"},
        {"size_tier": "8K"},
        {"target_artifact_id": str(uuid4())},
        {"base_version_id": str(uuid4())},
        {"target_artifact_id": None},
        {"reference_version_ids": [str(uuid4())] * 2},
    ],
)
def test_strict_image_schema(user, media, extra):
    problem(submit(user, image_request(create(user), **extra)), 422, "VALIDATION_ERROR")
    assert media.records() == []


@pytest.mark.parametrize(
    "patch",
    [
        {"mode": "keyframe"},
        {"seconds": 4.1},
        {"seconds": True},
        {"seconds": 13},
        {"seconds": "4"},
        {"reference_confirmation_id": str(uuid4())},
    ],
)
def test_video_mode_schema(user, media, patch):
    request = {
        "conversation_id": create(user),
        "kind": "ai_video",
        "mode": "text",
        "prompt": "合成画面",
        "seconds": 4,
        "size_tier": "720P",
        "aspect_ratio": "9:16",
        **patch,
    }
    problem(submit(user, request), 422, "VALIDATION_ERROR")


def test_reference_and_target_authorization_priority(user, admin, media):
    cid = create(user)
    own, foreign = uploaded(user), uploaded(admin)
    problem(
        submit(user, image_request(cid, reference_version_ids=[foreign["version"]["id"]])),
        404,
        "RESOURCE_NOT_FOUND",
    )
    other_cid = create(user)
    confirmed = confirmation(user, other_cid, own["version"]["id"]).json()["id"]
    request = {
        "conversation_id": cid,
        "kind": "ai_video",
        "mode": "keyframe",
        "prompt": "合成画面",
        "seconds": 4,
        "size_tier": "720P",
        "aspect_ratio": "9:16",
        "reference_confirmation_id": confirmed,
    }
    problem(submit(user, request), 409, "REFERENCE_CONFIRMATION_REQUIRED")
    problem(
        submit(
            user,
            image_request(
                cid,
                target_artifact_id=own["artifact"]["id"],
                base_version_id=foreign["version"]["id"],
            ),
        ),
        404,
        "RESOURCE_NOT_FOUND",
    )
    media.reference_editing_enabled = False
    problem(
        submit(user, image_request(cid, reference_version_ids=[own["version"]["id"]])),
        503,
        "PROVIDER_UNAVAILABLE",
    )
    assert user[0].get("/api/v1/capabilities").json()["image"]["reference_editing_enabled"] is False


def test_versions_busy_base_conflict_and_explicit_regenerate(user, media, database):
    cid = create(user)
    source = uploaded(user)
    request = image_request(
        cid,
        target_artifact_id=source["artifact"]["id"],
        base_version_id=source["version"]["id"],
        reference_version_ids=[source["version"]["id"]],
    )
    task = accepted(user, request)
    problem(submit(user, request), 409, "ARTIFACT_BUSY")
    done = finish(database, media, user, task["id"])
    assert done["status"] == "completed"
    problem(submit(user, request), 409, "VERSION_CONFLICT")
    version = (
        user[0]
        .get(
            f"/api/v1/artifacts/{source['artifact']['id']}/versions/{done['output_version_ids'][0]}"
        )
        .json()
    )
    assert (
        version["parent_version_id"] == source["version"]["id"] and version["version_number"] == 2
    )
    task2 = accepted(user, image_request(cid, regenerate_from_task_id=task["id"]))
    assert task2["id"] != task["id"] and len(media.records()) == 1


def test_cancel_queued_repeated_and_deleted_conversation_tombstone(user, media, database):
    cid, key = create(user), str(uuid4())
    task = accepted(user, image_request(cid), key)
    canceled = action(user, task["id"], "cancel")
    assert canceled.status_code == 200 and canceled.json()["status"] == "canceled"
    assert action(user, task["id"], "cancel").json() == canceled.json()
    problem(action(user, task["id"], "recover"), 409, "RECONCILIATION_REQUIRED")
    assert not TaskWorker(database, media).execute_next()
    assert (
        user[0].delete(f"/api/v1/conversations/{cid}", headers=headers(user[1])).status_code == 204
    )
    problem(submit(user, image_request(cid), key), 410, "RESOURCE_GONE")


@pytest.mark.parametrize(
    "code", ["FREE_ACCESS_UNCONFIRMED", "PROVIDER_LIMIT_REACHED", "PROVIDER_UNAVAILABLE"]
)
def test_provider_gate_before_enqueue_and_dispatch(user, media, database, code):
    cid, key = create(user), str(uuid4())

    def gate(kind):
        fail(503, code, "合成拒绝")

    media.gate = gate
    problem(submit(user, image_request(cid), key), 503, code)
    media.gate = None
    task = accepted(user, image_request(cid), key)
    media.gate = gate
    step(database, media)
    result = get(user, task["id"])
    assert result["status"] == "queued" and result["error"]["code"] == code
    assert media.records() == []
    media.gate = None
    assert finish(database, media, user, task["id"])["status"] == "completed"


def test_tasks_pagination_and_openapi_subset(user, app, media):
    cid = create(user)
    ids = [accepted(user, image_request(cid))["id"] for _ in range(3)]
    first = user[0].get("/api/v1/tasks?limit=2").json()
    schema("TaskPage", first)
    second = (
        user[0].get("/api/v1/tasks", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    )
    assert [t["id"] for t in first["items"] + second["items"]] == list(reversed(ids))
    problem(
        user[0].get("/api/v1/tasks", params={"cursor": first["next_cursor"], "status": "queued"}),
        400,
        "VALIDATION_ERROR",
    )
    actual = app.openapi()
    for path in (
        "/tasks",
        "/tasks/{task_id}",
        "/tasks/{task_id}/cancel",
        "/tasks/{task_id}/recover",
        "/admin/tasks",
    ):
        for method, designed in CONTRACT["paths"][path].items():
            implemented = actual["paths"]["/api/v1" + path][method]
            assert implemented["operationId"] == designed["operationId"]
            assert implemented["security"] == designed["security"]
            for code in ("200", "202"):
                if code in designed["responses"]:
                    assert (
                        implemented["responses"][code]["content"]
                        == designed["responses"][code]["content"]
                    )


def test_default_injection_boundary_and_cleanup_active_stable_file(user, media, database):
    from omniflow.config import Settings
    from omniflow.db import Database

    production = Database(Settings(environment="production", data_dir=database.settings.data_dir))
    with pytest.raises(ValueError):
        TaskService(production, media)
    with pytest.raises(ValueError):
        TaskWorker(production, media)
    assert isinstance(TaskService(database).provider, DisabledMediaProvider)
    task = accepted(user, image_request(create(user)))
    with database.snapshot() as connection:
        vid = connection.execute(
            "SELECT output_version_id FROM tasks WHERE id=?", (task["id"],)
        ).fetchone()[0]
    import os

    path = database.settings.media_dir / (vid + ".blob")
    path.write_bytes(b"synthetic orphan after publish")
    path.chmod(0o600)
    os.utime(path, (1, 1))
    ArtifactService(database).cleanup()
    assert path.exists()
