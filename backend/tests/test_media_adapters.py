"""Agnes HTTP 合成传输及 FFmpeg 合成图片实跑，绝无真实媒体供应商调用。"""

import json
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from test_agy_adapter import gate
from test_artifacts import confirmation, image_bytes, uploaded
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import create
from test_tasks import accepted, due, finish, get, image_request, step

from omniflow.agnes_adapter import AgnesProvider
from omniflow.ffmpeg_adapter import FFmpegProvider, MediaRouter
from omniflow.provider_gate import EvidenceGate
from omniflow.safe_http import HTTPResponse, SafeHTTP, validate_url
from omniflow.task_worker import TaskWorker
from omniflow.tasks import TaskService

HOST = "synthetic-storage.example"
URL = f"https://{HOST}/synthetic.png?signature=synthetic-only"


class Exchange:
    def __init__(self):
        self.requests = []
        self.responses = []
        self.closed = 0

    def add(self, data, *, mime="application/json", status=200, headers=None):
        raw = json.dumps(data).encode() if isinstance(data, dict) else data
        self.responses.append((status, {"Content-Type": mime, **(headers or {})}, [raw]))

    def __call__(self, request):
        self.requests.append(request)
        if not self.responses:
            raise TimeoutError("synthetic-secret-not-public")
        status, headers, chunks = self.responses.pop(0)
        return HTTPResponse(status, headers, chunks, self.close)

    def close(self):
        self.closed += 1


def agnes(database, exchange, **kwargs):
    return AgnesProvider(
        database,
        http=SafeHTTP(exchange=exchange, resolver=lambda host: ("8.8.8.8",)),
        authorization=lambda: "synthetic-api-key-only",
        gate=gate(),
        download_hosts=(HOST,),
        **kwargs,
    )


def install(app, database, provider):
    app.state.tasks = TaskService(database, provider)


def video_request(cid, **extra):
    return {
        "conversation_id": cid,
        "kind": "ai_video",
        "mode": "text",
        "prompt": "合成海洋",
        "seconds": 4,
        "size_tier": "720P",
        "aspect_ratio": "9:16",
        **extra,
    }


def test_synchronous_image_receipt_survives_adapter_restart_and_download_retry(user, app, database):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    cid = create(user)
    task = accepted(user, image_request(cid))
    exchange.add({"data": [{"url": URL}]})
    step(database, adapter)
    assert get(user, task["id"])["status"] == "running"
    assert json.loads(exchange.requests[0].body) == {
        "model": "agnes-image-2.5-flash",
        "prompt": "合成蓝色方块，无品牌",
        "size": "1K",
        "ratio": "9:16",
        "extra_body": {"response_format": "url"},
    }
    assert exchange.requests[0].url == "https://api.agnes-ai.cn/v1/images/generations"
    restarted = agnes(database, exchange)
    step(database, restarted)
    assert get(user, task["id"])["status"] == "saving"
    step(database, restarted)  # 下载超时保留同一 URL；没有偷偷重发图片 POST。
    assert get(user, task["id"])["status"] == "saving"
    exchange.add(image_bytes(), mime="image/png")
    step(database, agnes(database, exchange))
    result = get(user, task["id"])
    assert result["status"] == "completed"
    assert sum(r.method == "POST" for r in exchange.requests) == 1
    assert all("Authorization" not in r.headers for r in exchange.requests if HOST in r.url)
    assert all(r.address == "8.8.8.8" for r in exchange.requests)
    assert URL not in json.dumps(result) and "synthetic-api-key" not in json.dumps(result)
    assert exchange.closed == 2  # 成功 POST、成功 GET；超时无 response。


