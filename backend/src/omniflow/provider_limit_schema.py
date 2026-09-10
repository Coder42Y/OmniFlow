"""追加迁移 8：供应商限流事实独立于任务、开关及短期权益证据。"""

PROVIDER_LIMIT_STATEMENTS = (
    "CREATE TABLE provider_limits ("
    "provider TEXT PRIMARY KEY CHECK(provider='agnes'),"
    "observed_at REAL NOT NULL CHECK(observed_at>=0),"
    "retry_at REAL NOT NULL CHECK(retry_at>=observed_at)"
    ") STRICT",
)
