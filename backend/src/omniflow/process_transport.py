"""有界 NDJSON 子进程管道。仅操作自身新建的进程组；不是宿主沙箱。"""

import json
import os
import selectors
import signal
import subprocess
import time


def decode_frame(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("重复协议字段")
            result[key] = value
        return result

    result = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(result, dict):
        raise ValueError("协议帧不是对象")
    return result


class ProcessTransport:
    def __init__(self, argv, *, cwd, env, max_frame_bytes=262144, pass_fds=()):
        self.process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
            pass_fds=pass_fds,
        )
        self.max_frame_bytes = max_frame_bytes
        self.buffer = b""
        self.closed = False
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)

    def write(self, value, timeout=2):
        raw = (json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n").encode()
        if len(raw) > self.max_frame_bytes:
            raise ValueError("输入超过单帧上限")
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdin, selectors.EVENT_WRITE)
            while raw:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError
                try:
                    count = os.write(self.process.stdin.fileno(), raw)
                except BlockingIOError:
                    continue
                raw = raw[count:]

    def read(self, timeout):
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return None
                try:
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise EOFError
                self.buffer += chunk
                if len(self.buffer.split(b"\n", 1)[0]) > self.max_frame_bytes:
                    raise ValueError("输出超过单帧上限")
            raw, self.buffer = self.buffer.split(b"\n", 1)
        return decode_frame(raw)

    def interrupt(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGINT)

    def close(self):
        if self.closed:
            return True
        # stdin EOF 只能请求结束，不保证当前轮次成功/取消；调用方分别核对 result。
        self.process.stdin.close()
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.process.poll() is None:
                os.killpg(self.process.pid, sig)
            try:
                self.process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue
        if self.process.poll() is None:
            return False
        # 组内仍有子进程则不能把父进程 wait 当完整退出证明，不释放管理器占用。
        try:
            os.killpg(self.process.pid, 0)
        except ProcessLookupError:
            self.process.stdout.close()
            self.closed = True
            return True
        return False
