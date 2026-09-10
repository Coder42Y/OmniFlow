"""最终审查：字段校验前的请求容量保护；仅合成 ASGI 分块，不访问网络。"""

import json

import anyio
import pytest
from fastapi.testclient import TestClient
from test_app import assert_safe_problem

from omniflow.artifact_models import TextArtifactCreate

BODY_LIMIT = 1024 * 1024


def install_probe(app):
    accepted = []

    @app.post("/synthetic-body-limit")
    def probe(data: TextArtifactCreate):
        accepted.append(data)
        return {"accepted": True}

    return accepted


@pytest.mark.parametrize("declared_length", [None, "actual", "2"])
@pytest.mark.parametrize("chunk_size", [65536, BODY_LIMIT + 1])
def test_body_limit_counts_received_bytes_without_trusting_length(app, declared_length, chunk_size):
    accepted = install_probe(app)
    # 超长的合法 JSON 空白也不能在字段长度检查前无限累积。
    body = b" " * BODY_LIMIT + b'{"kind":"text","title":"safe","content":"synthetic-secret"}'
    headers = [
        (b"host", b"localhost"),
        (b"origin", b"https://localhost:8443"),
        (b"content-type", b"application/json"),
    ]
    if declared_length is not None:
        headers.append(
            (b"content-length", str(len(body) if declared_length == "actual" else 2).encode())
        )
    reads = 0
    messages = []

    async def receive():
        nonlocal reads
        start = reads * chunk_size
        reads += 1
        assert start < len(body), "拒绝后不应继续消费请求体"
        return {
            "type": "http.request",
            "body": body[start : start + chunk_size],
            "more_body": start + chunk_size < len(body),
        }

    async def send(message):
        messages.append(message)

    async def run():
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/synthetic-body-limit",
                "raw_path": b"/synthetic-body-limit",
                "query_string": b"",
                "root_path": "",
                "headers": headers,
                "client": ("127.0.0.1", 12345),
                "server": ("localhost", 8443),
            },
            receive,
            send,
        )

    anyio.run(run)
    start = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    problem = json.loads(raw)
    assert start["status"] == problem["status"] == 413
    assert problem["code"] == "UPLOAD_TOO_LARGE"
    assert "synthetic-secret" not in raw.decode()
    response_headers = dict(start["headers"])
    assert response_headers[b"content-type"] == b"application/problem+json"
    assert response_headers[b"cache-control"] == b"private, no-store"
    assert response_headers[b"x-request-id"].decode() == problem["request_id"]
    assert not accepted
    if declared_length == "actual":
        assert reads == 0  # 可提前拒绝时，不等待客户端发送大正文。


@pytest.mark.parametrize(
    "media_type", ["application/json", "application/problem+json", "text/plain"]
)
def test_body_limit_not_bypassed_by_content_type(app, media_type):
    accepted = install_probe(app)
    with TestClient(app, base_url="https://localhost:8443") as client:
        response = client.post(
            "/synthetic-body-limit",
            content=b" " * (BODY_LIMIT + 1),
            headers={"Origin": "https://localhost:8443", "Content-Type": media_type},
        )
    assert_safe_problem(response, 413, "UPLOAD_TOO_LARGE")
    assert not accepted


def test_maximum_contract_text_and_exact_boundary_remain_accepted(app):
    accepted = install_probe(app)
    # 50000 个非 BMP 字符即使全部使用 JSON 转义，仍在保护上限内。
    body = json.dumps({"kind": "text", "title": "合成标题", "content": "🌊" * 50000}).encode()
    assert len(body) < BODY_LIMIT
    body += b" " * (BODY_LIMIT - len(body))
    with TestClient(app, base_url="https://localhost:8443") as client:
        response = client.post(
            "/synthetic-body-limit",
            content=body,
            headers={"Origin": "https://localhost:8443", "Content-Type": "application/json"},
        )
    assert response.status_code == 200
    assert len(accepted) == 1 and accepted[0].content == "🌊" * 50000


def test_origin_is_rejected_before_large_body(client):
    response = client.post(
        "/api/v1/auth/login",
        content=b" " * (BODY_LIMIT + 1),
        headers={"Origin": "https://untrusted.example", "Content-Type": "application/json"},
    )
    assert_safe_problem(response, 403, "CSRF_INVALID")
