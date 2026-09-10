"""媒体边界的附加故障、时序与实际 ASGI 检查。"""

import io
import random
from uuid import uuid4

import anyio
import pytest
from PIL import Image
from starlette.requests import ClientDisconnect, Request
from test_artifacts import (
    confirmation,
    delete,
    image_bytes,
    revision,
    saved,
    text,
    upload,
    uploaded,
)
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import headers, post, problem
from test_auth import user as user
from test_conversations import create, send
from test_media_delivery import issued, reserved

from omniflow.api.artifacts import file_response
from omniflow.artifacts import ArtifactService, TaskMediaService, version_owned
from omniflow.problems import ProblemError


def test_upload_without_content_length_has_same_streaming_limit(user, app, database):
    from omniflow.config import Settings
    from omniflow.db import Database

    app.state.artifacts = ArtifactService(
        Database(
            Settings(environment="test", data_dir=database.settings.data_dir, max_upload_bytes=1000)
        )
    )

    def body():
        yield (
            b'--synthetic\r\nContent-Disposition: form-data; name="file"; filename="a"\r\n'
            b"Content-Type: image/png\r\n\r\n"
        )
        yield b"x" * 1001
        yield b"\r\n--synthetic--\r\n"

    response = user[0].post(
        "/api/v1/uploads",
        content=body(),
        headers={
            **headers(user[1]),
            "Idempotency-Key": str(uuid4()),
            "Content-Type": "multipart/form-data; boundary=synthetic",
        },
    )
    problem(response, 413, "UPLOAD_TOO_LARGE")
    assert list(database.settings.media_dir.iterdir()) == []


@pytest.mark.parametrize("field", ["owner_id", "model", "shell", "content_url", "file_path"])
def test_text_writes_reject_injected_fields_and_keep_original(user, field):
    first = saved(user)
    problem(text(user, **{field: "synthetic-secret-input"}), 422, "VALIDATION_ERROR")
    problem(revision(user, first, **{field: "synthetic-secret-input"}), 422, "VALIDATION_ERROR")
    assert user[0].get(first["version"]["content_url"]).text == "这是合成文案。"


def test_text_replay_is_not_blocked_by_newer_version_or_low_disk(user, monkeypatch):
    first = saved(user)
    key = str(uuid4())
    response = revision(user, first, key=key)
    assert response.status_code == 201
    second = response.json()
    assert revision(user, second).status_code == 201
    with monkeypatch.context() as patch:

        def fail_gate(_):
            raise AssertionError("重放不能需要新磁盘空间")

        patch.setattr("omniflow.conversations.ConversationService.storage_gate", fail_gate)
        replay = revision(user, first, key=key)
        assert replay.json() == second and replay.headers["Idempotency-Replayed"] == "true"
    delete(user, first)
    problem(revision(user, first, key=key), 410, "RESOURCE_GONE")


def test_foreign_resource_before_idempotency_and_deleted_reference_before_message_replay(
    user, admin
):
    cid, foreign_cid, key = create(user), create(admin), str(uuid4())
    uploaded(user, key=key, cid=cid)
    problem(upload(user, key=key, cid=foreign_cid), 404, "RESOURCE_NOT_FOUND")
    image = uploaded(user)
    message_key, mid = str(uuid4()), str(uuid4())
    vid = image["version"]["id"]
    assert (
        send(user, cid, key=message_key, client_id=mid, attachment_version_ids=[vid]).status_code
        == 202
    )
    delete(user, image)
    problem(
        send(user, cid, key=message_key, client_id=mid, attachment_version_ids=[vid]),
        404,
        "RESOURCE_NOT_FOUND",
    )


def test_confirmation_only_message_authorizes_exact_version_not_later_message(user, database):
    cid, image, later = create(user), uploaded(user), uploaded(user)
    ref = confirmation(user, cid, image["version"]["id"]).json()
    assert send(user, cid, reference_confirmation_id=ref["id"]).status_code == 202
    assert send(user, cid, attachment_version_ids=[later["version"]["id"]]).status_code == 202
    with database.snapshot() as connection:
        assert ArtifactService(database).authorized_versions(
            connection, user[2], cid, through_seq=1
        ) == [image["version"]]


def test_duplicate_range_headers_rejected_after_ownership(user, admin):
    result = saved(user, content="0123456789")
    hdr = [("Range", "bytes=0-1"), ("Range", "bytes=3-4")]
    response = user[0].get(result["version"]["content_url"], headers=hdr)
    problem(response, 416, "RANGE_NOT_SATISFIABLE")
    assert response.headers["Content-Range"] == "bytes */10"
    problem(admin[0].get(result["version"]["content_url"], headers=hdr), 404, "RESOURCE_NOT_FOUND")


