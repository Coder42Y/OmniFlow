"""账户事务服务：一次性消费、授权复核和审计与业务修改一起提交。"""

import base64
import json
import secrets
import sqlite3
import time
from datetime import datetime
from uuid import UUID, uuid4

from . import auth_models as models
from .auth_security import (
    DUMMY_HASH,
    AuthContext,
    csrf_token,
    digest,
    expiry,
    fail,
    hash_password,
    normalized_username,
    now,
    require_admin,
    require_context,
    require_session,
    revoke_user_access,
    validate_credentials,
    validate_password,
    verify_password,
)
from .db import Database
from .problems import ProblemError


def public_user(row: sqlite3.Row) -> models.User:
    return models.User(**{k: row[k] for k in models.User.model_fields})


def public_invite(row: sqlite3.Row) -> models.Invite:
    data = {k: row[k] for k in models.Invite.model_fields}
    if (
        data["status"] == "active"
        and data["expires_at"] is not None
        and data["expires_at"] <= now()
    ):
        data["status"] = "expired"
    return models.Invite(**data)


def audit(connection, actor: str | None, action: str, target: str) -> None:
    # 固定 action + UUID；不保存 body、链接、Cookie、用户名、备注或异常原文。
    connection.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
        (str(uuid4()), actor, action, target, now()),
    )


def read_token(connection, purpose: str, raw: str):
    table = {"invite": "invites", "reset": "password_reset_tokens"}[purpose]
    expiry_check = (
        "(expires_at IS NULL OR expires_at > ?)" if purpose == "invite" else "expires_at > ?"
    )
    row = connection.execute(
        f"SELECT * FROM {table} WHERE token_hash = ? AND status = 'active' AND {expiry_check}",
        (digest(purpose, raw), now()),
    ).fetchone()
    if row is None:
        fail(400, "TOKEN_INVALID", "链接无效或已失效")
    return row


