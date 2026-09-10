"""阶段 02 追加迁移；消息预留 user/assistant 相邻序号，不按生成完成时间排序。"""

CONVERSATION_STATEMENTS = (
    """CREATE TABLE conversations (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
        title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        deleted_at TEXT, last_event_id INTEGER NOT NULL DEFAULT 0,
        event_floor INTEGER NOT NULL DEFAULT 0, next_message_seq INTEGER NOT NULL DEFAULT 1,
        cli_session_id TEXT UNIQUE, manager_token TEXT, lease_until TEXT,
        UNIQUE(id, owner_id)
    ) STRICT""",
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
        user_message_id TEXT NOT NULL, assistant_message_id TEXT,
        status TEXT NOT NULL CHECK(status IN ('queued','running','stopping','completed',
            'failed','canceled','interrupted','needs_reconciliation')),
        input_json TEXT NOT NULL, input_digest TEXT NOT NULL, owner_auth_epoch INTEGER NOT NULL,
        task_ids TEXT NOT NULL DEFAULT '[]', error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(id, conversation_id), UNIQUE(user_message_id), UNIQUE(assistant_message_id),
        FOREIGN KEY(user_message_id, conversation_id) REFERENCES messages(id, conversation_id)
            DEFERRABLE INITIALLY DEFERRED,
        FOREIGN KEY(assistant_message_id, conversation_id) REFERENCES messages(id, conversation_id)
            DEFERRABLE INITIALLY DEFERRED
    ) STRICT""",
    """CREATE UNIQUE INDEX one_active_run ON runs(conversation_id)
        WHERE status IN ('running','stopping','needs_reconciliation')""",
    """CREATE TABLE messages (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
        seq INTEGER NOT NULL CHECK(seq >= 1),
        role TEXT NOT NULL CHECK(role IN ('user','assistant')),
        content TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN
            ('queued','streaming','completed','interrupted','failed')),
        run_id TEXT NOT NULL, client_message_id TEXT,
        attachment_version_ids TEXT NOT NULL DEFAULT '[]', selected_version_id TEXT,
        artifact_version_ids TEXT NOT NULL DEFAULT '[]', chunk_index INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(id, conversation_id), UNIQUE(conversation_id, seq),
        UNIQUE(conversation_id, client_message_id),
        FOREIGN KEY(run_id, conversation_id) REFERENCES runs(id, conversation_id)
            DEFERRABLE INITIALLY DEFERRED
    ) STRICT""",
    """CREATE TABLE events (
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        seq INTEGER NOT NULL CHECK(seq >= 1), payload TEXT NOT NULL,
        occurred_at TEXT NOT NULL, PRIMARY KEY(conversation_id, seq)
    ) STRICT""",
    """CREATE TABLE idempotency_records (
        owner_id TEXT NOT NULL REFERENCES users(id), method TEXT NOT NULL,
        path TEXT NOT NULL, key TEXT NOT NULL, request_digest TEXT NOT NULL,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        response_json TEXT NOT NULL, status_code INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(owner_id, method, path, key)
    ) STRICT""",
    "CREATE INDEX conversations_owner_page ON conversations(owner_id, created_at DESC, id DESC)",
    "CREATE INDEX runs_queue ON runs(status, created_at, id)",
)
