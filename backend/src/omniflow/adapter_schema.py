"""迁移 7：同步提供方的持久结果线索；不保存原始回复或授权头。"""

ADAPTER_STATEMENTS = (
    """CREATE TABLE adapter_receipts (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id),
        result_key TEXT NOT NULL
    ) STRICT""",
)