def test_logout_revokes_get_head_and_idempotency_replay(user):
    key = str(uuid4())
    result = uploaded(user, key=key)
    assert post(user[0], "/auth/logout", user[1]).status_code == 204
    problem(user[0].get(result["version"]["content_url"]), 401, "AUTH_REQUIRED")
    head = user[0].head(result["version"]["content_url"], headers={"Range": "bytes=1-2"})
    assert head.status_code == 401 and head.content == b"" and "content-range" not in head.headers
    problem(upload(user, key=key), 401, "AUTH_REQUIRED")


def test_grant_stream_stops_after_task_end(user, database):
    cid = create(user)
    raw = io.BytesIO()
    with Image.frombytes("RGB", (256, 256), random.Random(99).randbytes(256 * 256 * 3)) as image:
        image.save(raw, format="PNG")
    result = uploaded(user, raw=raw.getvalue())
    vid = result["version"]["id"]
    task = reserved(database, user, cid, [vid])
    gid, token, _ = issued(database, task, vid)
    service, grants = ArtifactService(database), TaskMediaService(database)
    request = Request({"type": "http", "method": "GET", "headers": [], "path": "/synthetic"})
    response = file_response(
        request, service, lambda connection: grants.grant_version(connection, gid, token)
    )

    async def consume():
        assert len(await anext(response.body_iterator)) == 65536
        with database.transaction() as connection:
            grants.close(connection, task)
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)

    try:
        anyio.run(consume)
    finally:
        response.stream.close()


def test_file_descriptor_closed_when_response_send_fails_before_first_chunk(user, database):
    result, service = saved(user), ArtifactService(database)
    request = Request({"type": "http", "method": "GET", "headers": [], "path": "/synthetic"})
    response = file_response(
        request,
        service,
        lambda connection: version_owned(connection, result["version"]["id"], user[2]),
    )

    async def disconnect():
        async def send(_):
            raise OSError("synthetic disconnected client")

        async def receive():
            await anyio.sleep_forever()

        # Starlette 将发送失败转换为 ClientDisconnect；finally 仍必须关闭文件。
        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    anyio.run(disconnect)
    assert response.stream.closed


def test_head_error_has_no_body_at_actual_asgi_boundary(app, user):
    image = uploaded(user)
    url = image["version"]["content_url"]
    delete(user, image)

    async def check():
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        cookie = user[0].cookies.get("__Host-omniflow_session")
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "scheme": "https",
                "method": "HEAD",
                "path": url,
                "query_string": b"",
                "root_path": "",
                "server": ("localhost", 8443),
                "client": ("127.0.0.1", 1),
                "headers": [
                    (b"host", b"localhost:8443"),
                    (b"cookie", f"__Host-omniflow_session={cookie}".encode()),
                ],
            },
            receive,
            send,
        )
        assert sent[0]["status"] == 404
        assert all(not item.get("body") for item in sent)

    anyio.run(check)


def test_grant_tokens_never_logged_on_invalid_or_failing_delivery(
    user, database, browsers, caplog, monkeypatch, app
):
    cid, image = create(user), uploaded(user)
    task = reserved(database, user, cid, [image["version"]["id"]])
    _, token, url = issued(database, task, image["version"]["id"])
    anonymous = browsers()
    problem(anonymous.get(url + "invalid"), 404, "RESOURCE_NOT_FOUND")

    def broken(_):
        raise RuntimeError("synthetic error with " + token)

    monkeypatch.setattr(app.state.artifacts.storage, "open", broken)
    response = anonymous.get(url)
    problem(response, 500, "INTERNAL_ERROR")
    assert token not in caplog.text and token not in response.text


@pytest.mark.parametrize("identity", ["invalid", "..%5Csecret", "not-a-uuid"])
def test_malformed_grant_identity_has_same_not_found_response(browsers, identity):
    client = browsers()
    problem(
        client.get(f"/api/v1/media-grants/{identity}/content?token=synthetic"),
        404,
        "RESOURCE_NOT_FOUND",
    )
    response = client.head(f"/api/v1/media-grants/{identity}/content?token=synthetic")
    assert response.status_code == 404 and response.content == b""


def test_append_version_cannot_resurrect_deleted_artifact(user, database):
    from omniflow.media_storage import inspect_image

    image = uploaded(user)
    delete(user, image)
    with database.transaction() as connection, pytest.raises(ProblemError) as error:
        ArtifactService.append_version(
            connection,
            image["artifact"]["id"],
            str(uuid4()),
            inspect_image(image_bytes(), "image/png", database.settings),
            parent=image["version"]["id"],
        )
    assert error.value.status == 404
