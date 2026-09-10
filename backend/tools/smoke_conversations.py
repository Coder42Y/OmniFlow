"""真实回环 HTTP/SSE 冒烟；仅合成账号与临时库，不调用任何提供方。"""

import json
import socket
import subprocess
import sys
import tempfile
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from omniflow.auth_service import AuthService
from omniflow.config import Settings
from omniflow.db import Database

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv/bin/python"


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}/api/v1"
        self.opener = build_opener(ProxyHandler({}))
        self.cookies = {}
        self.csrf = None

    def request(self, path, data=None, key=None):
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        if data is not None:
            headers.update(
                {
                    "Origin": "https://localhost:8443",
                    "Content-Type": "application/json",
                    "X-CSRF-Token": self.csrf or "",
                }
            )
        if key:
            headers["Idempotency-Key"] = key
        request = Request(
            self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
        )
        response = self.opener.open(request, timeout=5)
        for header in response.headers.get_all("Set-Cookie", []):
            cookies = SimpleCookie()
            cookies.load(header)
            for name, morsel in cookies.items():
                if morsel["max-age"] == "0":
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        return response

    def json(self, path, data=None, key=None):
        with self.request(path, data, key) as response:
            result = json.load(response)
            if "csrf_token" in result:
                self.csrf = result["csrf_token"]
            return result


def event(response):
    frame = []
    while True:
        line = response.readline().decode().rstrip("\r\n")
        if line:
            frame.append(line)
        elif frame:
            if frame[0].startswith(":"):
                frame.clear()
                continue
            return frame
        else:
            raise AssertionError("事件流提前结束")


def main():
    root = ROOT / "backend/var/smoke"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="conversations-", dir=root) as directory:
        directory = Path(directory)
        settings = Settings(environment="test", data_dir=directory / "data")
        database = Database(settings)
        database.migrate()
        AuthService(database).create_admin("synthetic_owner", "Synthetic Only Password 123!")
        environment = {
            "OMNIFLOW_ENVIRONMENT": "test",
            "OMNIFLOW_DATA_DIR": str(settings.data_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        client = Client(port)
        cid = None
        for restart in range(2):
            log_path = directory / f"http-{restart}.log"
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    [str(PYTHON), "-m", "omniflow.cli", "serve", "--port", str(port)],
                    cwd=ROOT,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        assert process.poll() is None
                        try:
                            assert client.json("/health/ready")["status"] == "ok"
                            break
                        except URLError:
                            if time.monotonic() >= deadline:
                                raise AssertionError("合成服务未就绪") from None
                            time.sleep(0.05)
                    if restart == 0:
                        client.json("/auth/csrf")
                        client.json(
                            "/auth/login",
                            {
                                "username": "synthetic_owner",
                                "password": "Synthetic Only Password 123!",
                            },
                        )
                        cid = client.json(
                            "/conversations", {"title": "合成回环冒烟"}, "create-action"
                        )["id"]
                        before = client.json(f"/conversations/{cid}/snapshot")
                        accepted = client.json(
                            f"/conversations/{cid}/messages",
                            {
                                "client_message_id": "33333333-3333-4333-8333-333333333333",
                                "content": "合成内容，不调用真实提供方",
                            },
                            "message-action",
                        )
                        assert accepted["run"]["status"] == "queued"
                        with client.request(
                            f"/conversations/{cid}/events?after_event_id={before['last_event_id']}"
                        ) as stream:
                            assert stream.headers["X-Accel-Buffering"] == "no"
                            assert "event: message.created" in event(stream)
                            assert "event: run.updated" in event(stream)
                            # 自己创建的服务器收到停止信号后，先发无持久 ID 的 control 再退出。
                            process.terminate()
                            stopped = event(stream)
                            assert stopped[0] == "event: control"
                            assert json.loads(stopped[1][6:])["code"] == "SERVICE_RESTARTING"
                        process.wait(timeout=5)
                    else:
                        restored = client.json(f"/conversations/{cid}/snapshot")
                        assert len(restored["messages"]) == 1
                        assert restored["runs"][0]["status"] == "queued"
                        subprocess.run(
                            [str(PYTHON), "-m", "omniflow.cli", "run-manager", "--once"],
                            cwd=ROOT,
                            env=environment,
                            capture_output=True,
                            check=True,
                            timeout=10,
                        )
                        result = client.json(f"/runs/{accepted['run']['id']}")
                        assert result["status"] == "failed"
                        assert result["error"]["code"] == "PROVIDER_UNAVAILABLE"
                        with client.request(
                            f"/conversations/{cid}/events?after_event_id={restored['last_event_id']}"
                        ) as stream:
                            assert "event: message.created" in event(stream)
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            text = log_path.read_text()
            assert "Synthetic Only Password" not in text
            assert "Traceback" not in text
            for raw in client.cookies.values():
                assert raw not in text
        print(
            "PASS：127.0.0.1 HTTP/SSE、信号关闭 control、重启快照、"
            "独立轮次消费与拒绝真实提供方；自管进程已退出"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("FAIL：合成对话冒烟未通过；不输出原始凭据或服务器日志", file=sys.stderr)
        raise SystemExit(1) from None
