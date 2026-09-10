"""短期媒体授权、任务占用桥接、清理与授权竞争的离线故障注入。"""

import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from test_artifacts import confirmation, delete, image_bytes, revision, saved, upload, uploaded
from test_auth import NEW_PASSWORD, headers, post, problem, token_from
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import context, create

from omniflow.artifacts import ArtifactService, TaskMediaService, confirmation_owned, version_owned
from omniflow.auth_security import expiry, now
from omniflow.media_storage import inspect_image
from omniflow.problems import ProblemError


def reserved(database, actor, cid, inputs=(), target=None):
    identity = str(uuid4())
    with database.transaction() as connection:
        TaskMediaService.reserve(
            connection,
            task_id=identity,
            owner=actor[2],
            cid=cid,
            input_version_ids=inputs,
            target_version_id=target,
        )
    return identity


def issued(database, task_id, vid):
    service = TaskMediaService(database)
    with database.transaction() as connection:
        service.activate(connection, task_id)
        gid, token = service.issue(connection, task_id, vid)
    return gid, token, f"/api/v1/media-grants/{gid}/content?token={token}"


def test_grant_requires_trusted_scope_activation_and_exact_image_input(user, database, browsers):
    cid, image, other, prose = create(user), uploaded(user), uploaded(user), saved(user)
    vid = image["version"]["id"]
    task = reserved(database, user, cid, [vid])
    service = TaskMediaService(database)
    with pytest.raises(ProblemError) as error, database.transaction() as connection:
        service.issue(connection, task, vid)
    assert error.value.status == 404  # 排队期不能签发。
    gid, token, url = issued(database, task, vid)
    anonymous = browsers()
    for _ in range(2):
        get, head = anonymous.get(url), anonymous.head(url)
        assert get.status_code == head.status_code == 200
        assert get.content == image_bytes() and head.content == b""
        assert get.headers["content-length"] == head.headers["content-length"]
        assert get.headers["cache-control"] == "private, no-store"
    for item in (other, prose):
        with pytest.raises(ProblemError), database.transaction() as connection:
            service.issue(connection, task, item["version"]["id"])
    for bad in ("", "synthetic-invalid", "x" * 43, "%ED%A0%80", token + "&token=" + token):
        problem(
            anonymous.get(f"/api/v1/media-grants/{gid}/content?token={bad}"),
            404,
            "RESOURCE_NOT_FOUND",
        )
    problem(anonymous.get(f"/api/v1/media-grants/{gid}/content"), 404, "RESOURCE_NOT_FOUND")
    problem(
        anonymous.get(f"/api/v1/media-grants/{uuid4()}/content?token={token}"),
        404,
        "RESOURCE_NOT_FOUND",
    )
    with database.connect(readonly=True) as connection:
        rows = {
            table: [dict(r) for r in connection.execute(f"SELECT * FROM {table}")]
            for table in ("media_grants", "task_media_scopes", "artifact_idempotency", "events")
        }
    assert token not in json.dumps(rows) and url not in json.dumps(rows)
    assert user[0].post(f"/api/v1/media-grants/{gid}", headers=headers(user[1])).status_code == 404
    # 无登录的特殊授权不能反过来读取普通作品元数据。
    problem(
        anonymous.get(f"/api/v1/artifacts/{image['artifact']['id']}?token={token}"),
        401,
        "AUTH_REQUIRED",
    )


