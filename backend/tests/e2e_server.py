"""浏览器联调专用：真实 HTTPS FastAPI + 独立假提供方 worker，仅临时目录。

没有测试 HTTP 后门；控制仅经父进程 stdin 和本轮临时文件。不得用于部署。
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from media_double import FakeMediaProvider
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from omniflow.agy_adapter import AgyProvider
from omniflow.app import create_app
from omniflow.config import Settings
from omniflow.db import Database
from omniflow.http_server import LocalServer
from omniflow.process_transport import ProcessTransport
from omniflow.provider_gate import MODELS, AccessEvidence, EvidenceGate
from omniflow.run_manager import RunManager
from omniflow.task_worker import TaskWorker
from omniflow.tasks import TaskService
from omniflow.tool_gateway import ToolGateway

PASSWORD = "Synthetic E2E Password 123!"


def settings(root, origin):
    return Settings(
        environment="test",
        data_dir=root / "data",
        public_origin=origin,
        provider_mode="mock",
        task_poll_seconds=0.05,
        task_retry_seconds=1,
        event_poll_seconds=0.05,
        run_poll_seconds=0.05,
    )


class Media(FakeMediaProvider):
    def __init__(self, root):
        self.root = root
        super().__init__(root / "provider.sqlite3")

    def refresh(self):
        self.mode = (self.root / "media-mode").read_text().strip()

    def submit(self, task, inputs):
        self.refresh()
        return super().submit(task, inputs)

    def poll(self, task_id):
        self.refresh()
        return super().poll(task_id)

    def download(self, key, max_bytes):
        self.refresh()
        return super().download(key, max_bytes)


def launcher(plan):
    return ProcessTransport(
        [sys.executable, "-I", str(Path(__file__).with_name("fake_agy.py")), *plan.argv[1:]],
        cwd=plan.cwd,
        env={**plan.env, "SYNTHETIC_SCENARIO": json.dumps({"e2e": True})},
    )


def evidence(kind):
    return AccessEvidence(
        MODELS[kind],
        time.time() - 1,
        time.time() + 120,
        available=True,
        free=True,
        overages_disabled=True,
        isolation_verified=True,
    )


def worker(role, root, origin):
    # 假提供方 worker 不需要任何网络；即使误接真实适配器也立即失败。
    def deny_connect(*_args, **_kwargs):
        raise RuntimeError("E2E worker 禁止网络连接")

    socket.socket.connect = deny_connect
    socket.socket.connect_ex = deny_connect
    db = Database(settings(root, origin))
    media = Media(root)
    if role == "runs":
        instance = RunManager(
            db,
            AgyProvider(root / "cli", gate=EvidenceGate(evidence), launcher=launcher),
            tool_gateway=ToolGateway(db, media),
        )
    else:
        instance = TaskWorker(db, media)
    signal.signal(signal.SIGTERM, lambda *_: instance.shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: instance.shutdown.set())
    try:
        while not instance.shutdown.is_set():
            if not instance.execute_next():
                instance.shutdown.wait(0.05)
    finally:
        if role == "runs":
            instance.close()


def main(root):
    # 绑定实际 socket 的零端口，避免先探端口再绑定的竞争。
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"https://127.0.0.1:{sock.getsockname()[1]}"
    config = settings(root, origin)
    db = Database(config)
    db.migrate()
    (root / "media-mode").write_text("ok")
    app = create_app(config)
    app.state.auth.create_admin("e2e_owner", PASSWORD)
    app.state.tasks = TaskService(db, Media(root))
    dist = Path(__file__).resolve().parents[2] / "frontend/dist"

    @app.get("/register", include_in_schema=False)
    @app.get("/reset-password", include_in_schema=False)
    def account_page():
        return FileResponse(dist / "index.html")

    app.mount("/", StaticFiles(directory=dist, html=True))
    server = LocalServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            access_log=False,
            proxy_headers=False,
            server_header=False,
            log_level="error",
            timeout_graceful_shutdown=3,
            ssl_keyfile=str(root / "key.pem"),
            ssl_certfile=str(root / "cert.pem"),
        )
    )
    processes = {}
    lock = threading.Lock()

    def start(role):
        processes[role] = subprocess.Popen(
            [sys.executable, __file__, str(root), "--worker", role, "--origin", origin],
            env={"PATH": os.environ.get("PATH", ""), "PYTHONUNBUFFERED": "1"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )

    def stop(role):
        child = processes.pop(role, None)
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
                raise RuntimeError("本轮 worker 未能正常退出") from None

    def control():
        try:
            for line in sys.stdin:
                command = json.loads(line)
                with lock:
                    if command["action"] == "stop-media":
                        stop("media")
                    elif command["action"] == "start-media":
                        assert "media" not in processes
                        start("media")
                    else:
                        break
                print(json.dumps({"ack": command["id"]}), flush=True)
        finally:
            app.state.stream_shutdown.set()
            server.should_exit = True

    try:
        start("runs")
        start("media")
        threading.Thread(target=control, daemon=True).start()
        print(json.dumps({"origin": origin}), flush=True)
        server.run(sockets=[sock])
    finally:
        with lock:
            for role in list(processes):
                stop(role)
        sock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--worker", choices=["runs", "media"])
    parser.add_argument("--origin")
    args = parser.parse_args()
    expected_parent = Path(__file__).resolve().parents[1] / "var"
    if args.root.parent.resolve() != expected_parent or not args.root.name.startswith("e2e-"):
        parser.error("只允许当前 worktree 的 backend/var/e2e-* 临时目录")
    if args.root.is_symlink() or not args.root.is_dir():
        parser.error("临时目录必须为已有普通目录")
    if not args.worker and (args.root / "data").exists():
        parser.error("拒绝复用已有数据库目录")
    if args.worker:
        worker(args.worker, args.root, args.origin)
    else:
        main(args.root)