def test_reference_images_keep_original_order_and_grants_are_task_bound(user, app, database):
    exchange = Exchange()
    adapter = agnes(database, exchange, reference_editing_enabled=True)
    install(app, database, adapter)
    cid = create(user)
    refs = [uploaded(user, cid=cid)["version"]["id"] for _ in range(2)]
    refs.sort(reverse=True)
    task = accepted(user, image_request(cid, reference_version_ids=refs))
    exchange.add({"data": [{"url": URL}]})
    step(database, adapter)
    body = json.loads(exchange.requests[0].body)
    urls = body["extra_body"]["image"]
    actual = []
    with database.snapshot() as connection:
        for url in urls:
            gid = urlsplit(url).path.split("/")[-2]
            row = connection.execute(
                "SELECT task_id,version_id FROM media_grants WHERE id=?", (gid,)
            ).fetchone()
            assert row["task_id"] == task["id"]
            actual.append(row["version_id"])
    assert actual == refs
    assert all("token=" in u for u in urls)
    assert all(u not in json.dumps(get(user, task["id"])) for u in urls)


def test_video_uses_video_id_model_name_and_saves_actual_not_requested(user, app, database):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    cid = create(user)
    image = uploaded(user, cid=cid)
    confirmation_id = confirmation(user, cid, image["version"]["id"]).json()["id"]
    task = accepted(
        user, video_request(cid, mode="keyframe", reference_confirmation_id=confirmation_id)
    )
    exchange.add(
        {
            "id": "task_distinct",
            "task_id": "task_distinct",
            "video_id": "video_synthetic",
            "model": "agnes-video-2.5-flash",
            "status": "queued",
        }
    )
    step(database, adapter)
    body = json.loads(exchange.requests[0].body)
    assert body["model"] == "agnes-video-2.5-flash" and body["seconds"] == "4"
    assert body["n"] == 1 and body["size"] == "720P" and body["mode"] == "keyframe"
    assert set(body) == {
        "model",
        "prompt",
        "size",
        "n",
        "seconds",
        "aspect_ratio",
        "mode",
        "first_frame",
    }
    first_frame = body["first_frame"]
    grant = user[0].get(first_frame)
    assert grant.status_code == 200
    exchange.add(
        {"video_id": "video_synthetic", "model": "agnes-video-2.5-flash", "status": "in_progress"}
    )
    step(database, agnes(database, exchange))
    query = parse_qs(urlsplit(exchange.requests[-1].url).query)
    assert query == {"video_id": ["video_synthetic"], "model_name": ["agnes-video-2.5-flash"]}
    exchange.add(
        {
            "video_id": "video_synthetic",
            "model": "agnes-video-2.5-flash",
            "status": "completed",
            "metadata": {"url": URL},
        }
    )
    step(database, adapter)
    exchange.add(
        Path(__file__).with_name("fixtures").joinpath("synthetic.mp4").read_bytes(),
        mime="video/mp4",
    )
    step(database, adapter)
    result = get(user, task["id"])
    assert result["status"] == "completed" and result["requested_parameters"]["seconds"] == 4
    with database.snapshot() as connection:
        row = connection.execute(
            "SELECT * FROM artifact_versions WHERE id=?", (result["output_version_ids"][0],)
        ).fetchone()
        assert row["duration_seconds"] == 1.25 and row["width"] == 32
    assert user[0].get(first_frame).status_code == 404
    assert sum(r.method == "POST" for r in exchange.requests) == 1


@pytest.mark.parametrize(
    "response,status,expected",
    [
        ({"detail": "size must be 720P"}, 400, "failed"),
        ({"detail": "secret"}, 500, "submission_unknown"),
        ({"detail": "limit"}, 429, "submission_unknown"),
        ({"model": "agnes-video-2.5", "video_id": "video_x"}, 200, "submission_unknown"),
        ({"model": "agnes-video-2.5-flash", "id": "not_video_id"}, 200, "submission_unknown"),
    ],
)
def test_submission_error_never_fallback_or_blind_retry(
    user, app, database, response, status, expected
):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    task = accepted(user, video_request(create(user)))
    exchange.add(response, status=status)
    step(database, adapter)
    assert get(user, task["id"])["status"] == expected
    due(database)
    assert not TaskWorker(database, agnes(database, exchange)).execute_next()
    assert len(exchange.requests) == 1


