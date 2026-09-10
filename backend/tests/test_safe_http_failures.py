"""实际 PinnedHTTPSExchange 故障链路；socket/TLS/HTTP 全为合成替身，不联网。"""

import subprocess
import sys
from pathlib import Path

import pytest
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import problem
from test_auth import user as user
from test_conversations import create
from test_media_adapters import agnes, install, video_request
from test_tasks import accepted, action, due, get, image_request, step, submit

from omniflow import safe_http


class SyntheticWire:
    def __init__(
        self,
        monkeypatch,
        *,
        status=429,
        headers=None,
        response_error=None,
        connection_error=None,
        header_error=None,
        read_error=None,
        tls_error=None,
        raw_error=None,
    ):
        self.requests = []
        self.closed = []
        self.reads = 0
        self.header_reads = 0
        wire = self

        class RawSocket:
            def close(self):
                wire.closed.append("raw")
                if raw_error is not None:
                    raise raw_error

        class Socket:
            def settimeout(self, timeout):
                assert timeout > 0

        class TLS:
            def wrap_socket(self, raw, *, server_hostname):
                assert isinstance(raw, RawSocket)
                assert server_hostname == "api.agnes-ai.cn"
                if tls_error is not None:
                    raise tls_error
                return Socket()

        class Response:
            def __init__(self):
                self.status = status
                self.chunks = iter([b"{}", b""])

            def getheaders(self):
                wire.header_reads += 1
                if header_error is not None:
                    raise header_error
                return (
                    headers
                    if headers is not None
                    else [
                        ("Content-Type", "application/json"),
                        ("Retry-After", "120"),
                    ]
                )

            def read1(self, size):
                wire.reads += 1
                assert size == 65536
                if read_error is not None:
                    raise read_error
                return next(self.chunks)

            def close(self):
                wire.closed.append("response")
                if response_error is not None:
                    raise response_error

        class Connection:
            def __init__(self, hostname, *, timeout):
                assert hostname == "api.agnes-ai.cn" and 0 < timeout <= 10

            def request(self, method, target, *, body, headers):
                wire.requests.append((method, target, body, headers))

            def getresponse(self):
                return Response()

            def close(self):
                wire.closed.append("connection")
                if connection_error is not None:
                    raise connection_error

        def connect(address, *, timeout):
            assert address == ("8.8.8.8", 443) and 0 < timeout <= 10
            return RawSocket()

        monkeypatch.setattr(safe_http.socket, "create_connection", connect)
        monkeypatch.setattr(safe_http.ssl, "create_default_context", TLS)
        monkeypatch.setattr(safe_http.http.client, "HTTPSConnection", Connection)


def request(http=None):
    http = http or safe_http.SafeHTTP(
        exchange=safe_http.PinnedHTTPSExchange(), resolver=lambda host: ("8.8.8.8",)
    )
    return http.request(
        "POST",
        "https://api.agnes-ai.cn/v1/videos",
        hosts=("api.agnes-ai.cn",),
        body=b"{}",
        max_bytes=20,
    )


@pytest.mark.parametrize("factory", [video_request, image_request], ids=["video", "image"])
@pytest.mark.parametrize(
    "fault",
    [
        "response-close",
        "duplicate-content-type",
        "connection-close",
        "both-close",
        "duplicate-retry-after",
        "header-read",
        "duplicate-and-both-close",
    ],
)
def test_pinned_429_persists_and_blocks_new_and_queued_after_restart(
    user, app, database, monkeypatch, tmp_path, fault, factory
):
    options = {}
    if fault in ("response-close", "both-close", "duplicate-and-both-close"):
        options["response_error"] = OSError("synthetic-close-private")
    if fault in ("connection-close", "both-close", "duplicate-and-both-close"):
        options["connection_error"] = OSError("synthetic-connection-private")
    if fault in ("duplicate-content-type", "duplicate-and-both-close"):
        options["headers"] = [
            ("Retry-After", "120"),
            ("Content-Type", "application/json"),
            ("content-type", "text/html"),
        ]
    if fault == "duplicate-retry-after":
        options["headers"] = [("Retry-After", "600"), ("retry-after", "1")]
    if fault == "header-read":
        options["header_error"] = OSError("synthetic-header-private")
    wire = SyntheticWire(monkeypatch, **options)
    adapter = agnes(database, safe_http.PinnedHTTPSExchange())
    install(app, database, adapter)
    cid = create(user)
    unknown = accepted(user, factory(cid))
    queued = accepted(user, factory(cid))
    step(database, adapter)
    assert get(user, unknown["id"])["status"] == "submission_unknown"

    restarted = agnes(database, safe_http.PinnedHTTPSExchange())
    install(app, database, restarted)
    new_request = submit(user, factory(cid))
    step(database, restarted)
    current = get(user, queued["id"])
    with database.snapshot() as connection:
        rows = connection.execute("SELECT * FROM provider_limits").fetchall()
        observed = (
            len(rows),
            new_request.status_code,
            current["status"],
            sum(r[0] == "POST" for r in wire.requests),
        )
        assert observed == (1, 503, "queued", 1)
        delay = 120 if fault in ("response-close", "connection-close", "both-close") else 300
        assert rows[0]["retry_at"] - rows[0]["observed_at"] == delay
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (queued["id"],)).fetchone()
        assert row["dispatched_at"] is None and row["lease_token"] is None
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 2
    problem(new_request, 503, "PROVIDER_LIMIT_REACHED")
    assert current["error"]["code"] == "PROVIDER_LIMIT_REACHED"
    problem(action(user, unknown["id"], "recover"), 409, "RECONCILIATION_REQUIRED")
    assert get(user, unknown["id"])["status"] == "submission_unknown"
    assert wire.closed == ["response", "connection"]
    assert wire.reads == 0
    assert "synthetic-close-private" not in new_request.text
    if factory is video_request and fault in ("response-close", "duplicate-content-type"):
        # 两个原始重现再以独立 Python 进程、新有效证据验证；不能靠内存对象记住暂停。
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
        current = get(user, queued["id"])
        assert current["status"] == "queued"
        assert current["error"]["code"] == "PROVIDER_LIMIT_REACHED"
        assert get(user, unknown["id"])["status"] == "submission_unknown"
        assert len(wire.requests) == 1


