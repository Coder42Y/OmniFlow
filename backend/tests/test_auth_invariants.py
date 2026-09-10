"""账户事务故障注入、本地管理员入口、迁移接续及跨上下文安全。"""

import getpass
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor

import pytest

import omniflow.auth_service as service_module
import omniflow.db as db_module
from omniflow.auth_models import LoginInput, RegisterInput
from omniflow.auth_security import AuthContext, digest
from omniflow.auth_service import AuthService
from omniflow.cli import main
from omniflow.db import MIGRATIONS, Database
from omniflow.problems import ProblemError

PASSWORD = "Synthetic Local Admin 123!"


def context(auth):
    _, raw = auth.bootstrap_csrf(None)
    return AuthContext("csrf", digest("csrf", raw), raw)


def authenticated(auth, username):
    _, raw = auth.login(LoginInput(username=username, password=PASSWORD), context(auth))
    return AuthContext("session", digest("session", raw), raw)


def test_forward_v1_to_v2_keeps_history_and_data(settings, monkeypatch):
    database = Database(settings)
    monkeypatch.setattr(db_module, "MIGRATIONS", MIGRATIONS[:1])
    assert database.migrate() == 1
    with database.transaction() as connection:
        connection.execute("INSERT INTO app_metadata VALUES ('synthetic', 'preserved')")
        first = tuple(connection.execute("SELECT * FROM schema_migrations").fetchone())
    monkeypatch.setattr(db_module, "MIGRATIONS", MIGRATIONS)
    assert database.migrate() == len(MIGRATIONS)
    database.check_ready()
    with database.connect() as connection:
        assert (
            tuple(
                connection.execute("SELECT * FROM schema_migrations WHERE version = 1").fetchone()
            )
            == first
        )
        assert (
            connection.execute("SELECT value FROM app_metadata WHERE key = 'synthetic'").fetchone()[
                0
            ]
            == "preserved"
        )
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM login_sessions").fetchone()[0] == 0


def test_cli_admin_requires_explicit_interactive_hidden_password(database, monkeypatch, capsys):
    monkeypatch.setenv("OMNIFLOW_DATA_DIR", str(database.settings.data_dir))
    monkeypatch.setattr("omniflow.cli.sys.stdin.isatty", lambda: False)
    assert main(["create-admin", "--username", "owner"]) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    monkeypatch.setattr("omniflow.cli.sys.stdin.isatty", lambda: True)
    prompts = []

    def getpass(prompt):
        prompts.append(prompt)
        return PASSWORD

    monkeypatch.setattr("omniflow.cli.getpass.getpass", getpass)
    assert main(["create-admin", "--username", "owner"]) == 0
    assert len(prompts) == 2
    assert main(["create-admin", "--username", "owner"]) == 1
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM users").fetchone()
        assert row["role"] == "admin" and row["password_hash"].startswith("$argon2id$")
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM login_sessions").fetchone()[0] == 0
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err


@pytest.mark.parametrize("passwords", [(PASSWORD, "different"), ("short", "short")])
def test_cli_rejects_mismatch_and_short_password(database, monkeypatch, capsys, passwords):
    monkeypatch.setenv("OMNIFLOW_DATA_DIR", str(database.settings.data_dir))
    monkeypatch.setattr("omniflow.cli.sys.stdin.isatty", lambda: True)
    iterator = iter(passwords)
    monkeypatch.setattr("omniflow.cli.getpass.getpass", lambda _: next(iterator))
    assert main(["create-admin", "--username", "owner"]) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err


def test_cli_refuses_visible_password_fallback(database, monkeypatch, capsys):
    monkeypatch.setenv("OMNIFLOW_DATA_DIR", str(database.settings.data_dir))
    monkeypatch.setattr("omniflow.cli.sys.stdin.isatty", lambda: True)

    def no_hidden_input(_):
        warnings.warn("synthetic-no-echo-control", getpass.GetPassWarning, stacklevel=2)
        pytest.fail("不能退回可见密码输入")

    monkeypatch.setattr("omniflow.cli.getpass.getpass", no_hidden_input)
    assert main(["create-admin", "--username", "owner"]) == 1
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
    assert "synthetic-no-echo-control" not in capsys.readouterr().err


