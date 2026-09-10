"""独立进程故障注入入口，仅由离线测试用临时路径调用，无 HTTP 监听。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from media_double import FakeMediaProvider

from omniflow.config import Settings
from omniflow.db import Database
from omniflow.task_worker import TaskWorker


def main():
    database = Database(Settings(environment="test", data_dir=Path(sys.argv[1])))
    provider = FakeMediaProvider(sys.argv[2])
    mode = sys.argv[3]
    worker = TaskWorker(database, provider)
    if mode == "hard_crash_after_publish":
        original = worker.storage.publish

        def publish(stage, vid):
            original(stage, vid)
            os._exit(78)

        worker.storage.publish = publish
    else:
        provider.mode = mode
    worker.execute_next()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
