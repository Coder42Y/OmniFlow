"""独立进程验证持久限流；只用临时库及拒绝网络的合成传输。"""

import sys
import time
from pathlib import Path

from omniflow.agnes_adapter import AgnesProvider
from omniflow.config import Settings
from omniflow.db import Database
from omniflow.provider_gate import MODELS, AccessEvidence, EvidenceGate
from omniflow.task_worker import TaskWorker


class Transport:
    def request(self, *args, **kwargs):
        # 若保护失效，留下跨进程证据；不进行网络调用。
        Path(sys.argv[2]).write_text("unexpected synthetic submission")
        raise AssertionError("限流期间不允许提交")


def evidence(kind):
    current = time.time()
    return AccessEvidence(
        MODELS[kind],
        current,
        current + 120,
        available=True,
        free=True,
        overages_disabled=True,
        isolation_verified=True,
    )


def main():
    database = Database(Settings(environment="test", data_dir=Path(sys.argv[1])))
    adapter = AgnesProvider(
        database,
        http=Transport(),
        authorization=lambda: "synthetic-token-only",
        gate=EvidenceGate(evidence),
    )
    assert TaskWorker(database, adapter).execute_next()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
