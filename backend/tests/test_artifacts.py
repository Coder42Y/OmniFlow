"""阶段 03：真实 FastAPI/SQLite/合成图片，不调用真实媒体提供方。"""

import hashlib
import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from PIL import Image
from test_auth import CONTRACT, headers, problem, schema
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import context, create, send, snapshot

from omniflow.artifact_models import TextVersionCreate
from omniflow.artifacts import ArtifactService, confirmation_owned
from omniflow.problems import ProblemError


def image_bytes(fmt="PNG", size=(12, 8), color="blue"):
    output = io.BytesIO()
    with Image.new("RGB", size, color) as image:
        image.save(output, format=fmt)
    return output.getvalue()


def upload(actor, *, raw=None, mime="image/png", name="合成图片.png", cid=None, key=None):
    return actor[0].post(
        "/api/v1/uploads",
        files={"file": (name, image_bytes() if raw is None else raw, mime)},
        data={"conversation_id": cid} if cid else {},
        headers={**headers(actor[1]), "Idempotency-Key": key or str(uuid4())},
    )


def uploaded(actor, **kwargs):
    response = upload(actor, **kwargs)
    assert response.status_code == 201, response.text
    schema("ArtifactCreated", response.json())
    return response.json()


def text(actor, *, content="这是合成文案。", key=None, **extra):
    return actor[0].post(
        "/api/v1/artifacts",
        json={"kind": "text", "title": "合成标题", "content": content, **extra},
        headers={**headers(actor[1]), "Idempotency-Key": key or str(uuid4())},
    )


def saved(actor, **kwargs):
    response = text(actor, **kwargs)
    assert response.status_code == 201, response.text
    schema("ArtifactCreated", response.json())
    return response.json()


def revision(actor, artifact, *, content="修改合成文案", key=None, **extra):
    return actor[0].post(
        f"/api/v1/artifacts/{artifact['artifact']['id']}/text-versions",
        json={"base_version_id": artifact["version"]["id"], "content": content, **extra},
        headers={**headers(actor[1]), "Idempotency-Key": key or str(uuid4())},
    )


def confirmation(actor, cid, vid, *, key=None, **extra):
    return actor[0].post(
        f"/api/v1/conversations/{cid}/reference-confirmations",
        json={"version_id": vid, "purpose": "video_first_frame", **extra},
        headers={**headers(actor[1]), "Idempotency-Key": key or str(uuid4())},
    )


def delete(actor, result):
    return actor[0].delete(
        f"/api/v1/artifacts/{result['artifact']['id']}", headers=headers(actor[1])
    )