@pytest.mark.parametrize(
    "cause", ["expiry", "revoked", "finished", "deadline", "disabled", "reset"]
)
def test_grant_revocation_persists_across_new_service_and_reenable(
    user, admin, browsers, database, cause
):
    cid, image = create(user), uploaded(user)
    vid = image["version"]["id"]
    task = reserved(database, user, cid, [vid])
    gid, token, url = issued(database, task, vid)
    anonymous = browsers()
    assert anonymous.get(url).status_code == 200
    if cause in ("expiry", "revoked", "finished", "deadline"):
        with database.transaction() as connection:
            if cause == "expiry":
                connection.execute(
                    "UPDATE media_grants SET expires_at=? WHERE id=?", (expiry(-1), gid)
                )
            elif cause == "revoked":
                connection.execute("UPDATE media_grants SET revoked_at=? WHERE id=?", (now(), gid))
            elif cause == "deadline":
                connection.execute(
                    "UPDATE task_media_scopes SET grant_deadline=? WHERE task_id=?",
                    (expiry(-1), task),
                )
            else:
                TaskMediaService.close(connection, task)
    elif cause == "disabled":
        for state in ("disabled", "active"):
            response = admin[0].patch(
                f"/api/v1/admin/users/{user[2]}", json={"status": state}, headers=headers(admin[1])
            )
            assert response.status_code == 200
    else:
        issued_reset = post(
            admin[0],
            f"/admin/users/{user[2]}/password-resets",
            admin[1],
            {"verification_method": "in_person"},
        )
        assert issued_reset.status_code == 201, issued_reset.text
        from test_auth import csrf

        response = post(
            anonymous,
            "/auth/password-resets/complete",
            csrf(anonymous),
            {
                "reset_token": token_from(issued_reset.json()["reset_url"]),
                "new_password": NEW_PASSWORD,
            },
        )
        assert response.status_code == 204, response.text
    problem(anonymous.get(url), 404, "RESOURCE_NOT_FOUND")
    head = anonymous.head(url)
    assert head.status_code == 404 and head.content == b""
    assert "content-range" not in head.headers and token not in str(head.headers)
    with database.snapshot() as connection, pytest.raises(ProblemError):
        TaskMediaService(database).grant_version(connection, gid, token)


def test_grant_renewal_cannot_extend_task_window(user, database):
    cid, image = create(user), uploaded(user)
    vid = image["version"]["id"]
    task = reserved(database, user, cid, [vid])
    service = TaskMediaService(database)
    with database.transaction() as connection:
        service.activate(connection, task)
        deadline = expiry(2)
        connection.execute(
            "UPDATE task_media_scopes SET grant_deadline=? WHERE task_id=?", (deadline, task)
        )
        service.activate(connection, task)
        ids = [service.issue(connection, task, vid)[0] for _ in range(2)]
        assert (
            connection.execute(
                "SELECT grant_deadline FROM task_media_scopes WHERE task_id=?", (task,)
            ).fetchone()[0]
            == deadline
        )
        assert {r[0] for r in connection.execute("SELECT expires_at FROM media_grants")} == {
            deadline
        }
        assert len(set(ids)) == 2


def test_input_and_target_occupancy_blocks_delete_and_competing_edit(user, database):
    cid, image, prose = create(user), uploaded(user), saved(user)
    task = reserved(database, user, cid, [image["version"]["id"]], prose["version"]["id"])
    for item in (image, prose):
        problem(delete(user, item), 409, "RESOURCE_IN_USE")
        assert user[0].get(item["version"]["content_url"]).status_code == 200
    problem(revision(user, prose), 409, "ARTIFACT_BUSY")
    problem(
        user[0].delete(f"/api/v1/conversations/{cid}", headers=headers(user[1])),
        409,
        "RESOURCE_IN_USE",
    )
    with pytest.raises(ProblemError) as error:
        reserved(database, user, cid, target=prose["version"]["id"])
    assert error.value.code == "ARTIFACT_BUSY"
    with database.transaction() as connection:
        TaskMediaService.close(connection, task)
    assert delete(user, image).status_code == 202
    assert revision(user, prose).status_code == 201
    # 已结束派生结果不会被原图删除连带删除。
    assert user[0].get(prose["version"]["content_url"]).status_code == 200
    assert (
        user[0].delete(f"/api/v1/conversations/{cid}", headers=headers(user[1])).status_code == 204
    )


