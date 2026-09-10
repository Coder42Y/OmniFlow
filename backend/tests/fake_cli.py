"""测试专用假 CLI；不导入产品适配器、不访问网络或宿主凭据。"""

import json
import os
import sys
from pathlib import Path
from uuid import uuid4


def output(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    first = json.loads(sys.stdin.readline())
    path = Path("synthetic-session.json")
    if first["resume_id"] is None:
        assert not path.exists(), "不能给新对话继承旧目录"
        state = {"id": str(uuid4()), "turns": []}
    else:
        state = json.loads(path.read_text())
        assert state["id"] == first["resume_id"], "必须明确恢复本目录的 ID"
    path.write_text(json.dumps(state))
    output({"session_id": state["id"], "pid": os.getpid()})
    for line in sys.stdin:
        turn = json.loads(line)
        state["turns"].append(turn)
        path.write_text(json.dumps(state, ensure_ascii=True))
        content = turn["history"][-1]["content"]
        output({"type": "delta", "text": "离线假CLI："})
        if content == "中途崩溃":
            return
        if content == "等待释放":
            assert json.loads(sys.stdin.readline()) == {"release": True}
        if content == "不可信日志":
            output({"type": "raw_log", "path": "/synthetic/private", "secret": "SYNTHETIC-SECRET"})
            continue
        output({"type": "delta", "text": content + "🌊"})
        output({"type": "result"})


if __name__ == "__main__":
    main()