@pytest.mark.parametrize(
    "fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_upload_decodes_metadata_preserves_original_bytes(user, database, fmt, mime):
    cid = create(user)
    raw = image_bytes(fmt)
    result = uploaded(user, raw=raw, mime=mime, cid=cid)
    version = result["version"]
    assert version["width"] == 12 and version["height"] == 8
    assert version["media_type"] == mime
    assert version["sha256"] == hashlib.sha256(raw).hexdigest()
    assert version["byte_size"] == len(raw)
    assert version["execution_engine"] is None and version["source_task_id"] is None
    assert user[0].get(version["content_url"]).content == raw
    assert str(database.settings.data_dir) not in json.dumps(result)
    files = list(database.settings.media_dir.iterdir())
    assert len(files) == 1 and files[0].name == version["id"] + ".blob"
    assert files[0].stat().st_mode & 0o077 == 0
    with database.connect(readonly=True) as connection:
        event = json.loads(
            connection.execute(
                "SELECT payload FROM events WHERE conversation_id=?", (cid,)
            ).fetchone()[0]
        )
    schema("EventArtifactReady", event)
    assert event["data"] == version


def test_text_versions_are_immutable_and_no_model_claims(user, database):
    cid, key = create(user), str(uuid4())
    first = saved(user, conversation_id=cid, content="第一版 😀\n末行", key=key)
    second_response = revision(user, first, conversation_id=cid)
    assert second_response.status_code == 201, second_response.text
    second = second_response.json()
    schema("ArtifactCreated", second)
    assert second["artifact"]["id"] == first["artifact"]["id"]
    assert second["version"]["parent_version_id"] == first["version"]["id"]
    assert second["artifact"]["version_count"] == second["version"]["version_number"] == 2
    assert user[0].get(first["version"]["content_url"]).text == "第一版 😀\n末行"
    replay = text(user, conversation_id=cid, content="第一版 😀\n末行", key=key)
    assert replay.json() == first and replay.headers["Idempotency-Replayed"] == "true"
    for statement in (
        "UPDATE artifact_versions SET sha256='bad' WHERE id=?",
        "DELETE FROM artifact_versions WHERE id=?",
    ):
        with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
            connection.execute(statement, (first["version"]["id"],))
    assert len(list(database.settings.media_dir.glob("*.blob"))) == 2
    problem(revision(user, first), 409, "VERSION_CONFLICT")
    assert user[0].get(second["version"]["content_url"]).text == "修改合成文案"


def test_version_parent_and_artifact_path_must_match(user, admin):
    first, other, foreign = saved(user), saved(user), saved(admin)
    aid = first["artifact"]["id"]
    for item in (other, foreign):
        path = f"/api/v1/artifacts/{aid}/versions/{item['version']['id']}"
        problem(user[0].get(path), 404, "RESOURCE_NOT_FOUND")
        problem(
            user[0].get(path + "/content", headers={"Range": "bytes=999999-"}),
            404,
            "RESOURCE_NOT_FOUND",
        )
        response = user[0].head(path + "/content")
        assert response.status_code == 404 and response.content == b""
        assert "content-range" not in response.headers
        problem(
            revision(user, {"artifact": first["artifact"], "version": item["version"]}),
            404,
            "RESOURCE_NOT_FOUND",
        )


@pytest.mark.parametrize("actor_name", ["admin", "anonymous"])
def test_all_private_artifact_routes_reject_other_identity(user, admin, browsers, actor_name):
    result = uploaded(user)
    aid, vid = result["artifact"]["id"], result["version"]["id"]
    actor = admin if actor_name == "admin" else (browsers(), "invalid", "none")
    code, status = ("RESOURCE_NOT_FOUND", 404) if actor_name == "admin" else ("AUTH_REQUIRED", 401)
    for path in (
        f"/artifacts/{aid}",
        f"/artifacts/{aid}/versions",
        f"/artifacts/{aid}/versions/{vid}",
        f"/artifacts/{aid}/versions/{vid}/content",
    ):
        response = actor[0].get("/api/v1" + path, headers={"Range": "bytes=999999-"})
        problem(response, status, code)
        assert "content-range" not in response.headers
    response = actor[0].head(result["version"]["content_url"], headers={"Range": "invalid"})
    assert response.status_code == status and response.content == b""
    assert "content-range" not in response.headers
    if actor_name == "admin":
        problem(delete(actor, result), 404, "RESOURCE_NOT_FOUND")
        problem(revision(actor, result), 404, "RESOURCE_NOT_FOUND")
        assert actor[0].get("/api/v1/artifacts").json()["items"] == []


@pytest.mark.parametrize(
    "name",
    [
        "../../server.py",
        "..\\..\\secret.txt",
        "/etc/passwd",
        '"\r\nX-Injected: true.png',
        "<script>.svg",
    ],
)
def test_untrusted_filename_never_used_as_path_or_headers(user, database, name):
    result = uploaded(user, name=name)
    response = user[0].get(result["version"]["content_url"] + "?download=true")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{result["version"]["id"]}.png"'
    )
    assert "x-injected" not in response.headers
    assert [p.name for p in database.settings.media_dir.iterdir()] == [
        result["version"]["id"] + ".blob"
    ]