@pytest.mark.parametrize("status", [200, 302, 400, 500])
@pytest.mark.parametrize(
    "name", ["Content-Type", "Content-Length", "Content-Encoding", "Location", "Retry-After"]
)
def test_non_429_ambiguous_headers_still_rejected_and_both_closed(monkeypatch, status, name):
    wire = SyntheticWire(
        monkeypatch,
        status=status,
        headers=[(name, "1"), (name.lower(), "2")],
        response_error=OSError("secondary-response"),
        connection_error=OSError("secondary-connection"),
    )
    with pytest.raises(ValueError, match="响应头不明确"):
        request()
    assert wire.closed == ["response", "connection"] and wire.reads == 0


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize("failure", ["response", "connection", "both"])
def test_standalone_close_error_is_not_suppressed(monkeypatch, status, failure):
    response_error, connection_error = OSError("response"), OSError("connection")
    wire = SyntheticWire(
        monkeypatch,
        status=status,
        response_error=response_error if failure != "connection" else None,
        connection_error=connection_error if failure != "response" else None,
    )
    with pytest.raises(OSError) as error:
        request()
    assert error.value is (connection_error if failure == "connection" else response_error)
    assert wire.closed == ["response", "connection"] and wire.reads == 2


@pytest.mark.parametrize("primary", [TimeoutError("read"), SystemExit(73)])
def test_body_error_remains_primary_when_both_close_fail(monkeypatch, primary):
    wire = SyntheticWire(
        monkeypatch,
        status=200,
        read_error=primary,
        response_error=OSError("response"),
        connection_error=OSError("connection"),
    )
    with pytest.raises(type(primary)) as error:
        request()
    assert error.value is primary
    assert wire.closed == ["response", "connection"] and wire.reads == 1


@pytest.mark.parametrize(
    "status,headers,message",
    [
        (302, [("Location", "https://127.0.0.1/private")], "重定向"),
        (200, [("Content-Encoding", "gzip")], "压缩"),
        (200, [("Content-Length", "1000")], "容量"),
        (200, [("Content-Length", "4")], "不完整"),
    ],
)
def test_response_validation_remains_primary_when_both_close_fail(
    monkeypatch, status, headers, message
):
    wire = SyntheticWire(
        monkeypatch,
        status=status,
        headers=headers,
        response_error=OSError("response"),
        connection_error=OSError("connection"),
    )
    with pytest.raises(ValueError, match=message):
        request()
    assert wire.closed == ["response", "connection"]


def test_header_read_error_without_429_is_not_hidden(monkeypatch):
    primary = OSError("headers")
    wire = SyntheticWire(
        monkeypatch,
        status=200,
        header_error=primary,
        response_error=OSError("response"),
        connection_error=OSError("connection"),
    )
    with pytest.raises(OSError) as error:
        request()
    assert error.value is primary
    assert wire.closed == ["response", "connection"] and wire.reads == 0


def test_tls_error_closes_raw_and_connection_without_losing_primary(monkeypatch):
    primary = safe_http.ssl.SSLError("synthetic TLS verification failure")
    wire = SyntheticWire(
        monkeypatch,
        tls_error=primary,
        raw_error=OSError("raw"),
        connection_error=OSError("connection"),
    )
    with pytest.raises(safe_http.ssl.SSLError) as error:
        request()
    assert error.value is primary
    assert wire.closed == ["raw", "connection"] and wire.requests == []


@pytest.mark.parametrize("malformed", [False, True])
def test_injected_response_429_survives_headers_and_close_errors(malformed):
    closed = []

    def close():
        closed.append(True)
        raise OSError("synthetic-close")

    def chunks():
        raise AssertionError("429 正文不得读取")
        yield b""

    response = safe_http.HTTPResponse(
        429,
        {"Retry-After": "120", **({"retry-after": "1"} if malformed else {})},
        chunks(),
        close,
    )
    http = safe_http.SafeHTTP(exchange=lambda request: response, resolver=lambda host: ("8.8.8.8",))
    with pytest.raises(safe_http.HTTPRateLimited) as error:
        request(http)
    assert error.value.retry_after == (None if malformed else "120")
    assert closed == [True]
