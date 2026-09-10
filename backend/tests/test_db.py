import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

import omniflow.db as db_module
from omniflow.config import Settings
from omniflow.db import APPLICATION_ID, MIGRATIONS, Database, Migration, StorageError


def test_migrations_repeat_and_persist_after_new_instance(settings):
    database = Database(settings)
    assert not database.path.exists()
    assert database.migrate() == len(MIGRATIONS)
    with database.connect(readonly=True) as connection:
        first = [tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")]
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    fresh = Database(settings)
    assert fresh.migrate() == len(MIGRATIONS)
    fresh.check_ready()
    with fresh.connect(readonly=True) as connection:
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM schema_migrations")
        ] == first
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO app_metadata VALUES ('forbidden', 'write')")
    assert settings.data_dir.stat().st_mode & 0o777 == 0o700
    assert settings.media_dir.stat().st_mode & 0o777 == 0o700
    assert settings.database_path.stat().st_mode & 0o777 == 0o600


def test_transaction_rollback_foreign_keys_uniqueness_and_close(database):
    with database.transaction() as connection:
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child (id INTEGER REFERENCES parent(id), name TEXT UNIQUE)"
        )
        connection.execute("INSERT INTO parent VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute("INSERT INTO child VALUES (99, 'test')")
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        connection.execute("INSERT INTO child VALUES (1, 'test')")
        connection.execute("INSERT INTO child VALUES (1, 'test')")
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM child").fetchone()[0] == 0


def test_concurrent_migrations_apply_once(settings):
    # 预创建私有目录以免 mkdir 测试与 SQLite 测试混为一谈。
    Database(settings).initialize_storage()
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert (
            list(executor.map(lambda _: Database(settings).migrate(), range(8)))
            == [len(MIGRATIONS)] * 8
        )
    with Database(settings).connect() as connection:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            )
        ] == [(m.version, m.name, m.checksum) for m in MIGRATIONS]


def test_failed_migration_rolls_back_schema_data_and_history(database, monkeypatch):
    migration = Migration(
        len(MIGRATIONS) + 1,
        "synthetic_failure",
        (
            "CREATE TABLE should_rollback (id INTEGER PRIMARY KEY)",
            "INSERT INTO app_metadata VALUES ('rollback', 'test')",
            "INVALID SQL FOR TEST",
        ),
    )
    monkeypatch.setattr(db_module, "MIGRATIONS", (*MIGRATIONS, migration))
    with pytest.raises(sqlite3.OperationalError):
        database.migrate()
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'should_rollback'"
            ).fetchone()
            is None
        )
        assert (
            connection.execute("SELECT value FROM app_metadata WHERE key = 'rollback'").fetchone()
            is None
        )


def test_pending_migration_not_ready_then_forward_migrate(database, monkeypatch):
    migration = Migration(
        len(MIGRATIONS) + 1, "synthetic_next", ("CREATE TABLE next_stage (id INTEGER PRIMARY KEY)",)
    )
    monkeypatch.setattr(db_module, "MIGRATIONS", (*MIGRATIONS, migration))
    with pytest.raises(StorageError, match="尚未执行"):
        database.check_ready()
    assert database.migrate() == len(MIGRATIONS) + 1
    database.check_ready()
    assert database.migrate() == len(MIGRATIONS) + 1


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE schema_migrations SET checksum = 'tampered'",
        "UPDATE schema_migrations SET version = 99 WHERE version = 1",
        "INSERT INTO schema_migrations(version,name,checksum) "
        f"VALUES ({len(MIGRATIONS) + 1},'future','unknown')",
        "UPDATE app_metadata SET value = 'another-application'",
    ],
)
def test_modified_or_future_history_is_rejected(database, tamper):
    with database.transaction() as connection:
        connection.execute(tamper)
    with pytest.raises(StorageError):
        database.migrate()
    with pytest.raises(StorageError):
        database.check_ready()


def test_foreign_database_not_adopted_or_modified(settings):
    database = Database(settings)
    database.initialize_storage()
    with closing(sqlite3.connect(settings.database_path)) as connection, connection:
        connection.execute("CREATE TABLE legacy_data (value TEXT)")
        connection.execute("INSERT INTO legacy_data VALUES ('synthetic-preserve')")
    original = settings.database_path.read_bytes()
    with pytest.raises(StorageError, match="未识别"):
        database.migrate()
    assert settings.database_path.read_bytes() == original


def test_initial_migration_failure_leaves_no_partial_schema(settings, monkeypatch):
    database = Database(settings)
    original = MIGRATIONS[0]
    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        (Migration(1, original.name, (*original.statements, "INVALID SYNTHETIC SQL")),),
    )
    with pytest.raises(sqlite3.OperationalError):
        database.migrate()
    with database.connect() as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
    monkeypatch.setattr(db_module, "MIGRATIONS", MIGRATIONS)
    assert database.migrate() == len(MIGRATIONS)
    database.check_ready()


def test_foreign_view_only_database_is_not_adopted(settings):
    database = Database(settings)
    database.initialize_storage()
    with database.transaction() as connection:
        connection.execute("CREATE VIEW legacy_view AS SELECT 'synthetic' AS value")
    original = database.path.read_bytes()
    with pytest.raises(StorageError):
        database.migrate()
    assert database.path.read_bytes() == original


def test_readiness_detects_exposed_directory_permissions(database):
    os.chmod(database.settings.data_dir, 0o755)
    with pytest.raises(StorageError):
        database.check_ready()


def test_missing_storage_never_created_by_readiness(settings):
    database = Database(settings)
    with pytest.raises(StorageError):
        database.check_ready()
    with pytest.raises(sqlite3.OperationalError), database.connect():
        pass
    assert not settings.data_dir.exists()


@pytest.mark.parametrize("target", ["root", "media", "database"])
def test_symlinks_rejected_without_writing_target(tmp_path, target):
    outside = tmp_path / "synthetic-target"
    outside.mkdir(mode=0o700)
    settings = Settings(data_dir=tmp_path / "data")
    if target == "root":
        settings.data_dir.symlink_to(outside, target_is_directory=True)
    else:
        settings.data_dir.mkdir(mode=0o700)
        if target == "media":
            settings.media_dir.symlink_to(outside, target_is_directory=True)
        else:
            settings.database_path.symlink_to(outside / "untouched.sqlite3")
    with pytest.raises(StorageError, match="符号链接"):
        Database(settings).migrate()
    assert not list(outside.iterdir())


def test_public_storage_permissions_rejected(settings):
    settings.data_dir.mkdir(mode=0o755)
    os.chmod(settings.data_dir, 0o755)
    with pytest.raises(StorageError, match="仅当前用户"):
        Database(settings).migrate()
    assert not settings.database_path.exists()


def test_database_busy_transaction_rolls_back(database):
    short_wait = Database(Settings(data_dir=database.settings.data_dir, sqlite_busy_timeout_ms=1))
    with (
        database.transaction(),
        pytest.raises(sqlite3.OperationalError, match="locked"),
        short_wait.transaction(),
    ):
        pytest.fail("并发写事务不应获得锁")
    short_wait.check_ready()
