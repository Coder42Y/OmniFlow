"""账户阶段的追加迁移；不改写 foundation 的历史校验值。"""

AUTH_STATEMENTS = (
    """CREATE TABLE users (
        id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('user', 'admin')),
        status TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
        auth_epoch INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE invites (
        id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
        created_by TEXT NOT NULL REFERENCES users(id),
        used_by TEXT REFERENCES users(id),
        status TEXT NOT NULL CHECK(status IN ('active', 'used', 'revoked')),
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE login_sessions (
        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL
    ) STRICT""",
    "CREATE INDEX login_sessions_user ON login_sessions(user_id)",
    """CREATE TABLE csrf_contexts (
        token_hash TEXT PRIMARY KEY, expires_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE password_reset_tokens (
        id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
        user_id TEXT NOT NULL REFERENCES users(id),
        created_by TEXT NOT NULL REFERENCES users(id),
        verification_method TEXT NOT NULL CHECK(verification_method IN
            ('trusted_existing_contact', 'in_person')),
        status TEXT NOT NULL CHECK(status IN ('active', 'used', 'revoked')),
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL
    ) STRICT""",
    "CREATE INDEX reset_tokens_user ON password_reset_tokens(user_id)",
    """CREATE TABLE auth_rate_limits (
        bucket_hash TEXT PRIMARY KEY, window_started INTEGER NOT NULL, attempts INTEGER NOT NULL
    ) STRICT""",
    """CREATE TABLE audit_events (
        id TEXT PRIMARY KEY, actor_id TEXT REFERENCES users(id),
        action TEXT NOT NULL, target_id TEXT NOT NULL, created_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE generation_policy (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        text_enabled INTEGER NOT NULL CHECK(text_enabled IN (0, 1)),
        image_enabled INTEGER NOT NULL CHECK(image_enabled IN (0, 1)),
        ai_video_enabled INTEGER NOT NULL CHECK(ai_video_enabled IN (0, 1)),
        local_motion_enabled INTEGER NOT NULL CHECK(local_motion_enabled IN (0, 1)),
        updated_at TEXT NOT NULL
    ) STRICT""",
    """INSERT INTO generation_policy VALUES
        (1, 1, 1, 1, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
)