@pytest.mark.parametrize(
    "raw,mime,status,code",
    [
        (b"<svg><script>alert(1)</script></svg>", "image/svg+xml", 415, "UNSUPPORTED_MEDIA_TYPE"),
        (b"<html>synthetic</html>", "text/html", 415, "UNSUPPORTED_MEDIA_TYPE"),
        (b"not an image", "image/png", 422, "IMAGE_DECODE_FAILED"),
        (b"", "image/png", 422, "IMAGE_DECODE_FAILED"),
        (b"GIF89a", "image/gif", 415, "UNSUPPORTED_MEDIA_TYPE"),
        (b"synthetic video", "video/mp4", 415, "UNSUPPORTED_MEDIA_TYPE"),
        (image_bytes(), "image/jpeg", 415, "UNSUPPORTED_MEDIA_TYPE"),
        (image_bytes()[:45], "image/png", 422, "IMAGE_DECODE_FAILED"),
    ],
)
def test_bad_images_rejected_without_artifact_or_key(user, database, raw, mime, status, code):
    key = str(uuid4())
    problem(upload(user, raw=raw, mime=mime, key=key), status, code)
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM artifact_idempotency").fetchone()[0] == 0
    assert list(database.settings.media_dir.iterdir()) == []
    uploaded(user, key=key)


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("WEBP", "image/webp")])
def test_animated_images_rejected(user, fmt, mime):
    output = io.BytesIO()
    with Image.new("RGB", (8, 8), "red") as a, Image.new("RGB", (8, 8), "blue") as b:
        a.save(output, format=fmt, save_all=True, append_images=[b], duration=100, loop=0)
    problem(upload(user, raw=output.getvalue(), mime=mime), 415, "UNSUPPORTED_MEDIA_TYPE")


def test_upload_pixel_and_byte_limits_are_real_and_reported(user, app, database):
    from omniflow.config import Settings
    from omniflow.db import Database

    limited = Settings(
        environment="test",
        data_dir=database.settings.data_dir,
        max_upload_bytes=1000,
        max_image_pixels=50,
    )
    app.state.artifacts = ArtifactService(Database(limited))
    capabilities = user[0].get("/api/v1/capabilities")
    schema("Capabilities", capabilities.json())
    assert capabilities.json()["limits"]["max_upload_bytes"] == 1000
    assert capabilities.json()["limits"]["max_image_pixels"] == 50
    assert capabilities.json()["text"]["model"] == "gemini-3.8-flash-low"
    assert capabilities.json()["text"]["availability"]["available"] is False
    problem(upload(user), 422, "IMAGE_DECODE_FAILED")
    problem(upload(user, raw=b"x" * 1001), 413, "UPLOAD_TOO_LARGE")
    problem(upload(user, raw=b"x" * 18000), 413, "UPLOAD_TOO_LARGE")
    uploaded(user, raw=image_bytes(size=(5, 5)))


def test_large_valid_upload_is_not_limited_by_json_body_budget(user):
    # 上传继续使用独立的图片上限，不能被普通 JSON 的 1MiB 保护误伤。
    output = io.BytesIO()
    with Image.new("RGB", (1024, 512), "blue") as image:
        image.save(output, format="PNG", compress_level=0)
    raw = output.getvalue()
    assert 1024 * 1024 < len(raw) < 20 * 1024 * 1024
    result = uploaded(user, raw=raw)
    assert result["version"]["byte_size"] == len(raw)
    assert user[0].get(result["version"]["content_url"]).content == raw


