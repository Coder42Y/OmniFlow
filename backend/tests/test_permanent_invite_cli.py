"""只在临时数据库签发合成邀请；不连接生产或供应商。"""

import json

from fastapi.testclient import TestClient

from omniflow import cli, db
from omniflow.auth_models import InviteCreate
from omniflow.auth_service import AuthService


def test_cli_nonexpiring_invite_and_revoke_without_any_admin(database, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Settings", lambda: database.settings)
    assert cli.main(["issue-invite", "--no-expiry"]) == 0
    issued = json.loads(capsys.readouterr().out)
    assert issued["invite"]["expires_at"] is None
    assert "#token=" in issued["invite_url"]
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    assert cli.main(["make-invite-reusable", issued["invite"]["id"]]) == 0
    assert "重复注册普通账号" in capsys.readouterr().out
    with database.connect(readonly=True) as connection:
        assert (
            connection.execute("SELECT invitation_id FROM reusable_invites").fetchone()[0]
            == issued["invite"]["id"]
        )
    assert cli.main(["revoke-invite", issued["invite"]["id"]]) == 0
    assert "revoked" in capsys.readouterr().out
    with database.connect(readonly=True) as connection:
        assert connection.execute("SELECT status FROM invites").fetchone()[0] == "revoked"


def test_append_only_migration_preserves_all_existing_invites(settings, monkeypatch):
    previous = db.MIGRATIONS
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", previous[:8])
        database = db.Database(settings)
        assert database.migrate() == 8
        auth = AuthService(database)
        owner = auth.create_admin("migration_owner", "synthetic-password")
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO invites VALUES (?, ?, ?, NULL, 'active', ?, ?)",
                (
                    "original-id",
                    "synthetic-hash",
                    str(owner.id),
                    "2026-01-01T00:00:00.000000Z",
                    "2027-01-01T00:00:00.000000Z",
                ),
            )
        with database.connect(readonly=True) as connection:
            before = tuple(connection.execute("SELECT * FROM invites").fetchone())
    assert database.migrate() == len(previous)
    with database.connect(readonly=True) as connection:
        assert tuple(connection.execute("SELECT * FROM invites").fetchone()) == before
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    issued = AuthService(database).issue_invite_locally(InviteCreate(expires_in_seconds=None))
    assert issued.invite.expires_at is None


def test_no_public_local_signing_route(app):
    assert all(
        "locally" not in path and "issue-invite" not in path for path in app.openapi()["paths"]
    )
    with TestClient(app, base_url="https://localhost:8443") as client:
        response = client.post(
            "/api/v1/admin/invitations",
            json={"expires_in_seconds": None},
            headers={"Origin": "https://localhost:8443"},
        )
        assert response.status_code in (401, 403)


def test_reset_expiry_remains_non_nullable():
    import pytest
    from pydantic import ValidationError

    from omniflow.auth_models import TokenValidity

    with pytest.raises(ValidationError):
        TokenValidity(valid=True, expires_at=None)
