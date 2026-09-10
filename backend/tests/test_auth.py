"""只使用合成账号/凭据；内存 HTTP + 临时 SQLite，禁止真实供应商网络。"""

import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from omniflow import auth_service
from omniflow.app import create_app
from omniflow.auth_models import LoginInput
from omniflow.auth_security import (
    PASSWORD_HASHER,
    AuthContext,
    digest,
    require_generation_allowed,
    require_session,
)
from omniflow.auth_service import AuthService
from omniflow.config import Settings
from omniflow.db import Database
from omniflow.problems import ProblemError

ORIGIN = "https://localhost:8443"
PASSWORD = "Synthetic Password 123! "
NEW_PASSWORD = "Different Synthetic 456! "
CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/api/openapi-v1.json").read_text()
)


def schema(name, data):
    Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": CONTRACT["components"]},
        format_checker=FormatChecker(),
    ).validate(data)


def problem(response, status, code):
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert response.json()["code"] == code
    schema("Problem", response.json())
    assert PASSWORD not in response.text
    assert "input" not in response.json()


def csrf(client):
    response = client.get("/api/v1/auth/csrf")
    assert response.status_code == 200, response.text
    schema("CsrfToken", response.json())
    return response.json()["csrf_token"]


def headers(token):
    return {"Origin": ORIGIN, "X-CSRF-Token": token}


def post(client, path, token, data=None):
    return client.post("/api/v1" + path, json=data, headers=headers(token))