def test_submit_rechecks_evidence_no_request_after_expiry(user, app, database):
    exchange = Exchange()
    adapter = agnes(database, exchange)
    install(app, database, adapter)
    task = accepted(user, image_request(create(user)))
    adapter.gate = EvidenceGate()
    step(database, adapter)
    assert get(user, task["id"])["status"] == "queued"
    assert get(user, task["id"])["error"]["code"] == "FREE_ACCESS_UNCONFIRMED"
    assert exchange.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "http://synthetic-storage.example/x",
        "https://127.0.0.1/x",
        "file:///etc/passwd",
        "https://synthetic-storage.example.evil/x",
        "https://user@synthetic-storage.example/x",
        "https://synthetic-storage.example:444/x",
        "https://synthetic-storage.example/x#y",
        "https://synthetic-storage.example/\\@evil",
        "https://synthetic-storage.example/x\r\n",
    ],
)
def test_bad_destinations_denied_before_transport(url):
    with pytest.raises(ValueError):
        validate_url(url, (HOST,))


@pytest.mark.parametrize(
    "addresses", [("127.0.0.1",), ("::1",), ("169.254.169.254",), ("8.8.8.8", "10.0.0.1"), ()]
)
def test_dns_private_or_mixed_targets_denied(addresses):
    exchange = Exchange()
    with pytest.raises(ValueError):
        SafeHTTP(exchange=exchange, resolver=lambda host: addresses).request(
            "GET", URL, hosts=(HOST,), max_bytes=20
        )
    assert exchange.requests == []


@pytest.mark.parametrize(
    "status,headers,raw",
    [
        (302, {"Location": "http://127.0.0.1/secret"}, b""),
        (302, {"Location": URL}, b""),
        (200, {"Content-Length": "1000"}, b"x"),
        (200, {"Content-Length": "4"}, b"x"),
        (200, {"Content-Encoding": "gzip"}, b"x"),
        (200, {}, b"x" * 21),
    ],
)
def test_redirect_capacity_compression_and_incomplete_response_close(status, headers, raw):
    exchange = Exchange()
    exchange.add(raw, status=status, headers=headers)
    with pytest.raises(ValueError):
        SafeHTTP(exchange=exchange, resolver=lambda host: ("8.8.8.8",)).request(
            "GET", URL, hosts=(HOST,), max_bytes=20
        )
    assert len(exchange.requests) == 1 and exchange.closed == 1


@pytest.mark.parametrize("motion", ["dolly_in", "pan_left", "pan_right", "dynamic_float"])
def test_real_ffmpeg_synthetic_image_four_motions_restart_and_download(user, app, database, motion):
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
            "motion_type": motion,
            "seconds": 1,
            "aspect_ratio": "16:9",
        },
    )
    step(database, local)
    assert get(user, task["id"])["status"] == "running"
    result = finish(database, FFmpegProvider(database, gate=gate()), user, task["id"])
    assert result["status"] == "completed" and result["execution_engine"] == "local-ffmpeg"
    with database.snapshot() as connection:
        version = connection.execute(
            "SELECT * FROM artifact_versions WHERE id=?", (result["output_version_ids"][0],)
        ).fetchone()
        assert version["width"] == 1280 and version["height"] == 720 and version["fps"] == 24
        assert version["duration_seconds"] == 1
        url = f"/api/v1/artifacts/{version['artifact_id']}/versions/{version['id']}/content"
    assert user[0].get(url).status_code == 200
    assert user[0].get(url, headers={"Range": "bytes=0-9"}).status_code == 206


def test_ffmpeg_timeout_and_fixed_command_no_shell(user, app, database):
    calls = []

    def timeout(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired("synthetic", 90)

    local = FFmpegProvider(database, gate=gate(), runner=timeout)
    router = MediaRouter(AgnesProvider(database), local)
    install(app, database, router)
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
            "aspect_ratio": "9:16",
        },
    )
    step(database, router)
    assert get(user, task["id"])["status"] == "submission_unknown"
    args, kwargs = calls[0]
    assert args[args.index("-protocol_whitelist") + 1] == "file,pipe"
    assert kwargs.get("shell", False) is False and kwargs["timeout"] == 90
    assert kwargs["input"].startswith(b"\x89PNG")
    assert not any("http" in arg for arg in args)
    due(database)
    assert not TaskWorker(database, router).execute_next()
    assert len(calls) == 1
