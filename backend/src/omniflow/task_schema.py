"""追加迁移 6：任务事实、提交意图及每次领取的 fencing token。"""

TASK_STATEMENTS = (
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY REFERENCES task_media_scopes(task_id),
        owner_id TEXT NOT NULL REFERENCES users(id),
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        run_id TEXT REFERENCES runs(id),
        kind TEXT NOT NULL CHECK(kind IN ('image','ai_video','local_motion')),
        status TEXT NOT NULL CHECK(status IN ('queued','submitting','running','saving',
            'completed','failed','canceled','submission_unknown','needs_reconciliation')),
        requested_parameters TEXT NOT NULL,
        execution_engine TEXT NOT NULL CHECK(execution_engine IN
            ('agnes-image-2.5-flash','agnes-video-2.5-flash','local-ffmpeg')),
        owner_auth_epoch INTEGER NOT NULL,
        target_artifact_id TEXT REFERENCES artifacts(id),
        base_version_id TEXT REFERENCES artifact_versions(id),
        output_artifact_id TEXT NOT NULL,
        output_version_id TEXT NOT NULL UNIQUE,
        output_version_ids TEXT NOT NULL DEFAULT '[]',
        provider_task_id TEXT,
        result_key TEXT,
        intent_at TEXT,
        dispatched_at TEXT,
        lease_token TEXT,
        lease_until TEXT,
        next_attempt_at TEXT NOT NULL,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK((target_artifact_id IS NULL)=(base_version_id IS NULL)),
        CHECK((lease_token IS NULL)=(lease_until IS NULL)),
        CHECK(status!='completed' OR output_version_ids!='[]')
    ) STRICT""",
    "CREATE INDEX tasks_queue ON tasks(status,next_attempt_at,created_at,id)",
    "CREATE INDEX tasks_owner_page ON tasks(owner_id,created_at DESC,id DESC)",
    "CREATE UNIQUE INDEX tasks_provider_identity ON tasks(execution_engine,provider_task_id) "
    "WHERE provider_task_id IS NOT NULL",
    """CREATE TABLE task_idempotency (
        owner_id TEXT NOT NULL REFERENCES users(id),
        method TEXT NOT NULL CHECK(method='POST'), path TEXT NOT NULL, key TEXT NOT NULL,
        request_digest TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id),
        response_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(owner_id,method,path,key)
    ) STRICT""",
)