def test_multipart_extra_duplicate_truncated_and_missing_fields_rejected(user):
    hdr = {**headers(user[1]), "Idempotency-Key": str(uuid4())}
    for files, data in [
        (
            [
                ("file", ("a", image_bytes(), "image/png")),
                ("file", ("b", image_bytes(), "image/png")),
            ],
            {},
        ),
        ({"file": ("a", image_bytes(), "image/png")}, {"owner_id": str(uuid4())}),
        ({"file": ("a", image_bytes(), "image/png")}, {"conversation_id": "../bad"}),
        ({"conversation_id": ("a", image_bytes(), "image/png")}, {}),
        (
            {"file": ("a", image_bytes(), "image/png")},
            {"conversation_id": str(uuid4()), "x": "bad"},
        ),
    ]:
        response = user[0].post("/api/v1/uploads", files=files, data=data, headers=hdr)
        problem(response, 422, "VALIDATION_ERROR")
    raw = (
        b'--synthetic\r\nContent-Disposition: form-data; name="file"; filename="a"\r\n'
        b"Content-Type: image/png\r\n\r\n"
    )
    response = user[0].post(
        "/api/v1/uploads",
        content=raw + image_bytes(),
        headers={**hdr, "Content-Type": "multipart/form-data; boundary=synthetic"},
    )
    problem(response, 422, "VALIDATION_ERROR")
    uploaded(user, key=hdr["Idempotency-Key"])


def test_upload_auth_csrf_checked_before_parsing(user, browsers, database):
    for client, hdr, status, code in [
        (browsers(), headers("x"), 401, "AUTH_REQUIRED"),
        (user[0], headers("x"), 403, "CSRF_INVALID"),
    ]:
        response = client.post(
            "/api/v1/uploads",
            content=b"not multipart",
            headers={**hdr, "Idempotency-Key": str(uuid4()), "Content-Type": "multipart/form-data"},
        )
        problem(response, status, code)
    assert list(database.settings.media_dir.iterdir()) == []


def test_upload_idempotency_ignores_filename_and_boundary_but_not_bytes_or_owner(user, admin):
    key = str(uuid4())
    first = uploaded(user, key=key)
    replay = upload(user, key=key, name="completely-different.jpg")
    assert replay.status_code == 201 and replay.json() == first
    assert replay.headers["Idempotency-Replayed"] == "true"
    problem(upload(user, key=key, raw=image_bytes(color="red")), 409, "IDEMPOTENCY_CONFLICT")
    assert uploaded(admin, key=key)["artifact"]["id"] != first["artifact"]["id"]
    assert user[0].get("/api/v1/artifacts").json()["items"] == [first["artifact"]]


def test_upload_concurrent_replay_creates_one_file_one_version(user, app, database):
    barrier = threading.Barrier(6)
    ctx, content = context(user[0]), image_bytes()

    def action(_):
        barrier.wait(timeout=5)
        return app.state.artifacts.upload(content, "image/png", None, "upload-race-key", ctx)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(action, range(6)))
    assert sum(not replay for _, replay in results) == 1
    assert len({r[0]["artifact"]["id"] for r in results}) == 1
    assert len(list(database.settings.media_dir.iterdir())) == 1
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM artifact_idempotency").fetchone()[0] == 1


def test_concurrent_text_revisions_one_wins_without_overwrite(user, app, database):
    first = saved(user)
    barrier, ctx = threading.Barrier(2), context(user[0])

    def action(index):
        barrier.wait(timeout=5)
        try:
            return app.state.artifacts.save_text(
                TextVersionCreate(
                    content=f"revision {index}", base_version_id=first["version"]["id"]
                ),
                f"revision-{index}",
                ctx,
                first["artifact"]["id"],
            )[0]
        except ProblemError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(action, range(2)))
    assert sum(item == "VERSION_CONFLICT" for item in results) == 1
    assert len(list(database.settings.media_dir.iterdir())) == 2
    assert user[0].get(first["version"]["content_url"]).text == "这是合成文案。"


