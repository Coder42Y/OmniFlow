"""SQLite 短连接、显式事务和可审计的前向迁移基础，不提供降级/清库命令。"""

import hashlib
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .adapter_schema import ADAPTER_STATEMENTS
from .artifact_schema import ARTIFACT_STATEMENTS
from .auth_schema import AUTH_STATEMENTS
from .cli_process_schema import CLI_PROCESS_STATEMENTS
from .config import Settings
from .conversation_schema import CONVERSATION_STATEMENTS
from .invitation_schema import INVITATION_STATEMENTS
from .provider_limit_schema import PROVIDER_LIMIT_STATEMENTS
from .task_schema import TASK_STATEMENTS

APPLICATION_ID = 0x4F4D4E49


class StorageError(RuntimeError):
    """仅使用固定错误描述，不包含数据库路径或原始 SQLite 错误。"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n".join((str(self.version), self.name, *self.statements))
        return hashlib.sha256(payload.encode()).hexdigest()


# 后续阶段只能追加迁移；已执行条目不得改写。业务表由所属阶段增加。
MIGRATIONS = (
    Migration(
        1,
        "foundation",
        (
            "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT",
            "INSERT INTO app_metadata (key, value) VALUES ('application', 'omniflow')",
        ),
    ),
    Migration(2, "accounts_and_admin", AUTH_STATEMENTS),
    Migration(3, "conversations_runs_events", CONVERSATION_STATEMENTS),
    Migration(4, "durable_cli_process_ownership", CLI_PROCESS_STATEMENTS),
    Migration(5, "immutable_artifacts_and_delivery", ARTIFACT_STATEMENTS),
    Migration(6, "durable_media_tasks", TASK_STATEMENTS),
    Migration(7, "adapter_result_receipts", ADAPTER_STATEMENTS),
    Migration(8, "durable_provider_limits", PROVIDER_LIMIT_STATEMENTS),
    Migration(9, "nonexpiring_and_local_invitations", INVITATION_STATEMENTS),
    Migration(
        10,
        "explicit_reusable_invitations",
        (
            """CREATE TABLE reusable_invites (
                invitation_id TEXT PRIMARY KEY REFERENCES invites(id),
                enabled_at TEXT NOT NULL
            ) STRICT""",
        ),
    ),
)


def _no_symlinks(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise StorageError("数据目录或文件不允许符号链接")


def _private_directory(path: Path) -> None:
    _no_symlinks(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise StorageError("数据目录必须为仅当前用户可访问的目录")


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.database_path

    def initialize_storage(self) -> None:
        """仅显式 migrate 调用；普通请求和应用导入不创建文件。"""
        _private_directory(self.settings.data_dir)
        _private_directory(self.settings.media_dir)
        _no_symlinks(self.path)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not self.path.is_file() or self.path.stat().st_mode & 0o077:
                raise StorageError("数据库必须为仅当前用户可访问的普通文件") from None
        else:
            os.close(fd)

    @contextmanager
    def connect(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        _no_symlinks(self.path)
        connection = sqlite3.connect(
            self.path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
            uri=True,
            timeout=self.settings.sqlite_busy_timeout_ms / 1000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.settings.sqlite_busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            if readonly:
                connection.execute("PRAGMA query_only = ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        """显式读事务：鉴权、资源及事件水位来自同一 SQLite 快照。"""
        with self.connect(readonly=True) as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            finally:
                connection.rollback()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """拿写锁后做短事务；不得在此上下文等待网络、CLI 或媒体生成。"""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _history(connection: sqlite3.Connection, *, allow_empty: bool) -> int:
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        objects = connection.execute(
            "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {row[0] for row in objects if row[1] == "table"}
        if app_id == 0 and not objects and allow_empty:
            return 0
        if app_id != APPLICATION_ID or "schema_migrations" not in tables:
            raise StorageError("拒绝使用未识别的数据库")
        rows = connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        if not rows or len(rows) > len(MIGRATIONS):
            raise StorageError("数据库版本不兼容，禁止自动降级")
        for row, migration in zip(rows, MIGRATIONS, strict=False):
            if tuple(row) != (migration.version, migration.name, migration.checksum):
                raise StorageError("迁移历史不一致，禁止自动修补")
        marker = connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'application'"
        ).fetchone()
        if marker is None or marker[0] != "omniflow":
            raise StorageError("数据库身份标记不一致")
        return len(rows)

    def migrate(self) -> int:
        self.initialize_storage()
        if [m.version for m in MIGRATIONS] != list(range(1, len(MIGRATIONS) + 1)):
            raise StorageError("迁移版本必须连续且唯一")
        with self.transaction() as connection:
            current = self._history(connection, allow_empty=True)
            if current == 0:
                connection.execute(
                    "CREATE TABLE schema_migrations ("
                    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
                    "applied_at TEXT NOT NULL DEFAULT "
                    "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))) STRICT"
                )
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            for migration in MIGRATIONS[current:]:
                for statement in migration.statements:
                    # 不用 executescript，它会提前提交事务破坏迁移原子性。
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                    (migration.version, migration.name, migration.checksum),
                )
        self._enable_wal()
        return len(MIGRATIONS)

    def _enable_wal(self) -> None:
        # journal_mode 的锁升级不总是服从 busy_timeout；仅重试这个本地幂等设置。
        # 不重放已提交的迁移，也绝不将此策略用于真实提供方提交。
        deadline = time.monotonic() + self.settings.sqlite_busy_timeout_ms / 1000
        with self.connect() as connection:
            while True:
                try:
                    mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                except sqlite3.OperationalError as exc:
                    if (
                        getattr(exc, "sqlite_errorcode", 0) & 0xFF
                        not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.01)
                else:
                    if mode != "wal":
                        raise StorageError("无法启用 SQLite WAL")
                    return

    def check_ready(self) -> None:
        """只核对本地必需组件；不测试、更不推断真实提供方可用性。"""
        _no_symlinks(self.settings.media_dir)
        if (
            not self.settings.data_dir.is_dir()
            or self.settings.data_dir.stat().st_mode & 0o077
            or not self.settings.media_dir.is_dir()
            or not os.access(self.settings.media_dir, os.R_OK | os.W_OK | os.X_OK)
            or self.settings.media_dir.stat().st_mode & 0o077
            or not self.path.is_file()
            or self.path.stat().st_mode & 0o077
        ):
            raise StorageError("本地存储未就绪")
        if shutil.disk_usage(self.settings.data_dir).free < self.settings.min_free_disk_bytes:
            raise StorageError("本地存储空间不足")
        with self.connect(readonly=True) as connection:
            if self._history(connection, allow_empty=False) != len(MIGRATIONS):
                raise StorageError("存在尚未执行的迁移")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                raise StorageError("数据库日志模式未就绪")
