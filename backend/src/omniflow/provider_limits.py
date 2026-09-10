"""持久供应商暂停；单一 Agnes 授权未知限额范围时保守覆盖图片和视频。

冷却结束不等于重新获准：还必须有晚于限流事实的有效免费/限额证据。
不存原始响应、请求或账号凭据；普通开关和适配器重建都不能清除事实。
"""

import math
import re
import time
from email.utils import parsedate_to_datetime

from .auth_security import fail


def retry_deadline(value, current):
    # 缺失/坏头保守冷却五分钟，之后仍须新权益证据；不能用旧证据自动放行。
    if not isinstance(value, str) or len(value) > 128:
        return current + 300
    value = value.strip()
    if re.fullmatch(r"[0-9]+", value):
        # 不截短合法的超长等待时间；SQLite REAL 支持此有界长度整数的浮点表示。
        return current + max(1, int(value))
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is not None:
            deadline = date.timestamp()
            if math.isfinite(deadline) and deadline > current:
                return deadline
    except (ValueError, TypeError, OverflowError):
        pass
    return current + 300


class ProviderLimits:
    def __init__(self, database, *, clock=time.time):
        self.database, self.clock = database, clock

    def record(self, retry_after):
        current = self.clock()
        deadline = retry_deadline(retry_after, current)
        with self.database.transaction() as connection:
            # 并发或晚到的较短 Retry-After 不能缩短已经保存的等待时间。
            connection.execute(
                "INSERT INTO provider_limits(provider,observed_at,retry_at) VALUES ('agnes',?,?) "
                "ON CONFLICT(provider) DO UPDATE SET "
                "observed_at=MAX(observed_at,excluded.observed_at),"
                "retry_at=MAX(retry_at,excluded.retry_at)",
                (current, deadline),
            )

    def check(self, proof=None):
        with self.database.snapshot() as connection:
            row = connection.execute(
                "SELECT observed_at,retry_at FROM provider_limits WHERE provider='agnes'"
            ).fetchone()
        if row and (
            self.clock() < row["retry_at"]
            or proof is None
            or proof.checked_at <= row["observed_at"]
        ):
            fail(503, "PROVIDER_LIMIT_REACHED", "提供方限流，等待冷却及重新核验")
