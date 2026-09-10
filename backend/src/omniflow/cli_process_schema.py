"""进程占用不同于可过期的执行租约；只有确认退出才能解除。"""

CLI_PROCESS_STATEMENTS = (
    """CREATE TABLE cli_process_holds (
        conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
        manager_token TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('opening','open','uncertain'))
    ) STRICT""",
    # 旧版没有可靠的退出确认记录，不能在升级时假定遗留 CLI 都已退出。
    # 不提供按时间/PID 自动清除的后门；需后续经验证的人工核对流程。
    """INSERT INTO cli_process_holds(conversation_id,manager_token,state)
        SELECT id,coalesce(manager_token,'legacy-unconfirmed'),'uncertain'
        FROM conversations WHERE cli_session_id IS NOT NULL OR manager_token IS NOT NULL""",
)