@pytest.mark.parametrize(
    "range_value,status,expected",
    [
        (None, 200, b"0123456789"),
        ("bytes=2-5", 206, b"2345"),
        ("bytes=7-", 206, b"789"),
        ("bytes=-3", 206, b"789"),
        ("bytes=-999", 206, b"0123456789"),
        ("bytes=0-999", 206, b"0123456789"),
        ("bytes=9-9", 206, b"9"),
        ("bytes=10-", 416, None),
        ("bytes=-0", 416, None),
        ("bytes=5-2", 416, None),
        ("bytes=0-1,3-4", 416, None),
        ("bytes=-", 416, None),
        ("other=0-1", 416, None),
        ("bytes=" + "9" * 1000 + "-", 416, None),
    ],
)
def test_get_single_ranges_and_head_ignores_range(user, range_value, status, expected):
    result = saved(user, content="0123456789")
    url = result["version"]["content_url"]
    hdr = {} if range_value is None else {"Range": range_value}
    response = user[0].get(url, headers=hdr)
    assert response.status_code == status, response.text
    if expected is not None:
        assert response.content == expected
        assert int(response.headers["Content-Length"]) == len(expected)
        assert response.headers["Accept-Ranges"] == "bytes"
        if status == 206:
            assert response.headers["Content-Range"].endswith("/10")
    else:
        problem(response, 416, "RANGE_NOT_SATISFIABLE")
        assert response.headers["Content-Range"] == "bytes */10"
    head = user[0].head(url, headers=hdr)
    assert head.status_code == 200 and head.content == b""
    assert head.headers["Content-Length"] == "10" and "Content-Range" not in head.headers