def test_cli_has_no_password_argument(database, monkeypatch, capsys):
    monkeypatch.setenv("OMNIFLOW_DATA_DIR", str(database.settings.data_dir))
    with pytest.raises(SystemExit):
        # 这里只测试选项不存在，不把任何密码放在命令行。
        main(["create-admin", "--username", "owner", "--password"])
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_register_audit_failure_rolls_back_user_invite_and_context(database, monkeypatch):
    from omniflow.auth_models import InviteCreate

    auth = AuthService(database)
    auth.create_admin("owner", PASSWORD)
    admin = authenticated(auth, "owner")
    issued = auth.issue_invite(InviteCreate(), admin)
    token = issued.invite_url.split("#token=")[1]
    anonymous = context(auth)
    data = RegisterInput(username="alice", password=PASSWORD, invite_token=token)
    original_audit = service_module.audit

    def fail_audit(*args):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(service_module, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic"):
        auth.register(data, anonymous)
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT status FROM invites").fetchone()[0] == "active"
        assert connection.execute(
            "SELECT 1 FROM csrf_contexts WHERE token_hash = ?", (anonymous.token_hash,)
        ).fetchone()
    monkeypatch.setattr(service_module, "audit", original_audit)
    result, _ = auth.register(data, anonymous)
    assert result.user.username == "alice"


def test_same_username_distinct_invites_race_leaves_losing_invite_active(database, monkeypatch):
    from omniflow.auth_models import InviteCreate

    auth = AuthService(database)
    auth.create_admin("owner", PASSWORD)
    admin = authenticated(auth, "owner")
    tokens = [
        auth.issue_invite(InviteCreate(), admin).invite_url.split("#token=")[1] for _ in range(2)
    ]
    contexts = [context(auth), context(auth)]
    barrier = threading.Barrier(2)
    original_hash = service_module.hash_password

    def paused_hash(password):
        value = original_hash(password)
        barrier.wait(timeout=10)
        return value

    monkeypatch.setattr(service_module, "hash_password", paused_hash)

    def attempt(i):
        try:
            auth.register(
                RegisterInput(username="same_user", password=PASSWORD, invite_token=tokens[i]),
                contexts[i],
            )
            return "created"
        except ProblemError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["USERNAME_UNAVAILABLE", "created"]
    with database.connect() as connection:
        assert sorted(r[0] for r in connection.execute("SELECT status FROM invites")) == [
            "active",
            "used",
        ]
        assert (
            connection.execute(
                "SELECT count(*) FROM users WHERE username = 'same_user'"
            ).fetchone()[0]
            == 1
        )


def test_login_concurrent_same_context_creates_only_one_new_session(database, monkeypatch):
    auth = AuthService(database)
    auth.create_admin("owner", PASSWORD)
    anonymous = context(auth)
    barrier = threading.Barrier(2)
    original_verify = service_module.verify_password

    def paused_verify(password_hash, password):
        result = original_verify(password_hash, password)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(service_module, "verify_password", paused_verify)

    def attempt(_):
        try:
            auth.login(LoginInput(username="owner", password=PASSWORD), anonymous)
            return "created"
        except ProblemError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["CSRF_INVALID", "created"]
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM login_sessions").fetchone()[0] == 1


def test_unknown_user_also_performs_password_verification(database, monkeypatch):
    auth = AuthService(database)
    checked = []

    def verify(password_hash, password):
        checked.append(password_hash)
        return False

    monkeypatch.setattr(service_module, "verify_password", verify)
    with pytest.raises(ProblemError, match="INVALID_CREDENTIALS"):
        auth.login(LoginInput(username="absent", password=PASSWORD), context(auth))
    assert len(checked) == 1 and checked[0].startswith("$argon2id$")