def test_delete_and_task_reservation_race_is_atomic(user, database):
    cid, image = create(user), uploaded(user)
    barrier = threading.Barrier(2)

    def deleting():
        barrier.wait(timeout=5)
        try:
            ArtifactService(database).delete(image["artifact"]["id"], context(user[0]))
            return "deleted"
        except ProblemError as exc:
            return exc.code

    def occupying():
        barrier.wait(timeout=5)
        try:
            reserved(database, user, cid, [image["version"]["id"]])
            return "reserved"
        except ProblemError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(deleting), pool.submit(occupying)
        outcomes = (a.result(), b.result())
    assert outcomes in (("deleted", "RESOURCE_NOT_FOUND"), ("RESOURCE_IN_USE", "reserved"))


def test_new_image_version_does_not_rebind_existing_confirmation_or_grant(user, database):
    cid, image = create(user), uploaded(user)
    aid, vid = image["artifact"]["id"], image["version"]["id"]
    ref = confirmation(user, cid, vid).json()
    task = reserved(database, user, cid, [vid], vid)
    _, _, url = issued(database, task, vid)
    service = ArtifactService(database)
    raw, new_vid = image_bytes(color="red"), str(uuid4())
    info = inspect_image(raw, "image/png", database.settings)
    # 只模拟 worker 已保存文件时的本地终态事务，不执行真实生成。
    with service.storage.stage(raw) as stage, database.transaction() as connection:
        service.storage.publish(stage, new_vid)
        service.append_version(
            connection, aid, new_vid, info, parent=vid, task_id=task, engine="agnes-image-2.5-flash"
        )
    assert user[0].get(url).content == image_bytes()
    with database.snapshot() as connection:
        assert confirmation_owned(connection, ref["id"], user[2], cid)["version_id"] == vid
        assert version_owned(connection, new_vid, user[2])["parent_version_id"] == vid
    with database.transaction() as connection:
        TaskMediaService.close(connection, task)
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        service.append_version(
            connection,
            aid,
            str(uuid4()),
            info,
            parent=new_vid,
            task_id=task,
            engine="agnes-image-2.5-flash",
        )
    assert user[0].get(f"/api/v1/artifacts/{aid}").json()["version_count"] == 2
    problem(user[0].get(url), 404, "RESOURCE_NOT_FOUND")
    assert user[0].get(image["version"]["content_url"]).content == image_bytes()