def test_pagination_filters_and_cursors_remain_owner_bound(user, admin):
    a, b = create(user), create(user)
    items = [saved(user, conversation_id=a), uploaded(user, cid=b), saved(user, conversation_id=a)]
    listed = user[0].get("/api/v1/artifacts?limit=1").json()
    schema("ArtifactPage", listed)
    assert listed["items"] == [items[-1]["artifact"]]
    cursor = listed["next_cursor"]
    second = user[0].get("/api/v1/artifacts", params={"limit": 1, "cursor": cursor}).json()
    assert second["items"] == [items[1]["artifact"]]
    problem(
        user[0].get("/api/v1/artifacts", params={"cursor": cursor, "kind": "text"}),
        400,
        "VALIDATION_ERROR",
    )
    problem(admin[0].get("/api/v1/artifacts", params={"cursor": cursor}), 400, "VALIDATION_ERROR")
    problem(
        admin[0].get("/api/v1/artifacts", params={"conversation_id": a}), 404, "RESOURCE_NOT_FOUND"
    )
    assert len(user[0].get("/api/v1/artifacts", params={"conversation_id": a}).json()["items"]) == 2
    revision(user, items[0])
    path = f"/api/v1/artifacts/{items[0]['artifact']['id']}/versions"
    page = user[0].get(path, params={"limit": 1}).json()
    schema("ArtifactVersionPage", page)
    assert page["items"][0]["version_number"] == 2
    older = user[0].get(path, params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert older["items"] == [items[0]["version"]] and older["next_cursor"] is None


def test_confirmation_explicit_selection_and_snapshot_do_not_grant_other_conversation(
    user, admin, database
):
    a, b = create(user), create(user)
    image, other, prose = uploaded(user, cid=a), uploaded(user), saved(user)
    vid = image["version"]["id"]
    key = str(uuid4())
    confirmed = confirmation(user, b, vid, key=key)
    assert confirmed.status_code == 201, confirmed.text
    schema("ReferenceConfirmation", confirmed.json())
    assert confirmation(user, b, vid, key=key).json() == confirmed.json()
    problem(confirmation(user, b, other["version"]["id"], key=key), 409, "IDEMPOTENCY_CONFLICT")
    problem(confirmation(admin, create(admin), vid), 404, "RESOURCE_NOT_FOUND")
    problem(confirmation(user, b, prose["version"]["id"]), 422, "VALIDATION_ERROR")
    problem(confirmation(user, b, vid, confirmed=True), 422, "VALIDATION_ERROR")
    problem(
        send(user, a, selected_version_id=vid, reference_confirmation_id=confirmed.json()["id"]),
        409,
        "REFERENCE_CONFIRMATION_REQUIRED",
    )
    problem(
        send(
            user,
            b,
            selected_version_id=other["version"]["id"],
            reference_confirmation_id=confirmed.json()["id"],
        ),
        409,
        "REFERENCE_CONFIRMATION_REQUIRED",
    )
    response = send(
        user,
        b,
        attachment_version_ids=[vid],
        selected_version_id=vid,
        reference_confirmation_id=confirmed.json()["id"],
    )
    assert response.status_code == 202, response.text
    assert snapshot(user, b)["artifact_versions"] == [image["version"]]
    with database.snapshot() as connection:
        visible = ArtifactService(database).authorized_versions(
            connection, user[2], b, through_seq=1
        )
        assert visible == [image["version"]]
        confirmation_owned(connection, confirmed.json()["id"], user[2], b, vid)
    # 后排显式引用不能经新的全局关联表提前进入前一轮。
    assert send(user, b, attachment_version_ids=[other["version"]["id"]]).status_code == 202
    with database.snapshot() as connection:
        assert ArtifactService(database).authorized_versions(
            connection, user[2], b, through_seq=1
        ) == [image["version"]]
    assert delete(user, image).status_code == 202
    assert [v["id"] for v in snapshot(user, b)["artifact_versions"]] == [other["version"]["id"]]
    problem(confirmation(user, b, vid, key=key), 410, "RESOURCE_GONE")
    problem(send(user, b, selected_version_id=vid), 404, "RESOURCE_NOT_FOUND")


def test_delete_receipt_tombstone_replay_and_conversation_deletion_preserves_files(user, database):
    cid, key = create(user), str(uuid4())
    result = uploaded(user, cid=cid, key=key)
    assert (
        user[0].delete(f"/api/v1/conversations/{cid}", headers=headers(user[1])).status_code == 204
    )
    assert user[0].get(result["version"]["content_url"]).status_code == 200
    problem(upload(user, cid=cid, key=key), 410, "RESOURCE_GONE")
    receipt = delete(user, result)
    assert receipt.status_code == 202
    schema("DeletionReceipt", receipt.json())
    assert delete(user, result).json() == receipt.json()
    problem(user[0].get(result["version"]["content_url"]), 404, "RESOURCE_NOT_FOUND")
    assert user[0].get("/api/v1/artifacts").json()["items"] == []
    assert len(list(database.settings.media_dir.iterdir())) == 1  # 非立即物理删除。


def test_same_key_after_deleted_global_artifact_is_gone(user):
    key = str(uuid4())
    result = uploaded(user, key=key)
    assert delete(user, result).status_code == 202
    problem(upload(user, key=key), 410, "RESOURCE_GONE")
    assert user[0].head(result["version"]["content_url"]).status_code == 404


def test_actual_artifact_openapi_subset_security_and_models(app):
    actual = app.openapi()
    for path, operations in CONTRACT["paths"].items():
        if not any(
            s in path
            for s in (
                "artifacts",
                "uploads",
                "reference-confirmations",
                "media-grants",
                "capabilities",
            )
        ):
            continue
        implemented = actual["paths"]["/api/v1" + path]
        assert set(implemented) == set(operations)
        for method, design in operations.items():
            op = implemented[method]
            assert op["operationId"] == design["operationId"]
            assert op["security"] == design["security"]
            for status, response in design["responses"].items():
                if int(status) < 300 and method != "head":
                    assert op["responses"][status].get("content") == response.get("content")
            if method == "post":
                assert any(
                    p.get("name") == "Idempotency-Key" and p["required"] for p in op["parameters"]
                )
    for name in (
        "TextArtifactCreate",
        "TextVersionCreate",
        "ReferenceConfirmationCreate",
        "Artifact",
        "ArtifactVersion",
        "ReferenceConfirmation",
        "DeletionReceipt",
    ):
        model = actual["components"]["schemas"][name]
        design = CONTRACT["components"]["schemas"][name]
        assert model["additionalProperties"] is False
        assert set(model["properties"]) == set(design["properties"])
        assert set(model["required"]) == set(design["required"])
