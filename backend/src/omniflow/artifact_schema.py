"""追加迁移 5：版本元数据不可变，清理与占用信息独立保存。"""

ARTIFACT_STATEMENTS = (
    """CREATE TABLE artifacts (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL REFERENCES users(id),
        kind TEXT NOT NULL CHECK(kind IN ('image','video','text')),
        title TEXT NOT NULL,
        current_version_id TEXT NOT NULL,
        version_count INTEGER NOT NULL CHECK(version_count>=1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT,
        purge_target_at TEXT,
        purged_at TEXT,
        UNIQUE(id,owner_id),
        FOREIGN KEY(id,current_version_id) REFERENCES artifact_versions(artifact_id,id)
            DEFERRABLE INITIALLY DEFERRED
    ) STRICT""",
    """CREATE TABLE artifact_versions (
        id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL REFERENCES artifacts(id),
        version_number INTEGER NOT NULL CHECK(version_number>=1),
        parent_version_id TEXT,
        source_task_id TEXT UNIQUE REFERENCES task_media_scopes(task_id),
        execution_engine TEXT CHECK(execution_engine IN
            ('agnes-image-2.5-flash','agnes-video-2.5-flash','local-ffmpeg')),
        media_type TEXT NOT NULL CHECK(media_type IN
            ('image/png','image/jpeg','image/webp','video/mp4','text/plain')),
        byte_size INTEGER NOT NULL CHECK(byte_size>=0),
        sha256 TEXT NOT NULL,
        width INTEGER CHECK(width>0), height INTEGER CHECK(height>0),
        duration_seconds REAL CHECK(duration_seconds>=0), fps REAL CHECK(fps>0),
        created_at TEXT NOT NULL,
        UNIQUE(artifact_id,id), UNIQUE(artifact_id,version_number),
        FOREIGN KEY(artifact_id,parent_version_id) REFERENCES artifact_versions(artifact_id,id)
    ) STRICT""",
    """CREATE TRIGGER immutable_artifact_versions BEFORE UPDATE ON artifact_versions
        BEGIN SELECT RAISE(ABORT,'immutable_version'); END""",
    """CREATE TRIGGER retain_artifact_version_tombstones BEFORE DELETE ON artifact_versions
        BEGIN SELECT RAISE(ABORT,'retain_version_tombstone'); END""",
    "CREATE INDEX artifacts_owner_page ON artifacts(owner_id,created_at DESC,id DESC)",
    # 明确授权的精确版本，不把同作品将来的新版本自动授权给旧对话。
    """CREATE TABLE conversation_artifact_versions (
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        version_id TEXT NOT NULL REFERENCES artifact_versions(id),
        PRIMARY KEY(conversation_id,version_id)
    ) STRICT""",
    """CREATE TABLE reference_confirmations (
        id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL REFERENCES users(id),
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        version_id TEXT NOT NULL REFERENCES artifact_versions(id),
        purpose TEXT NOT NULL CHECK(purpose='video_first_frame'),
        created_at TEXT NOT NULL
    ) STRICT""",
    # 不改阶段 02 的幂等表历史；本账本允许无对话的作品，墓碑不依赖聊天存在。
    """CREATE TABLE artifact_idempotency (
        owner_id TEXT NOT NULL REFERENCES users(id),
        method TEXT NOT NULL CHECK(method='POST'), path TEXT NOT NULL, key TEXT NOT NULL,
        request_digest TEXT NOT NULL,
        artifact_id TEXT NOT NULL REFERENCES artifacts(id),
        conversation_id TEXT REFERENCES conversations(id),
        response_json TEXT NOT NULL, status INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(owner_id,method,path,key)
    ) STRICT""",
    # 仅是任务的媒体访问/占用范围，不是任务队列。阶段 04 在任务事务内登记/结束，
    # 在 worker 即将提交时激活；不存在网站/MCP 任意签发或释放此范围的入口。
    """CREATE TABLE task_media_scopes (
        task_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL REFERENCES users(id),
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        owner_auth_epoch INTEGER NOT NULL,
        activated_at TEXT, grant_deadline TEXT, closed_at TEXT,
        created_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE task_artifact_uses (
        task_id TEXT NOT NULL REFERENCES task_media_scopes(task_id),
        artifact_id TEXT NOT NULL REFERENCES artifacts(id),
        version_id TEXT NOT NULL REFERENCES artifact_versions(id),
        role TEXT NOT NULL CHECK(role IN ('input','target')),
        PRIMARY KEY(task_id,version_id,role),
        FOREIGN KEY(artifact_id,version_id) REFERENCES artifact_versions(artifact_id,id)
    ) STRICT""",
    """CREATE TABLE media_grants (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES task_media_scopes(task_id),
        version_id TEXT NOT NULL REFERENCES artifact_versions(id),
        token_hash TEXT NOT NULL UNIQUE,
        purpose TEXT NOT NULL CHECK(purpose='provider_reference'),
        expires_at TEXT NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL
    ) STRICT""",
)
