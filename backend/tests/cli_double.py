"""假 CLI 传输只在测试中注入；进程以合成私有目录与最小环境启动。"""

import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

from omniflow.run_manager import TextDelta, TurnResult


class FakeSession:
    def __init__(self, directory, resume_id):
        self.directory = directory
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).with_name("fake_cli.py"))],
            cwd=directory,
            env={"PYTHONIOENCODING": "utf-8"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.send_json({"resume_id": resume_id})
        try:
            greeting = self.frame(3)
            self.session_id = greeting["session_id"]
            assert greeting["pid"] == self.process.pid
        except BaseException:
            self.close()
            raise

    def send_json(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode())

    def send(self, history, permission):
        self.send_json({"history": history, "generation_permission": permission})

    def frame(self, timeout):
        if b"\n" not in self.buffer:
            if not self.selector.select(timeout):
                return None
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise EOFError("合成进程断线")
            self.buffer += chunk
        if b"\n" not in self.buffer:
            return None
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def receive(self, timeout):
        frame = self.frame(timeout)
        if frame is None:
            return None
        if frame["type"] == "delta":
            return TextDelta(frame["text"])
        if frame["type"] == "result":
            return TurnResult()
        # 原始日志绝不能直接透传；测试将未知输出交给 manager 拒绝。
        return frame

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=2)
        return True

    def close(self):
        self.stop()
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()
        return True  # stop 已 wait 确认仅本会话子进程退出，才可释放持久占用。


class FakeProvider:
    def __init__(self, root):
        self.root = root
        self.opened = []

    def open(self, *, owner_id, conversation_id, resume_id, model):
        assert model == "gemini-3.8-flash-low"
        session = FakeSession(self.root / owner_id / conversation_id, resume_id)
        self.opened.append(
            {
                "owner_id": owner_id,
                "conversation_id": conversation_id,
                "resume_id": resume_id,
                "model": model,
                "session": session,
            }
        )
        return session