def login(client, username="owner", password=PASSWORD):
    response = post(
        client, "/auth/login", csrf(client), {"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    schema("LoginResult", response.json())
    return response.json()["csrf_token"]


def token_from(url):
    parsed = urlsplit(url)
    assert parsed.scheme == "https" and not parsed.query
    return parse_qs(parsed.fragment)["token"][0]


@pytest.fixture
def browsers(app):
    with ExitStack() as stack:

        def factory():
            return stack.enter_context(TestClient(app, base_url=ORIGIN))

        yield factory


@pytest.fixture
def admin(app, browsers):
    user = app.state.auth.create_admin("owner", PASSWORD)
    client = browsers()
    return client, login(client), str(user.id)


def invite(admin, data=None):
    client, token, _ = admin
    response = post(client, "/admin/invitations", token, data or {})
    assert response.status_code == 201, response.text
    schema("InviteIssued", response.json())
    return response.json()


def register(client, raw, username="alice", password=PASSWORD):
    return post(
        client,
        "/auth/register",
        csrf(client),
        {"username": username, "password": password, "invite_token": raw},
    )


@pytest.fixture
def user(admin, browsers):
    client = browsers()
    response = register(client, token_from(invite(admin)["invite_url"]))
    assert response.status_code == 201, response.text
    return client, response.json()["csrf_token"], response.json()["user"]["id"]


def issue_reset(admin, user_id):
    response = post(
        admin[0],
        f"/admin/users/{user_id}/password-resets",
        admin[1],
        {"verification_method": "trusted_existing_contact", "note": PASSWORD},
    )
    assert response.status_code == 201, response.text
    schema("PasswordResetIssued", response.json())
    return token_from(response.json()["reset_url"])


def test_default_and_dynamic_policy(settings):
    custom = Settings(
        data_dir=settings.data_dir,
        password_min_length=16,
        password_max_length=100,
        session_ttl_seconds=100,
        invitation_ttl_seconds=222,
        password_reset_ttl_seconds=33,
    )
    Database(custom).migrate()
    app = create_app(custom)
    app.state.auth.create_admin("owner", PASSWORD)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/v1/auth/policy")
        schema("AuthPolicy", response.json())
        assert response.json() == {
            "username_pattern": "^[a-z0-9_]{3,32}$",
            "username_normalization": "trim_ascii_spaces_then_lowercase",
            "password_min_length": 16,
            "password_max_length": 100,
            "session_ttl_seconds": 100,
            "invitation_ttl_default_seconds": 222,
            "password_reset_ttl_default_seconds": 33,
        }
        issued = invite((client, login(client), ""))
        from datetime import datetime

        metadata = issued["invite"]
        duration = datetime.fromisoformat(metadata["expires_at"]) - datetime.fromisoformat(
            metadata["created_at"]
        )
        assert 221 <= duration.total_seconds() <= 222


def test_invite_register_once_normalization_and_argon2(admin, browsers, database):
    issued = invite(admin)
    raw = token_from(issued["invite_url"])
    client = browsers()
    before = csrf(client)
    for _ in range(2):
        response = post(client, "/auth/invitations/validate", before, {"token": raw})
        assert response.status_code == 200
        schema("TokenValidity", response.json())
    response = post(
        client,
        "/auth/register",
        before,
        {"username": " ALIce_01 ", "password": PASSWORD, "invite_token": raw},
    )
    assert response.status_code == 201, response.text
    schema("LoginResult", response.json())
    assert response.json()["user"]["username"] == "alice_01"
    assert response.json()["user"]["role"] == "user"
    assert response.json()["csrf_token"] != before
    assert client.cookies.get("__Host-omniflow_csrf") is None
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE username = 'alice_01'").fetchone()
        assert row["password_hash"].startswith("$argon2id$v=19$m=19456,t=2,p=1$")
        assert PASSWORD_HASHER.verify(row["password_hash"], PASSWORD)
        assert connection.execute("SELECT used_by FROM invites").fetchone()[0] == row["id"]
    problem(register(browsers(), raw, "bob"), 400, "TOKEN_INVALID")
    listing = admin[0].get("/api/v1/admin/invitations")
    schema("InvitePage", listing.json())
    assert listing.json()["items"][0]["status"] == "used"
    assert raw not in listing.text and "invite_url" not in listing.text
    problem(
        post(admin[0], f"/admin/invitations/{issued['invite']['id']}/revoke", admin[1]),
        409,
        "RESOURCE_IN_USE",
    )


def test_username_conflict_does_not_consume_invite(admin, user, browsers):
    raw = token_from(invite(admin)["invite_url"])
    client = browsers()
    problem(register(client, raw, "ALICE"), 409, "USERNAME_UNAVAILABLE")
    response = register(client, raw, "bob")
    assert response.status_code == 201


@pytest.mark.parametrize(
    "username", ["ab", "a" * 33, "用户名字", "abc-def", "abc\t", "\u212aelvin"]
)
def test_username_policy_forbids_non_ascii(admin, browsers, username):
    raw = token_from(invite(admin)["invite_url"])
    response = register(browsers(), raw, username)
    problem(response, 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("password", ["a" * 5, "a" * 129, ""])
def test_password_lengths_never_truncated(admin, browsers, password):
    raw = token_from(invite(admin)["invite_url"])
    problem(register(browsers(), raw, password=password), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("password", ["246810", "a" * 11, "a" * 128])
def test_registration_accepts_new_password_boundaries(admin, browsers, password):
    raw = token_from(invite(admin)["invite_url"])
    client = browsers()
    response = register(client, raw, password=password)
    assert response.status_code == 201, response.text
    assert client.get("/api/v1/auth/policy").json()["password_min_length"] == 6
    assert login(client, "alice", password)


def test_admin_creation_uses_six_character_policy(database, browsers):
    service = AuthService(database)
    with pytest.raises(ProblemError):
        service.create_admin("short_admin", "12345")
    service.create_admin("short_admin", "246810")
    assert login(browsers(), "short_admin", "246810")


def test_reset_accepts_six_characters_after_rejecting_five(admin, user, browsers):
    _, _, user_id = user
    raw = issue_reset(admin, user_id)
    client = browsers()
    context = csrf(client)
    problem(
        post(
            client,
            "/auth/password-resets/complete",
            context,
            {"reset_token": raw, "new_password": "12345"},
        ),
        422,
        "VALIDATION_ERROR",
    )
    response = post(
        client,
        "/auth/password-resets/complete",
        context,
        {"reset_token": raw, "new_password": "246810"},
    )
    assert response.status_code == 204
    assert login(client, "alice", "246810")


def test_local_nonexpiring_invite_without_admin_registers_only_user(
    database, browsers, monkeypatch
):
    from omniflow.auth_models import InviteCreate

    auth = AuthService(database)
    issued = auth.issue_invite_locally(InviteCreate(expires_in_seconds=None))
    raw = token_from(issued.invite_url)
    assert issued.invite.expires_at is None
    with database.connect(readonly=True) as connection:
        row = connection.execute("SELECT * FROM invites").fetchone()
        assert row["created_by"] is None and row["expires_at"] is None
        assert row["token_hash"] == digest("invite", raw)
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert (
            connection.execute("SELECT action FROM audit_events").fetchone()[0]
            == "admin.invite_issued_locally"
        )
    client = browsers()
    context = csrf(client)
    with monkeypatch.context() as clock:
        clock.setattr(auth_service, "now", lambda: "2100-01-01T00:00:00.000000Z")
        valid = post(client, "/auth/invitations/validate", context, {"token": raw})
        assert valid.status_code == 200 and valid.json() == {"valid": True, "expires_at": None}
        schema("InviteTokenValidity", valid.json())
    response = register(client, raw, password="246810")
    assert response.status_code == 201 and response.json()["user"]["role"] == "user"
    problem(register(browsers(), raw, "another"), 400, "TOKEN_INVALID")
    problem(
        post(
            client,
            "/admin/invitations",
            response.json()["csrf_token"],
            {"expires_in_seconds": None},
        ),
        403,
        "FORBIDDEN",
    )


def test_admin_nonexpiring_invite_and_local_revocation(admin, browsers, database):
    response = post(admin[0], "/admin/invitations", admin[1], {"expires_in_seconds": None})
    assert response.status_code == 201
    schema("InviteIssued", response.json())
    assert response.json()["invite"]["expires_at"] is None
    raw = token_from(response.json()["invite_url"])
    result = AuthService(database).revoke_invite_locally(response.json()["invite"]["id"])
    assert result.status == "revoked" and result.expires_at is None
    problem(register(browsers(), raw), 400, "TOKEN_INVALID")


def test_reusable_invite_preserves_users_and_can_be_revoked(admin, browsers, database):
    issued = invite(admin, {"expires_in_seconds": None})
    raw = token_from(issued["invite_url"])
    first = register(browsers(), raw, "first_trial")
    assert first.status_code == 201
    auth = AuthService(database)
    auth.make_invite_reusable_locally(issued["invite"]["id"])
    for name in ["second_trial", "third_trial"]:
        response = register(browsers(), raw, name)
        assert response.status_code == 201 and response.json()["user"]["role"] == "user"
    problem(register(browsers(), raw, "first_trial"), 409, "USERNAME_UNAVAILABLE")
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT * FROM invites WHERE id = ?", (issued["invite"]["id"],)
        ).fetchone()
        assert row["status"] == "active" and row["expires_at"] is None
        assert row["used_by"] == first.json()["user"]["id"]
    auth.revoke_invite_locally(issued["invite"]["id"])
    problem(register(browsers(), raw, "fourth_trial"), 400, "TOKEN_INVALID")
    from omniflow.problems import ProblemError

    with pytest.raises(ProblemError):
        auth.make_invite_reusable_locally(issued["invite"]["id"])


def test_password_not_trimmed_or_casefolded(admin, browsers):
    client = browsers()
    token = csrf(client)
    for password in (PASSWORD.strip(), PASSWORD.lower()):
        problem(
            post(client, "/auth/login", token, {"username": "owner", "password": password}),
            401,
            "INVALID_CREDENTIALS",
        )
    assert login(client, " OWNER ")


def test_cookie_flags_csrf_rotation_logout_and_replay(admin, browsers, app):
    client = browsers()
    token = csrf(client)
    anonymous = client.cookies.get("__Host-omniflow_csrf")
    response = post(client, "/auth/login", token, {"username": "owner", "password": PASSWORD})
    for value in response.headers.get_list("set-cookie"):
        for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
            assert flag in value
        assert "Domain=" not in value
    session = client.cookies.get("__Host-omniflow_session")
    current = response.json()["csrf_token"]
    assert csrf(client) == current
    problem(post(client, "/auth/logout", token), 403, "CSRF_INVALID")
    # 登录前匿名上下文已被消费，不能复制出来继续表单登录。
    copied = browsers()
    copied.cookies.set("__Host-omniflow_csrf", anonymous)
    problem(
        post(copied, "/auth/login", token, {"username": "owner", "password": PASSWORD}),
        403,
        "CSRF_INVALID",
    )
    assert post(client, "/auth/logout", current).status_code == 204
    assert not client.cookies.get("__Host-omniflow_session")
    copied.cookies.clear()
    copied.cookies.set("__Host-omniflow_session", session)
    problem(copied.get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")
    with app.state.database.connect() as connection, pytest.raises(ProblemError):
        require_session(connection, AuthContext("session", digest("session", session), session))
    # 另一浏览器的网站登录不受单次注销影响。
    assert admin[0].get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize(
    "variation", ["missing", "wrong", "no_origin", "evil_origin", "other_context", "duplicate"]
)
def test_csrf_failures_for_anonymous_login(admin, browsers, variation):
    client = browsers()
    token = csrf(client)
    request_headers = headers(token)
    if variation == "missing":
        del request_headers["X-CSRF-Token"]
    elif variation == "wrong":
        request_headers["X-CSRF-Token"] = "z" * 64
    elif variation == "no_origin":
        del request_headers["Origin"]
    elif variation == "evil_origin":
        request_headers["Origin"] = "https://evil.invalid"
    elif variation == "other_context":
        request_headers["X-CSRF-Token"] = csrf(browsers())
    else:
        request_headers = [*request_headers.items(), ("X-CSRF-Token", token)]
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": PASSWORD},
        headers=request_headers,
    )
    problem(response, 403, "CSRF_INVALID")


def test_csrf_cookie_not_auth_and_session_relogin_revokes_old(admin, browsers):
    client = browsers()
    csrf(client)
    problem(client.get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")
    old = admin[0].cookies.get("__Host-omniflow_session")
    fresh_token = login(admin[0])
    assert fresh_token != admin[1]
    client.cookies.set("__Host-omniflow_session", old)
    problem(client.get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/admin/users"),
        ("get", "/admin/invitations"),
        ("get", "/admin/generation-policy"),
        ("post", "/admin/invitations"),
        ("patch", "/admin/users/{id}"),
        ("post", "/admin/users/{id}/password-resets"),
        ("post", "/admin/invitations/{id}/revoke"),
        ("patch", "/admin/generation-policy"),
    ],
)
def test_every_admin_route_denies_users_and_anonymous(user, browsers, method, path):
    client, token, user_id = user
    path = "/api/v1" + path.format(id=user_id)
    kwargs = {} if method == "get" else {"json": {}, "headers": headers(token)}
    problem(getattr(client, method)(path, **kwargs), 403, "FORBIDDEN")
    anon = browsers()
    kwargs = {} if method == "get" else {"json": {}, "headers": headers(csrf(anon))}
    problem(getattr(anon, method)(path, **kwargs), 401, "AUTH_REQUIRED")


def test_disable_revokes_all_sessions_epoch_and_reenable_does_not_restore(
    admin, user, browsers, database
):
    client, _, user_id = user
    other = browsers()
    login(other, "alice")
    response = admin[0].patch(
        f"/api/v1/admin/users/{user_id}", json={"status": "disabled"}, headers=headers(admin[1])
    )
    assert response.status_code == 200
    schema("User", response.json())
    for browser in (client, other):
        problem(browser.get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")
    anon = browsers()
    token = csrf(anon)
    problem(
        post(anon, "/auth/login", token, {"username": "alice", "password": "wrong"}),
        401,
        "INVALID_CREDENTIALS",
    )
    problem(
        post(anon, "/auth/login", token, {"username": "absent", "password": "wrong"}),
        401,
        "INVALID_CREDENTIALS",
    )
    problem(
        post(anon, "/auth/login", token, {"username": "alice", "password": PASSWORD}),
        403,
        "ACCOUNT_DISABLED",
    )
    with database.connect() as connection:
        assert (
            connection.execute("SELECT auth_epoch FROM users WHERE id = ?", (user_id,)).fetchone()[
                0
            ]
            == 1
        )
        with pytest.raises(ProblemError, match="ACCOUNT_DISABLED"):
            require_generation_allowed(connection, user_id, "image")
    assert (
        admin[0]
        .patch(
            f"/api/v1/admin/users/{user_id}", json={"status": "active"}, headers=headers(admin[1])
        )
        .status_code
        == 200
    )
    problem(client.get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")
    assert login(anon, "alice")


def test_unique_admin_cannot_be_disabled(admin):
    response = admin[0].patch(
        f"/api/v1/admin/users/{admin[2]}", json={"status": "disabled"}, headers=headers(admin[1])
    )
    problem(response, 409, "RESOURCE_IN_USE")
    assert admin[0].get("/api/v1/auth/me").status_code == 200


def test_reset_purpose_reissue_consumption_and_all_old_sessions(admin, user, browsers, database):
    user_client, _, user_id = user
    second = browsers()
    login(second, "alice")
    raw1 = issue_reset(admin, user_id)
    raw2 = issue_reset(admin, user_id)
    anon = browsers()
    context = csrf(anon)
    problem(
        post(anon, "/auth/password-resets/validate", context, {"token": raw1}), 400, "TOKEN_INVALID"
    )
    problem(
        post(anon, "/auth/invitations/validate", context, {"token": raw2}), 400, "TOKEN_INVALID"
    )
    invite_raw = token_from(invite(admin)["invite_url"])
    problem(
        post(anon, "/auth/password-resets/validate", context, {"token": invite_raw}),
        400,
        "TOKEN_INVALID",
    )
    for _ in range(2):
        assert (
            post(anon, "/auth/password-resets/validate", context, {"token": raw2}).status_code
            == 200
        )
    problem(
        post(
            anon,
            "/auth/password-resets/complete",
            context,
            {"reset_token": raw2, "new_password": "short"},
        ),
        422,
        "VALIDATION_ERROR",
    )
    response = post(
        anon,
        "/auth/password-resets/complete",
        context,
        {"reset_token": raw2, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 204 and response.content == b""
    problem(
        post(
            anon,
            "/auth/password-resets/complete",
            context,
            {"reset_token": raw2, "new_password": PASSWORD},
        ),
        400,
        "TOKEN_INVALID",
    )
    for browser in (anon, second, user_client):
        problem(browser.get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")
    assert admin[0].get("/api/v1/auth/me").status_code == 200
    assert login(anon, "alice", NEW_PASSWORD)
    with database.connect() as connection:
        assert (
            connection.execute("SELECT auth_epoch FROM users WHERE id = ?", (user_id,)).fetchone()[
                0
            ]
            == 1
        )


def test_tokens_expired_revoked_and_unknown_indistinguishable(admin, user, browsers, database):
    issued = invite(admin)
    raw = token_from(issued["invite_url"])
    reset = issue_reset(admin, user[2])
    with database.transaction() as connection:
        connection.execute("UPDATE invites SET expires_at = '2000-01-01T00:00:00Z'")
        connection.execute("UPDATE password_reset_tokens SET expires_at = '2000-01-01T00:00:00Z'")
    assert admin[0].get("/api/v1/admin/invitations").json()["items"][0]["status"] == "expired"
    client = browsers()
    token = csrf(client)
    for route, value in (
        ("invitations", raw),
        ("password-resets", reset),
        ("invitations", "z" * 43),
    ):
        problem(
            post(client, f"/auth/{route}/validate", token, {"token": value}), 400, "TOKEN_INVALID"
        )
    issued2 = invite(admin)
    for _ in range(2):
        assert (
            post(
                admin[0], f"/admin/invitations/{issued2['invite']['id']}/revoke", admin[1]
            ).status_code
            == 200
        )
    problem(
        post(
            client,
            "/auth/invitations/validate",
            token,
            {"token": token_from(issued2["invite_url"])},
        ),
        400,
        "TOKEN_INVALID",
    )


def test_expired_csrf_and_session_are_rejected_then_can_bootstrap(admin, browsers, database):
    client = browsers()
    token = csrf(client)
    with database.transaction() as connection:
        connection.execute("UPDATE csrf_contexts SET expires_at = '2000-01-01T00:00:00Z'")
        connection.execute("UPDATE login_sessions SET expires_at = '2000-01-01T00:00:00Z'")
    problem(
        post(client, "/auth/login", token, {"username": "owner", "password": PASSWORD}),
        403,
        "CSRF_INVALID",
    )
    problem(admin[0].get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")
    assert csrf(admin[0]) != admin[1]
    assert not admin[0].cookies.get("__Host-omniflow_session")
    assert login(admin[0])


def test_generation_pause_persists_partial_patch_and_disallows_arbitrary_fields(
    admin, app, database
):
    client, token, user_id = admin
    response = client.patch(
        "/api/v1/admin/generation-policy", json={"image_enabled": False}, headers=headers(token)
    )
    assert response.status_code == 200
    schema("GenerationPolicy", response.json())
    assert response.json()["text_enabled"] is True
    with database.transaction() as connection:
        require_generation_allowed(connection, user_id, "text")
        with pytest.raises(ProblemError, match="GENERATION_PAUSED"):
            require_generation_allowed(connection, user_id, "image")
    fresh = create_app(app.state.settings)
    with TestClient(fresh, base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get("/api/v1/admin/generation-policy").json() == response.json()
    for data in (
        {},
        {"model": "auto"},
        {"paid_fallback_enabled": True},
        {"image_enabled": "false"},
        {"image_enabled": None},
        {"business_quota": 5},
    ):
        problem(
            client.patch("/api/v1/admin/generation-policy", json=data, headers=headers(token)),
            422,
            "VALIDATION_ERROR",
        )
    response = client.patch(
        "/api/v1/admin/generation-policy", json={"image_enabled": True}, headers=headers(token)
    )
    assert response.json()["image_enabled"] is True
    assert not app.state.settings.free_access_verified
    assert not app.state.settings.paid_fallback_enabled


def test_metadata_pagination_and_no_private_fields(admin, user):
    client, token, _ = admin
    first = client.get("/api/v1/admin/users?limit=1")
    schema("UserPage", first.json())
    cursor = first.json()["next_cursor"]
    assert cursor
    second = client.get("/api/v1/admin/users", params={"limit": 1, "cursor": cursor})
    assert second.json()["next_cursor"] is None
    assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]
    for response in (first, second):
        assert set(response.json()["items"][0]) == {
            "id",
            "username",
            "role",
            "status",
            "created_at",
        }
    problem(
        client.get("/api/v1/admin/invitations", params={"cursor": cursor}), 400, "VALIDATION_ERROR"
    )
    for value in ("bad!", base64.urlsafe_b64encode(b"{}").decode(), "a" * 513):
        response = client.get("/api/v1/admin/users", params={"cursor": value})
        assert response.status_code in (400, 422)
    malformed = base64.urlsafe_b64encode(
        json.dumps(["users", admin[2], "2026-01-01T00:00:00Z", 123]).encode()
    ).decode()
    problem(
        client.get("/api/v1/admin/users", params={"cursor": malformed}), 400, "VALIDATION_ERROR"
    )
    for limit in (0, 101):
        problem(client.get("/api/v1/admin/users", params={"limit": limit}), 422, "VALIDATION_ERROR")
    problem(
        client.patch(
            f"/api/v1/admin/users/{uuid4()}", json={"status": "disabled"}, headers=headers(token)
        ),
        404,
        "RESOURCE_NOT_FOUND",
    )


def test_no_cleartext_credentials_in_database_responses_or_logs(
    admin, user, browsers, database, caplog
):
    issued = invite(admin)
    raw = token_from(issued["invite_url"])
    reset = issue_reset(admin, user[2])
    session = admin[0].cookies.get("__Host-omniflow_session")
    anon = browsers()
    csrf(anon)
    context_cookie = anon.cookies.get("__Host-omniflow_csrf")
    response = post(
        anon,
        "/auth/login",
        csrf(anon),
        {"username": "owner", "password": PASSWORD, "synthetic-secret-field": reset},
    )
    problem(response, 422, "VALIDATION_ERROR")
    assert "synthetic-secret-field" not in response.text
    with database.connect() as connection:
        dump = "\n".join(connection.iterdump())
    for secret in (PASSWORD, raw, reset, session, context_cookie, admin[1]):
        assert secret not in dump
        assert secret not in caplog.text
        assert secret not in response.text
    assert "auth.login" in dump and "admin.reset_issued" in dump


def test_rate_limit_persistent_account_and_ip_and_retry_after(settings, monkeypatch):
    settings = Settings(
        data_dir=settings.data_dir,
        auth_rate_ip_attempts=20,
        auth_rate_username_attempts=2,
        auth_rate_window_seconds=30,
    )
    db = Database(settings)
    db.migrate()
    auth = AuthService(db)
    auth.create_admin("owner", PASSWORD)
    app = create_app(settings)
    with TestClient(app, base_url=ORIGIN) as client:
        token = csrf(client)
        for _ in range(2):
            problem(
                post(client, "/auth/login", token, {"username": "owner", "password": "wrong"}),
                401,
                "INVALID_CREDENTIALS",
            )
        fresh = create_app(settings)
        with TestClient(fresh, base_url=ORIGIN) as restarted:
            restarted.cookies.update(client.cookies)
            response = post(
                restarted, "/auth/login", token, {"username": " OWNER ", "password": PASSWORD}
            )
            problem(response, 429, "RATE_LIMITED")
            assert 1 <= int(response.headers["retry-after"]) <= 30
            assert response.json()["retryable"] is True
        # 修改伪造转发头不能改变实际客户端防爆破桶。
        for _ in range(20):
            response = client.post(
                "/api/v1/auth/login",
                json={"username": str(uuid4()), "password": "wrong"},
                headers={**headers(token), "X-Forwarded-For": str(uuid4())},
            )
        problem(response, 429, "RATE_LIMITED")
        original_time = auth_service.time.time()
        monkeypatch.setattr(auth_service.time, "time", lambda: original_time + 31)
        assert login(client)


@pytest.mark.parametrize("mode", ["timed", "permanent", "reusable"])
def test_invitation_consumption_race_obeys_policy(admin, browsers, database, monkeypatch, mode):
    issued = invite(admin, {"expires_in_seconds": None} if mode != "timed" else {})
    raw = token_from(issued["invite_url"])
    if mode == "reusable":
        AuthService(database).make_invite_reusable_locally(issued["invite"]["id"])
    clients = [browsers(), browsers()]
    contexts = [csrf(c) for c in clients]
    barrier = threading.Barrier(2)
    original_hash = auth_service.hash_password

    def blocked_hash(password):
        value = original_hash(password)
        barrier.wait(timeout=10)
        return value

    monkeypatch.setattr(auth_service, "hash_password", blocked_hash)

    def attempt(i):
        return post(
            clients[i],
            "/auth/register",
            contexts[i],
            {"username": f"racer_{i}", "password": PASSWORD, "invite_token": raw},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(r.status_code for r in results) == (
        [201, 201] if mode == "reusable" else [201, 400]
    )
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users WHERE role = 'user'").fetchone()[
            0
        ] == (2 if mode == "reusable" else 1)
        assert connection.execute("SELECT count(*) FROM invites WHERE status = 'used'").fetchone()[
            0
        ] == (0 if mode == "reusable" else 1)


def test_reset_consumption_race_one_winner(admin, user, browsers, database, monkeypatch):
    raw = issue_reset(admin, user[2])
    clients = [browsers(), browsers()]
    contexts = [csrf(c) for c in clients]
    barrier = threading.Barrier(2)
    original_hash = auth_service.hash_password

    def blocked_hash(password):
        value = original_hash(password)
        barrier.wait(timeout=10)
        return value

    monkeypatch.setattr(auth_service, "hash_password", blocked_hash)
    passwords = [NEW_PASSWORD, "Another Synthetic Winner 789!"]

    def attempt(i):
        return post(
            clients[i],
            "/auth/password-resets/complete",
            contexts[i],
            {"reset_token": raw, "new_password": passwords[i]},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(r.status_code for r in results) == [204, 400]
    winner = next(i for i, r in enumerate(results) if r.status_code == 204)
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user[2],)).fetchone()
        assert PASSWORD_HASHER.verify(row["password_hash"], passwords[winner])
        assert row["auth_epoch"] == 1


def test_login_verified_before_reset_cannot_recreate_old_login(admin, user, browsers, monkeypatch):
    raw = issue_reset(admin, user[2])
    reset_client = browsers()
    reset_context = csrf(reset_client)
    login_client = browsers()
    login_context = csrf(login_client)
    checked = threading.Event()
    release = threading.Event()
    original_verify = auth_service.verify_password

    def paused_verify(password_hash, password):
        result = original_verify(password_hash, password)
        checked.set()
        assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(auth_service, "verify_password", paused_verify)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            post,
            login_client,
            "/auth/login",
            login_context,
            {"username": "alice", "password": PASSWORD},
        )
        assert checked.wait(timeout=10)
        try:
            assert (
                post(
                    reset_client,
                    "/auth/password-resets/complete",
                    reset_context,
                    {"reset_token": raw, "new_password": NEW_PASSWORD},
                ).status_code
                == 204
            )
        finally:
            release.set()
        problem(future.result(), 401, "INVALID_CREDENTIALS")


def test_two_admins_cannot_concurrently_disable_both(admin, app, browsers, database):
    second_id = str(app.state.auth.create_admin("second_admin", PASSWORD).id)
    second = browsers()
    second_token = login(second, "second_admin")
    barrier = threading.Barrier(2)

    def disable(i):
        client, token, own_id = admin if i == 0 else (second, second_token, second_id)
        barrier.wait(timeout=10)
        return client.patch(
            f"/api/v1/admin/users/{own_id}", json={"status": "disabled"}, headers=headers(token)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(disable, range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    with database.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM users WHERE role = 'admin' AND status = 'active'"
            ).fetchone()[0]
            == 1
        )


def test_actual_openapi_auth_subset_security_requests_and_responses(app):
    actual = app.openapi()
    assert {p.removeprefix("/api/v1") for p in actual["paths"]} <= set(CONTRACT["paths"])
    expected_routes = {
        p: ops for p, ops in CONTRACT["paths"].items() if p.startswith(("/auth/", "/admin/"))
    }
    actual_routes = {
        p.removeprefix("/api/v1"): ops
        for p, ops in actual["paths"].items()
        if p.startswith(("/api/v1/auth/", "/api/v1/admin/"))
    }
    assert actual_routes.keys() == expected_routes.keys()
    for path, operations in expected_routes.items():
        assert actual_routes[path].keys() == operations.keys()
        for method, design in operations.items():
            implementation = actual_routes[path][method]
            assert implementation["operationId"] == design["operationId"]
            assert implementation.get("security", []) == design["security"]
            if "requestBody" in design:
                expected_name = design["requestBody"]["content"]["application/json"]["schema"][
                    "$ref"
                ].split("/")[-1]
                actual_name = implementation["requestBody"]["content"]["application/json"][
                    "schema"
                ]["$ref"].split("/")[-1]
                assert actual_name == expected_name
                model = actual["components"]["schemas"][actual_name]
                assert model["additionalProperties"] is False
                expected_model = CONTRACT["components"]["schemas"][expected_name]
                assert set(model["properties"]) == set(expected_model["properties"])
                assert set(model.get("required", [])) == set(expected_model.get("required", []))
            for status, response in design["responses"].items():
                if int(status) < 300:
                    actual_response = implementation["responses"][status]
                    assert actual_response.get("content") == response.get("content")
            for status, response in implementation["responses"].items():
                if int(status) >= 400:
                    assert set(response["content"]) == {"application/problem+json"}
    assert set(actual["components"]["securitySchemes"]) == {
        "SessionCookie",
        "CsrfContextCookie",
        "CsrfHeader",
        "MediaGrantToken",
    }
    assert actual["components"]["securitySchemes"]["MediaGrantToken"] == {
        "type": "apiKey",
        "in": "query",
        "name": "token",
    }


@pytest.mark.parametrize(
    "route",
    [
        "/auth/register",
        "/auth/invitations/validate",
        "/auth/password-resets/validate",
        "/auth/password-resets/complete",
    ],
)
def test_all_anonymous_forms_require_csrf_before_body_validation(client, route):
    csrf(client)
    problem(
        client.post("/api/v1" + route, json={}, headers={"Origin": ORIGIN}), 403, "CSRF_INVALID"
    )


def test_cross_account_csrf_and_duplicate_cookie_rejected(admin, user):
    response = admin[0].patch(
        "/api/v1/admin/generation-policy", json={"text_enabled": False}, headers=headers(user[1])
    )
    problem(response, 403, "CSRF_INVALID")
    raw = admin[0].cookies.get("__Host-omniflow_session")
    response = admin[0].get(
        "/api/v1/auth/me",
        headers={"Cookie": f"__Host-omniflow_session={raw}; __Host-omniflow_session={raw}"},
    )
    problem(response, 403, "CSRF_INVALID")


@pytest.mark.parametrize("seconds", [True, "100", 0, -1, 10**30])
def test_invalid_invite_expiry_fails_without_issuing(admin, seconds, database):
    response = post(admin[0], "/admin/invitations", admin[1], {"expires_in_seconds": seconds})
    problem(response, 422, "VALIDATION_ERROR")
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM invites").fetchone()[0] == 0


def test_password_models_do_not_repr_secrets():
    assert PASSWORD not in repr(LoginInput(username="synthetic", password=PASSWORD))


@pytest.mark.parametrize("resource", ["users", "invitations"])
@pytest.mark.parametrize(
    "timestamp",
    [
        "\ud800",
        "not-a-date",
        "2026-02-30T00:00:00.000000Z",
        "2026-01-01T00:00:00.000000+08:00",
        "2026-01-01\x00T00:00:00.000000Z",
    ],
)
def test_malformed_cursor_timestamp_is_stable_problem(admin, resource, timestamp, caplog):
    cursor = base64.urlsafe_b64encode(
        json.dumps(
            ["invites" if resource == "invitations" else resource, admin[2], timestamp, admin[2]]
        ).encode()
    ).decode()
    response = admin[0].get(f"/api/v1/admin/{resource}", params={"cursor": cursor})
    problem(response, 400, "VALIDATION_ERROR")
    assert cursor not in response.text + caplog.text
    assert "request_failed" not in caplog.text


def test_invite_pagination_equal_timestamps_no_duplicates(admin, database):
    ids = {invite(admin)["invite"]["id"] for _ in range(3)}
    with database.transaction() as connection:
        connection.execute("UPDATE invites SET created_at = '2026-01-01T00:00:00.000000Z'")
    seen = []
    cursor = None
    for _ in range(3):
        params = {"limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        response = admin[0].get("/api/v1/admin/invitations", params=params)
        assert response.status_code == 200
        schema("InvitePage", response.json())
        seen.extend(item["id"] for item in response.json()["items"])
        cursor = response.json()["next_cursor"]
    assert cursor is None
    assert seen == sorted(ids, reverse=True)


def test_cursor_never_substitutes_for_current_admin_authorization(admin, user, browsers):
    first = admin[0].get("/api/v1/admin/users", params={"limit": 1})
    cursor = first.json()["next_cursor"]
    assert cursor
    for value in (cursor, "bad!"):
        problem(user[0].get("/api/v1/admin/users", params={"cursor": value}), 403, "FORBIDDEN")
        problem(
            browsers().get("/api/v1/admin/users", params={"cursor": value}),
            401,
            "AUTH_REQUIRED",
        )
    assert post(admin[0], "/auth/logout", admin[1]).status_code == 204
    problem(admin[0].get("/api/v1/admin/users", params={"cursor": cursor}), 401, "AUTH_REQUIRED")


@pytest.mark.parametrize("operation", ["register", "reset"])
def test_token_revoked_during_password_hash_cannot_be_consumed(
    admin, user, browsers, database, monkeypatch, operation
):
    issued = invite(admin) if operation == "register" else None
    raw = token_from(issued["invite_url"]) if issued else issue_reset(admin, user[2])
    client = browsers()
    context = csrf(client)
    hashed = threading.Event()
    release = threading.Event()
    original_hash = auth_service.hash_password

    def paused_hash(password):
        result = original_hash(password)
        hashed.set()
        assert release.wait(timeout=10)
        return result

    monkeypatch.setattr(auth_service, "hash_password", paused_hash)
    if operation == "register":
        route = "/auth/register"
        payload = {"username": "late_user", "password": PASSWORD, "invite_token": raw}
    else:
        route = "/auth/password-resets/complete"
        payload = {"reset_token": raw, "new_password": NEW_PASSWORD}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post, client, route, context, payload)
        assert hashed.wait(timeout=10)
        try:
            if issued:
                assert (
                    post(
                        admin[0], f"/admin/invitations/{issued['invite']['id']}/revoke", admin[1]
                    ).status_code
                    == 200
                )
            else:
                replacement = issue_reset(admin, user[2])
                assert replacement != raw
        finally:
            release.set()
        problem(future.result(), 400, "TOKEN_INVALID")
    with database.connect() as connection:
        if issued:
            assert (
                connection.execute("SELECT 1 FROM users WHERE username = 'late_user'").fetchone()
                is None
            )
            assert (
                connection.execute(
                    "SELECT status FROM invites WHERE id = ?", (issued["invite"]["id"],)
                ).fetchone()[0]
                == "revoked"
            )
        else:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user[2],)).fetchone()
            assert PASSWORD_HASHER.verify(row["password_hash"], PASSWORD)
            assert row["auth_epoch"] == 0
            assert user[0].get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("operation", ["reset", "disable"])
def test_revocation_audit_failure_rolls_back_all_access_changes(
    admin, user, browsers, database, monkeypatch, operation
):
    raw = issue_reset(admin, user[2])
    client = browsers()
    context = csrf(client)
    with database.connect() as connection:
        original = dict(
            connection.execute("SELECT * FROM users WHERE id = ?", (user[2],)).fetchone()
        )
    original_audit = auth_service.audit

    def failing_audit(*args):
        raise RuntimeError("synthetic-audit-failure")

    monkeypatch.setattr(auth_service, "audit", failing_audit)
    if operation == "reset":
        response = post(
            client,
            "/auth/password-resets/complete",
            context,
            {"reset_token": raw, "new_password": NEW_PASSWORD},
        )
    else:
        response = admin[0].patch(
            f"/api/v1/admin/users/{user[2]}",
            json={"status": "disabled"},
            headers=headers(admin[1]),
        )
    problem(response, 500, "INTERNAL_ERROR")
    assert "synthetic-audit-failure" not in response.text
    with database.connect() as connection:
        current = dict(
            connection.execute("SELECT * FROM users WHERE id = ?", (user[2],)).fetchone()
        )
        assert current == original
    assert user[0].get("/api/v1/auth/me").status_code == 200
    monkeypatch.setattr(auth_service, "audit", original_audit)
    assert (
        post(
            client,
            "/auth/password-resets/complete",
            context,
            {"reset_token": raw, "new_password": NEW_PASSWORD},
        ).status_code
        == 204
    )
    problem(user[0].get("/api/v1/auth/me"), 401, "AUTH_REQUIRED")
