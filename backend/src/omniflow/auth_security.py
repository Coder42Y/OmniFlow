"""Cookie 凭据仅保存摘要；CSRF 从高熵 Cookie 派生，不是登录凭据。

后续长连接须周期性重新调用 require_session；写业务需在同一写事务重新校验。
auth_epoch 用于让重置/停用之后已签发的媒体授权永久失效，启用账号不能复活旧授权。
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError
from fastapi import Request

from .config import Settings
from .problems import FieldError, ProblemError

PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
HASH_SLOTS = threading.BoundedSemaphore(4)
# 仅用于不存在用户时的等成本校验；随机不可登录，绝不是默认账号密码。
DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def expiry(seconds: int) -> str:
    try:
        return (
            (datetime.now(UTC) + timedelta(seconds=seconds))
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    except (OverflowError, ValueError):
        invalid_field("expires_in_seconds")


def fail(status: int, code: str, message: str) -> None:
    raise ProblemError(status, code, message, message + "。")


def invalid_field(name: str) -> None:
    raise ProblemError(
        422,
        "VALIDATION_ERROR",
        "请求参数无效",
        "字段不符合公开账号策略。",
        errors=[FieldError(field=name, code="INVALID", message="字段不符合公开账号策略。")],
    )


def digest(purpose: str, raw: str) -> str:
    return hashlib.sha256((purpose + ":" + raw).encode()).hexdigest()


def csrf_token(raw: str) -> str:
    return hmac.new(raw.encode(), b"omniflow:csrf:v1", hashlib.sha256).hexdigest()


def normalized_username(raw: str) -> str:
    # 不把非 ASCII 字符（例如 Kelvin 符号）折叠成合法的 ASCII 登录名。
    return raw.strip(" ").lower() if raw.isascii() else raw.strip(" ")


def validate_credentials(settings: Settings, username: str, password: str) -> str:
    username = normalized_username(username)
    if not re.fullmatch(settings.username_pattern, username):
        invalid_field("username")
    validate_password(settings, password)
    return username


def validate_password(settings: Settings, password: str, field_name: str = "password") -> None:
    if not settings.password_min_length <= len(password) <= settings.password_max_length:
        invalid_field(field_name)


def hash_password(password: str) -> str:
    with HASH_SLOTS:
        return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    with HASH_SLOTS:
        try:
            return PASSWORD_HASHER.verify(password_hash, password)
        except VerificationError:
            return False


@dataclass(frozen=True)
class AuthContext:
    kind: str
    token_hash: str = field(repr=False)
    raw: str = field(repr=False)


def cookie_context(request: Request) -> AuthContext | None:
    settings = request.app.state.settings
    # 浏览器正常不应发送重复的 __Host Cookie；拒绝含糊身份而不是任取其一。
    raw_cookie = ";".join(request.headers.getlist("cookie"))
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        if sum(part.strip().partition("=")[0] == name for part in raw_cookie.split(";")) > 1:
            fail(403, "CSRF_INVALID", "安全上下文无效")
    for name, kind in (
        (settings.session_cookie_name, "session"),
        (settings.csrf_cookie_name, "csrf"),
    ):
        raw = request.cookies.get(name)
        if raw is not None:
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
                return None
            return AuthContext(kind, digest(kind, raw), raw)
    return None


def require_session(connection: sqlite3.Connection, context: AuthContext | None) -> sqlite3.Row:
    if context is None or context.kind != "session":
        fail(401, "AUTH_REQUIRED", "请先登录")
    row = connection.execute(
        """SELECT u.*, s.expires_at AS session_expires_at FROM users u
        JOIN login_sessions s ON s.user_id = u.id
        WHERE s.token_hash = ? AND s.expires_at > ? AND u.status = 'active'""",
        (context.token_hash, now()),
    ).fetchone()
    if row is None:
        fail(401, "AUTH_REQUIRED", "请先登录")
    return row


def require_context(connection: sqlite3.Connection, context: AuthContext | None) -> str:
    if context is None:
        fail(403, "CSRF_INVALID", "安全上下文无效")
    if context.kind == "session":
        return require_session(connection, context)["session_expires_at"]
    row = connection.execute(
        "SELECT expires_at FROM csrf_contexts WHERE token_hash = ? AND expires_at > ?",
        (context.token_hash, now()),
    ).fetchone()
    if row is None:
        fail(403, "CSRF_INVALID", "安全上下文无效")
    return row["expires_at"]


def require_admin(connection: sqlite3.Connection, context: AuthContext | None) -> sqlite3.Row:
    user = require_session(connection, context)
    if user["role"] != "admin":
        fail(403, "FORBIDDEN", "需要管理员权限")
    return user


def check_csrf(request: Request, context: AuthContext | None) -> None:
    origins = request.headers.getlist("origin")
    tokens = request.headers.getlist("x-csrf-token")
    if (
        len(origins) != 1
        or origins[0] not in request.app.state.settings.origins
        or context is None
        or len(tokens) != 1
        or len(tokens[0]) != 64
        or not hmac.compare_digest(tokens[0].encode(), csrf_token(context.raw).encode())
    ):
        fail(403, "CSRF_INVALID", "安全上下文或令牌无效")


def revoke_user_access(connection: sqlite3.Connection, user_id: str) -> None:
    connection.execute("DELETE FROM login_sessions WHERE user_id = ?", (user_id,))
    connection.execute("UPDATE users SET auth_epoch = auth_epoch + 1 WHERE id = ?", (user_id,))


def require_generation_allowed(connection: sqlite3.Connection, user_id: str, kind: str) -> None:
    """后续入队/实际提交均在自己的事务内调用；不能替代提供方费用保护。"""
    columns = {
        "text": "text_enabled",
        "image": "image_enabled",
        "ai_video": "ai_video_enabled",
        "local_motion": "local_motion_enabled",
    }
    if kind not in columns:
        raise ValueError("未知生成类型")
    user = connection.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None or user["status"] != "active":
        fail(403, "ACCOUNT_DISABLED", "账号不可执行新增生成")
    policy = connection.execute("SELECT * FROM generation_policy WHERE singleton = 1").fetchone()
    if not policy[columns[kind]]:
        fail(503, "GENERATION_PAUSED", "此类新增生成已暂停")
