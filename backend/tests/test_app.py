import json
import shutil
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import Field

from omniflow.app import create_app
from omniflow.config import Settings
from omniflow.problems import StrictModel

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/api/openapi-v1.json").read_text()
)


def validate_schema(name, data):
    Draft202012Validator(
        CONTRACT["components"]["schemas"][name], format_checker=FormatChecker()
    ).validate(data)


def assert_safe_problem(response, status, code):
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.json()["code"] == code
    assert response.json()["request_id"] == response.headers["x-request-id"]
    validate_schema("Problem", response.json())


def test_health_contract_and_generated_request_ids(client, settings):
    ids = []
    for route in ("live", "ready"):
        response = client.get(f"/api/v1/health/{route}", headers={"X-Request-ID": "untrusted"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        validate_schema("Health", response.json())
        ids.append(UUID(response.headers["x-request-id"]))
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert str(settings.data_dir) not in response.text
        assert "provider" not in response.text
    assert ids[0] != ids[1]


def test_import_factory_and_lifespan_do_not_initialize_storage(settings):
    app = create_app(settings)
    assert not app.state.started
    assert not settings.data_dir.exists()
    with TestClient(app, base_url="https://localhost:8443") as client:
        assert app.state.started
        assert client.get("/api/v1/health/live").status_code == 200
        response = client.get("/api/v1/health/ready")
        assert_safe_problem(response, 503, "STORAGE_UNAVAILABLE")
        assert response.json()["retryable"] is True
        assert str(settings.data_dir) not in response.text
    assert not app.state.started
    assert not settings.data_dir.exists()


def test_low_disk_only_fails_readiness_and_preserves_data(client, database, monkeypatch):
    original = database.path.read_bytes()
    usage = shutil.disk_usage(database.settings.data_dir)
    monkeypatch.setattr("omniflow.db.shutil.disk_usage", lambda _: usage._replace(free=0))
    assert_safe_problem(client.get("/api/v1/health/ready"), 503, "STORAGE_UNAVAILABLE")
    assert client.get("/api/v1/health/live").status_code == 200
    assert database.path.read_bytes() == original


def test_missing_media_and_corrupt_database_readiness(client, database):
    database.settings.media_dir.rmdir()
    assert_safe_problem(client.get("/api/v1/health/ready"), 503, "STORAGE_UNAVAILABLE")
    database.settings.media_dir.mkdir(mode=0o700)
    with database.transaction() as connection:
        connection.execute("DROP TABLE app_metadata")
    assert_safe_problem(client.get("/api/v1/health/ready"), 503, "STORAGE_UNAVAILABLE")


@pytest.mark.parametrize(
    "path",
    [
        "/docs",
        "/redoc",
        "/openapi.json",
        "/",
        "/server.py",
        "/.env",
        "/uploads/synthetic.png",
        "/api/health",
        "/api/chat",
        "/api/v1/capabilities",
    ],
)
def test_no_debug_static_old_or_unimplemented_routes(client, path):
    if path == "/api/v1/capabilities":
        # 阶段 03 已实现，但匿名访问仍必须拒绝，不把能力信息作为公开入口。
        assert_safe_problem(client.get(path), 401, "AUTH_REQUIRED")
    else:
        assert_safe_problem(client.get(path), 404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "localhost.evil.example",
        "user@localhost",
        "localhost/evil",
        "localhost:99999",
        "localhost\\evil",
        "localhost:0",
    ],
)
def test_host_validation_returns_problem(client, host):
    assert_safe_problem(
        client.get("/api/v1/health/live", headers={"host": host}), 400, "VALIDATION_ERROR"
    )


def test_duplicate_origin_and_host_rejected(client):
    assert_safe_problem(
        client.get(
            "/api/v1/health/live",
            headers=[
                ("host", "localhost"),
                ("host", "evil.example"),
            ],
        ),
        400,
        "VALIDATION_ERROR",
    )
    assert_safe_problem(
        client.post(
            "/api/v1/health/live",
            headers=[
                ("origin", "https://localhost:8443"),
                ("origin", "https://evil.example"),
            ],
        ),
        403,
        "CSRF_INVALID",
    )


@pytest.mark.parametrize(
    "origin", [None, "null", "https://evil.example", "https://localhost:8443/"]
)
def test_mutating_requests_require_exact_origin(client, origin):
    headers = {} if origin is None else {"Origin": origin}
    response = client.post("/api/v1/health/live", headers=headers)
    assert_safe_problem(response, 403, "CSRF_INVALID")
    assert "access-control-allow-origin" not in response.headers


def test_method_not_allowed_redacted(client):
    response = client.post("/api/v1/health/live", headers={"Origin": "https://localhost:8443"})
    assert_safe_problem(response, 405, "METHOD_NOT_ALLOWED")


def test_explicit_cors_and_preflight(client):
    origin = "https://localhost:8443"
    response = client.get("/api/v1/health/live", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"]
    preflight = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type, X-CSRF-Token, Idempotency-Key",
    }
    response = client.options("/api/v1/health/live", headers=preflight)
    assert response.status_code == 204 and not response.content
    assert response.headers["access-control-allow-origin"] == origin
    assert "*" not in response.headers["access-control-allow-headers"]
    for override in (
        {"Origin": "https://evil.example"},
        {"Access-Control-Request-Headers": "X-Arbitrary-Key"},
        {"Access-Control-Request-Method": "TRACE"},
    ):
        assert_safe_problem(
            client.options("/api/v1/health/live", headers={**preflight, **override}),
            403,
            "CSRF_INVALID",
        )
    response = client.get("/api/v1/health/live", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_validation_http_and_unexpected_error_never_echo_secrets(app, caplog):
    class Input(StrictModel):
        password: str = Field(min_length=12)

    @app.post("/synthetic-validation")
    def validate(body: Input):
        return {"valid": True}

    @app.get("/synthetic-error")
    def error():
        raise RuntimeError("synthetic-secret /private/path provider-response")

    @app.get("/synthetic-http-error")
    def http_error():
        raise HTTPException(400, detail="synthetic-secret", headers={"X-Leak": "synthetic-secret"})

    with TestClient(
        app, base_url="https://localhost:8443", raise_server_exceptions=False
    ) as client:
        for response, status, code in (
            (
                client.post(
                    "/synthetic-validation",
                    json={"password": "short"},
                    headers={"Origin": "https://localhost:8443"},
                ),
                422,
                "VALIDATION_ERROR",
            ),
            (
                client.post(
                    "/synthetic-validation",
                    json={"password": "synthetic-password", "synthetic-secret": "synthetic-secret"},
                    headers={"Origin": "https://localhost:8443"},
                ),
                422,
                "VALIDATION_ERROR",
            ),
            (
                client.post(
                    "/synthetic-validation",
                    content='{"synthetic-secret":',
                    headers={
                        "Origin": "https://localhost:8443",
                        "Content-Type": "application/json",
                    },
                ),
                422,
                "VALIDATION_ERROR",
            ),
            (client.get("/synthetic-error"), 500, "INTERNAL_ERROR"),
            (client.get("/synthetic-http-error"), 400, "VALIDATION_ERROR"),
        ):
            assert_safe_problem(response, status, code)
            for sensitive in (
                "synthetic-secret",
                "synthetic-password",
                "short",
                "/private/path",
                "input",
            ):
                assert sensitive not in response.text
            assert "x-leak" not in response.headers
    assert "request_failed request_id=" in caplog.text
    assert "synthetic-secret" not in caplog.text
    assert "/private/path" not in caplog.text
    assert "Traceback" not in caplog.text


def test_health_openapi_matches_current_contract_subset(app):
    actual = app.openapi()
    health_paths = {p: ops for p, ops in actual["paths"].items() if p.startswith("/api/v1/health/")}
    assert set(health_paths) == {"/api/v1/health/live", "/api/v1/health/ready"}
    for path, operations in health_paths.items():
        expected = CONTRACT["paths"][path.removeprefix("/api/v1")]["get"]
        operation = operations["get"]
        assert operation["operationId"] == expected["operationId"]
        assert operation.get("security", []) == expected["security"] == []
        assert set(operation["responses"]) == set(expected["responses"])
        assert "application/problem+json" in operation["responses"]["503"]["content"]
        for response in operation["responses"].values():
            assert {"X-Request-ID", "Cache-Control"} <= set(response["headers"])
    health = actual["components"]["schemas"]["Health"]
    expected_health = CONTRACT["components"]["schemas"]["Health"]
    assert health["required"] == expected_health["required"]
    assert health["additionalProperties"] is False
    assert health["properties"]["status"]["enum"] == expected_health["properties"]["status"]["enum"]


def test_docs_opt_in_only_and_production_security_headers(settings):
    with TestClient(
        create_app(Settings(data_dir=settings.data_dir, docs_enabled=True)),
        base_url="https://localhost:8443",
    ) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
    with TestClient(
        create_app(Settings(data_dir=settings.data_dir, environment="production")),
        base_url="https://localhost:8443",
    ) as client:
        assert (
            client.get("/api/v1/health/live").headers["strict-transport-security"]
            == "max-age=31536000"
        )
        assert client.get("/docs").status_code == 404