class AuthService:
    def __init__(self, database: Database):
        self.database = database
        self.settings = database.settings

    def rate_limit(self, peer: str, *, username: str | None = None, scope: str = "form") -> None:
        """短窗口防爆破，跨进程/重启共享；不信任 X-Forwarded-For，不是业务额度。"""
        timestamp = int(time.time())
        window = self.settings.auth_rate_window_seconds
        buckets = [(digest("rate:ip:" + scope, peer), self.settings.auth_rate_ip_attempts)]
        if username is not None:
            buckets.append(
                (
                    digest("rate:username", normalized_username(username)),
                    self.settings.auth_rate_username_attempts,
                )
            )
        retry_after = 0
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM auth_rate_limits WHERE window_started <= ?", (timestamp - window,)
            )
            for key, maximum in buckets:
                row = connection.execute(
                    "SELECT * FROM auth_rate_limits WHERE bucket_hash = ?", (key,)
                ).fetchone()
                if row is not None and row["attempts"] >= maximum:
                    retry_after = max(retry_after, row["window_started"] + window - timestamp)
            if not retry_after:
                for key, _ in buckets:
                    connection.execute(
                        """INSERT INTO auth_rate_limits VALUES (?, ?, 1)
                        ON CONFLICT(bucket_hash) DO UPDATE SET attempts = attempts + 1""",
                        (key, timestamp),
                    )
        if retry_after:
            raise ProblemError(
                429,
                "RATE_LIMITED",
                "请求过于频繁",
                "请稍后再试。",
                retryable=True,
                retry_after=retry_after,
            )

    def bootstrap_csrf(self, context: AuthContext | None):
        with self.database.transaction() as connection:
            if context is not None:
                try:
                    expires_at = require_context(connection, context)
                    return models.CsrfToken(
                        csrf_token=csrf_token(context.raw), expires_at=expires_at
                    ), None
                except ProblemError:
                    pass
            connection.execute("DELETE FROM csrf_contexts WHERE expires_at <= ?", (now(),))
            raw = secrets.token_urlsafe(32)
            expires_at = expiry(self.settings.csrf_ttl_seconds)
            connection.execute(
                "INSERT INTO csrf_contexts VALUES (?, ?)", (digest("csrf", raw), expires_at)
            )
        return models.CsrfToken(csrf_token=csrf_token(raw), expires_at=expires_at), raw

    def _login(self, connection, user, context):
        # 原上下文在同一事务中消费，阻止并发登录产生两个新会话。
        require_context(connection, context)
        if context.kind == "session":
            connection.execute(
                "DELETE FROM login_sessions WHERE token_hash = ?", (context.token_hash,)
            )
        else:
            connection.execute(
                "DELETE FROM csrf_contexts WHERE token_hash = ?", (context.token_hash,)
            )
        raw = secrets.token_urlsafe(32)
        expires_at = expiry(self.settings.session_ttl_seconds)
        connection.execute("DELETE FROM login_sessions WHERE expires_at <= ?", (now(),))
        connection.execute(
            "INSERT INTO login_sessions VALUES (?, ?, ?, ?)",
            (digest("session", raw), user["id"], now(), expires_at),
        )
        audit(connection, user["id"], "auth.login", user["id"])
        return models.LoginResult(
            user=public_user(user), session_expires_at=expires_at, csrf_token=csrf_token(raw)
        ), raw

    def login(self, data: models.LoginInput, context: AuthContext):
        with self.database.connect(readonly=True) as connection:
            require_context(connection, context)
            user = connection.execute(
                "SELECT * FROM users WHERE username = ?", (normalized_username(data.username),)
            ).fetchone()
        matched = verify_password(user["password_hash"] if user else DUMMY_HASH, data.password)
        if not matched or user is None:
            fail(401, "INVALID_CREDENTIALS", "账号或密码错误")
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user["id"],)
            ).fetchone()
            if (
                current["password_hash"] != user["password_hash"]
                or current["auth_epoch"] != user["auth_epoch"]
            ):
                fail(401, "INVALID_CREDENTIALS", "账号或密码错误")
            if current["status"] != "active":
                fail(403, "ACCOUNT_DISABLED", "账号已停用")
            return self._login(connection, current, context)

    def register(self, data: models.RegisterInput, context: AuthContext):
        username = validate_credentials(self.settings, data.username, data.password)
        with self.database.connect(readonly=True) as connection:
            require_context(connection, context)
            read_token(connection, "invite", data.invite_token)
        password_hash = hash_password(data.password)
        with self.database.transaction() as connection:
            require_context(connection, context)
            invite = read_token(connection, "invite", data.invite_token)
            user_id = str(uuid4())
            try:
                connection.execute(
                    """INSERT INTO users (id, username, password_hash, role, status, created_at)
                    VALUES (?, ?, ?, 'user', 'active', ?)""",
                    (user_id, username, password_hash, now()),
                )
            except sqlite3.IntegrityError:
                if connection.execute(
                    "SELECT 1 FROM users WHERE username = ?", (username,)
                ).fetchone():
                    fail(409, "USERNAME_UNAVAILABLE", "此用户名不可用")
                raise
            reusable = connection.execute(
                "SELECT 1 FROM reusable_invites WHERE invitation_id = ?", (invite["id"],)
            ).fetchone()
            if reusable is None:
                connection.execute(
                    "UPDATE invites SET status = 'used', used_by = ? WHERE id = ?",
                    (user_id, invite["id"]),
                )
            user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            audit(connection, user_id, "auth.register", user_id)
            return self._login(connection, user, context)

    def validate_token(self, purpose, raw, context):
        with self.database.connect(readonly=True) as connection:
            require_context(connection, context)
            row = read_token(connection, purpose, raw)
            result = models.InviteTokenValidity if purpose == "invite" else models.TokenValidity
            return result(valid=True, expires_at=row["expires_at"])

    def logout(self, context):
        with self.database.transaction() as connection:
            user = require_session(connection, context)
            connection.execute(
                "DELETE FROM login_sessions WHERE token_hash = ?", (context.token_hash,)
            )
            audit(connection, user["id"], "auth.logout", user["id"])

    def complete_reset(self, data: models.PasswordResetInput, context):
        validate_password(self.settings, data.new_password, "new_password")
        with self.database.connect(readonly=True) as connection:
            require_context(connection, context)
            read_token(connection, "reset", data.reset_token)
        password_hash = hash_password(data.new_password)
        with self.database.transaction() as connection:
            require_context(connection, context)
            token = read_token(connection, "reset", data.reset_token)
            connection.execute(
                "UPDATE password_reset_tokens SET status = 'used' WHERE id = ?", (token["id"],)
            )
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, token["user_id"])
            )
            revoke_user_access(connection, token["user_id"])
            audit(connection, token["user_id"], "auth.password_reset", token["user_id"])

    def create_admin(self, username: str, password: str) -> models.User:
        """仅供本地显式命令，路由不得暴露角色提升。"""
        username = validate_credentials(self.settings, username, password)
        password_hash = hash_password(password)
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                fail(409, "USERNAME_UNAVAILABLE", "此用户名不可用")
            user_id = str(uuid4())
            connection.execute(
                """INSERT INTO users (id, username, password_hash, role, status, created_at)
                VALUES (?, ?, ?, 'admin', 'active', ?)""",
                (user_id, username, password_hash, now()),
            )
            audit(connection, None, "admin.created_locally", user_id)
            return public_user(
                connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            )

    def issue_invite(self, data: models.InviteCreate, context):
        return self._issue_invite(data, context, locally=False)

    def issue_invite_locally(self, data: models.InviteCreate):
        """仅供显式本机管理命令；普通用户邀请，不提供公开免登录入口。"""
        self.database.check_ready()
        return self._issue_invite(data, None, locally=True)

    def _issue_invite(self, data, context, *, locally):
        seconds = (
            data.expires_in_seconds
            if "expires_in_seconds" in data.model_fields_set
            else self.settings.invitation_ttl_seconds
        )
        expires_at = None if seconds is None else expiry(seconds)
        raw, invitation_id = secrets.token_urlsafe(32), str(uuid4())
        with self.database.transaction() as connection:
            actor = None if locally else require_admin(connection, context)["id"]
            connection.execute(
                """INSERT INTO invites VALUES (?, ?, ?, NULL, 'active', ?, ?)""",
                (invitation_id, digest("invite", raw), actor, now(), expires_at),
            )
            action = "admin.invite_issued_locally" if locally else "admin.invite_issued"
            audit(connection, actor, action, invitation_id)
            invite = public_invite(
                connection.execute(
                    "SELECT * FROM invites WHERE id = ?", (invitation_id,)
                ).fetchone()
            )
        return models.InviteIssued(
            invite=invite, invite_url=f"{self.settings.public_origin}/register#token={raw}"
        )

    def revoke_invite(self, invitation_id: str, context):
        return self._revoke_invite(invitation_id, context, locally=False)

    def make_invite_reusable_locally(self, invitation_id: str):
        """显式本机操作，只改变指定邀请；不复活撤销或到期链接。"""
        self.database.check_ready()
        invitation_id = str(UUID(invitation_id))
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM invites WHERE id = ?", (invitation_id,)
            ).fetchone()
            if row is None:
                fail(404, "RESOURCE_NOT_FOUND", "邀请不存在")
            if row["status"] == "revoked" or (
                row["expires_at"] is not None and row["expires_at"] <= now()
            ):
                fail(409, "RESOURCE_IN_USE", "已撤销或到期邀请不能启用重复注册")
            connection.execute(
                "INSERT INTO reusable_invites VALUES (?, ?) ON CONFLICT(invitation_id) DO NOTHING",
                (invitation_id, now()),
            )
            # 已用的一次性邀请可按明确授权启用复用，保留其首次使用者记录。
            connection.execute(
                "UPDATE invites SET status = 'active' WHERE id = ?", (invitation_id,)
            )
            audit(connection, None, "admin.invite_reuse_enabled_locally", invitation_id)

    def revoke_invite_locally(self, invitation_id: str):
        self.database.check_ready()
        return self._revoke_invite(str(UUID(invitation_id)), None, locally=True)

    def _revoke_invite(self, invitation_id, context, *, locally):
        with self.database.transaction() as connection:
            actor = None if locally else require_admin(connection, context)["id"]
            row = connection.execute(
                "SELECT * FROM invites WHERE id = ?", (invitation_id,)
            ).fetchone()
            if row is None:
                fail(404, "RESOURCE_NOT_FOUND", "资源不存在或当前用户不可访问")
            if row["status"] == "used":
                fail(409, "RESOURCE_IN_USE", "邀请已被消费")
            connection.execute(
                "UPDATE invites SET status = 'revoked' WHERE id = ?", (invitation_id,)
            )
            action = "admin.invite_revoked_locally" if locally else "admin.invite_revoked"
            audit(connection, actor, action, invitation_id)
            return public_invite(
                connection.execute(
                    "SELECT * FROM invites WHERE id = ?", (invitation_id,)
                ).fetchone()
            )

    def set_user_status(self, user_id: str, status: str, context):
        with self.database.transaction() as connection:
            admin = require_admin(connection, context)
            user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if user is None:
                fail(404, "RESOURCE_NOT_FOUND", "资源不存在或当前用户不可访问")
            if status == "disabled" and user["status"] == "active":
                if (
                    user["role"] == "admin"
                    and connection.execute(
                        "SELECT count(*) FROM users WHERE role = 'admin' AND status = 'active'"
                    ).fetchone()[0]
                    <= 1
                ):
                    fail(409, "RESOURCE_IN_USE", "不能停用唯一启用的管理员")
                revoke_user_access(connection, user_id)
            connection.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
            audit(connection, admin["id"], "admin.user_" + status, user_id)
            return public_user(
                connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            )

    def issue_reset(self, user_id: str, data: models.PasswordResetIssue, context):
        raw, token_id = secrets.token_urlsafe(32), str(uuid4())
        expires_at = expiry(self.settings.password_reset_ttl_seconds)
        with self.database.transaction() as connection:
            admin = require_admin(connection, context)
            if (
                connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
                is None
            ):
                fail(404, "RESOURCE_NOT_FOUND", "资源不存在或当前用户不可访问")
            connection.execute(
                "UPDATE password_reset_tokens SET status = 'revoked' "
                "WHERE user_id = ? AND status = 'active'",
                (user_id,),
            )
            connection.execute(
                "INSERT INTO password_reset_tokens VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    token_id,
                    digest("reset", raw),
                    user_id,
                    admin["id"],
                    data.verification_method,
                    now(),
                    expires_at,
                ),
            )
            audit(connection, admin["id"], "admin.reset_issued", user_id)
        return models.PasswordResetIssued(
            id=token_id,
            expires_at=expires_at,
            reset_url=f"{self.settings.public_origin}/reset-password#token={raw}",
        )

    def list_metadata(self, resource: str, cursor: str | None, limit: int, context):
        # 游标绑定资源、管理员身份和固定排序；不是授权凭据，数据库查询仍重新鉴权。
        table, project = {"users": ("users", public_user), "invites": ("invites", public_invite)}[
            resource
        ]
        with self.database.connect(readonly=True) as connection:
            admin = require_admin(connection, context)
            params = []
            where = ""
            if cursor is not None:
                try:
                    decoded = json.loads(
                        base64.b64decode(cursor.encode(), altchars=b"-_", validate=True)
                    )
                    if (
                        not isinstance(decoded, list)
                        or len(decoded) != 4
                        or decoded[:2] != [resource, admin["id"]]
                        or not isinstance(decoded[2], str)
                        or len(decoded[2]) > 32
                        or not isinstance(decoded[3], str)
                    ):
                        raise ValueError
                    # 游标虽非授权凭据，其排序值仍是不可信输入。仅接受本服务
                    # now() 签发的 UTC 微秒格式，避免坏日期/代理字符进入 SQLite。
                    timestamp = datetime.fromisoformat(decoded[2])
                    if (
                        not decoded[2].endswith("Z")
                        or timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
                        != decoded[2]
                        or str(UUID(decoded[3])) != decoded[3]
                    ):
                        raise ValueError
                    where = " WHERE (created_at, id) < (?, ?)"
                    params = decoded[2:]
                except (ValueError, TypeError, UnicodeError):
                    fail(400, "VALIDATION_ERROR", "分页游标无效")
            rows = connection.execute(
                f"SELECT * FROM {table}{where} ORDER BY created_at DESC, id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps(
                        [resource, admin["id"], last["created_at"], last["id"]],
                        separators=(",", ":"),
                    ).encode()
                ).decode()
            return {"items": [project(r) for r in rows[:limit]], "next_cursor": next_cursor}

    def generation_policy(self, context, patch: models.GenerationPolicyPatch | None = None):
        manager = self.database.transaction if patch is not None else self.database.connect
        with manager() as connection:
            admin = require_admin(connection, context)
            if patch is not None:
                fields = patch.model_dump(exclude_unset=True)
                # 字段来自 extra=forbid 模型白名单，不能将任意输入作为 SQL 标识符。
                assignments = ", ".join(f"{key} = ?" for key in fields)
                connection.execute(
                    f"UPDATE generation_policy SET {assignments}, updated_at = ? "
                    "WHERE singleton = 1",
                    (*fields.values(), now()),
                )
                audit(connection, admin["id"], "admin.generation_policy_updated", admin["id"])
            row = connection.execute(
                "SELECT * FROM generation_policy WHERE singleton = 1"
            ).fetchone()
            return models.GenerationPolicy(
                **{k: row[k] for k in models.GenerationPolicy.model_fields}
            )
