"""仅供离线回归：合成图 FFmpeg 发布后硬退出；重启严禁再生成。"""

import os
import subprocess
import sys
import time
from pathlib import Path

from omniflow.config import Settings
from omniflow.db import Database
from omniflow.ffmpeg_adapter import FFmpegProvider
from omniflow.provider_gate import MODELS, AccessEvidence, EvidenceGate
from omniflow.task_worker import TaskWorker


def main():
    database = Database(Settings(environment="test", data_dir=Path(sys.argv[1])))
    marker, mode = Path(sys.argv[2]), sys.argv[3]

    def evidence(kind):
        current = time.time()
        return AccessEvidence(
            MODELS[kind],
            current - 1,
            current + 120,
            available=True,
            free=True,
            overages_disabled=True,
            isolation_verified=True,
        )

    def runner(*args, **kwargs):
        with marker.open("a") as stream:
            stream.write("render\n")
            stream.flush()
            os.fsync(stream.fileno())
        if mode != "crash":
            raise AssertionError("重启不得执行新运镜")
        return subprocess.run(*args, **kwargs)

    local = FFmpegProvider(database, gate=EvidenceGate(evidence), runner=runner)
    if mode == "crash":
        original = local.storage.publish

        def publish(stage, vid):
            original(stage, vid)
            os._exit(78)

        local.storage.publish = publish
    TaskWorker(database, local).execute_next()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
