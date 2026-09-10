"""追加迁移：无到期时间及受信任本机签发；保留所有旧邀请。"""

INVITATION_STATEMENTS = (
    """CREATE TABLE invites_v9 (
        id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
        created_by TEXT REFERENCES users(id),
        used_by TEXT REFERENCES users(id),
        status TEXT NOT NULL CHECK(status IN ('active', 'used', 'revoked')),
        created_at TEXT NOT NULL, expires_at TEXT
    ) STRICT""",
    "INSERT INTO invites_v9 SELECT * FROM invites",
    "DROP TABLE invites",
    "ALTER TABLE invites_v9 RENAME TO invites",
)