def test_explicit_purge_retains_tombstones_and_live_artifacts(user, database):
    first, live = uploaded(user), saved(user)
    service = ArtifactService(database)
    delete(user, first)
    assert service.cleanup() == 0
    with database.transaction() as connection:
        connection.execute(
            "UPDATE artifacts SET purge_target_at=? WHERE id=?",
            (expiry(-1), first["artifact"]["id"]),
        )
    assert service.cleanup() == 1 and service.cleanup() == 0
    assert not service.storage.path(first["version"]["id"]).exists()
    assert user[0].get(live["version"]["content_url"]).status_code == 200
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_upload_rollback_orphans_not_visible_retry_and_maintenance_are_safe(
    user, app, database, monkeypatch
):
    service = app.state.artifacts
    original = service.record

    def fail_record(*args):
        raise RuntimeError("synthetic failure containing a pretend secret")

    monkeypatch.setattr(service, "record", fail_record)
    key = str(uuid4())
    problem(upload(user, key=key), 500, "INTERNAL_ERROR")
    with database.connect(readonly=True) as connection:
        for table in ("artifacts", "artifact_versions", "artifact_idempotency"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    orphan = next(database.settings.media_dir.glob("*.blob"))
    assert not list(database.settings.media_dir.glob("*.part"))
    monkeypatch.setattr(service, "record", original)
    result = uploaded(user, key=key)
    assert result["version"]["id"] != orphan.stem
    # 活跃临时文件即使很老也不能被维护误删。
    with service.storage.stage(b"synthetic active stage") as active:
        old = time.time() - 7200
        os.utime(active, (old, old))
        os.utime(orphan, (old, old))
        service.cleanup()
        assert active.exists() and not orphan.exists()
    assert len(list(database.settings.media_dir.iterdir())) == 1


def test_storage_failure_and_low_disk_do_not_consume_key_or_delete_old_content(
    user, app, monkeypatch
):
    import omniflow.conversations as conversations

    result = saved(user)
    key = str(uuid4())
    with monkeypatch.context() as patch:
        patch.setattr(conversations.shutil, "disk_usage", lambda _: type("Disk", (), {"free": 0})())
        problem(upload(user, key=key), 507, "STORAGE_UNAVAILABLE")
        assert user[0].get(result["version"]["content_url"]).status_code == 200
        assert user[0].head(result["version"]["content_url"]).status_code == 200
    with monkeypatch.context() as patch:

        def broken(*args):
            raise OSError("synthetic disk full with secret path")

        patch.setattr(app.state.artifacts.storage, "publish", broken)
        problem(upload(user, key=key), 507, "STORAGE_UNAVAILABLE")
    uploaded(user, key=key)


def test_symlink_and_missing_file_fail_closed_without_host_path(user, database, tmp_path):
    image = uploaded(user)
    path = ArtifactService(database).storage.path(image["version"]["id"])
    backup = tmp_path / "synthetic-only.txt"
    backup.write_text("Synthetic unrelated secret")
    path.unlink()
    path.symlink_to(backup)
    for method in ("get", "head"):
        response = getattr(user[0], method)(image["version"]["content_url"])
        assert response.status_code == 503
        assert (
            str(backup) not in response.text and "Synthetic unrelated secret" not in response.text
        )
    path.unlink()
    problem(user[0].get(image["version"]["content_url"]), 503, "STORAGE_UNAVAILABLE")
    assert backup.read_text() == "Synthetic unrelated secret"


def test_identity_revoked_during_staging_cannot_commit(user, app, database, monkeypatch):
    from contextlib import contextmanager

    service = app.state.artifacts
    original = service.storage.stage

    @contextmanager
    def revoke_after_stage(content):
        with original(content) as stage:
            with database.transaction() as connection:
                connection.execute("DELETE FROM login_sessions WHERE user_id=?", (user[2],))
            yield stage

    monkeypatch.setattr(service.storage, "stage", revoke_after_stage)
    problem(upload(user), 401, "AUTH_REQUIRED")
    assert list(database.settings.media_dir.iterdir()) == []


def test_grant_and_artifact_stream_stop_after_revocation(user, database, browsers):
    from starlette.requests import Request

    from omniflow.api.artifacts import file_response

    service = ArtifactService(database)
    # 纯本地 text 足够覆盖分块；不会把流停止说成已撤回之前交付的副本。
    result = saved(user, content="汉" * 50000)

    def authorize(connection):
        from omniflow.auth_security import require_session

        owner = require_session(connection, context(user[0]))["id"]
        return version_owned(connection, result["version"]["id"], owner)

    request = Request({"type": "http", "method": "GET", "headers": [], "path": "/synthetic"})
    response = file_response(request, service, authorize)

    async def consume():
        first = await anext(response.body_iterator)
        assert len(first) == 65536
        assert delete(user, result).status_code == 202
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)

    import anyio

    try:
        anyio.run(consume)
    finally:
        response.stream.close()


def test_migration_from_v4_preserves_all_history_and_accounts(settings, monkeypatch):
    from omniflow import db

    database = db.Database(settings)
    migrations = db.MIGRATIONS
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", migrations[:4])
        assert database.migrate() == 4
    from omniflow.auth_service import AuthService

    user_id = str(AuthService(database).create_admin("synthetic", "Synthetic Password 123!").id)
    with database.connect(readonly=True) as connection:
        old_history = [tuple(r) for r in connection.execute("SELECT * FROM schema_migrations")]
        old_user = dict(connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    assert database.migrate() == migrations[-1].version
    assert database.migrate() == migrations[-1].version
    with database.connect(readonly=True) as connection:
        assert [tuple(r) for r in connection.execute("SELECT * FROM schema_migrations")][
            :4
        ] == old_history
        assert (
            dict(connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
            == old_user
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
