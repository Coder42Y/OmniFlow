"""仅回环真实 HTTP 冒烟：合成临时存储、自管子进程、无提供方请求。"""

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "bin" / "python"


def main() -> None:
    temporary_root = ROOT / "backend" / "var" / "smoke"
    temporary_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="health-", dir=temporary_root) as temporary:
        directory = Path(temporary)
        # 不继承宿主授权、代理或其他应用配置。
        environment = {
            "OMNIFLOW_ENVIRONMENT": "test",
            "OMNIFLOW_DATA_DIR": str(directory / "data"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        for command in ("migrate", "migrate", "check"):
            subprocess.run(
                [str(PYTHON), "-m", "omniflow.cli", command],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                check=True,
                timeout=15,
            )
        opener = build_opener(ProxyHandler({}))
        for run in range(2):
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            log_path = directory / f"server-{run}.log"
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
                        if process.poll() is not None:
                            raise AssertionError("本轮自管服务提前退出")
                        try:
                            with opener.open(
                                f"http://127.0.0.1:{port}/api/v1/health/ready", timeout=1
                            ) as response:
                                assert response.status == 200
                                assert json.load(response) == {"status": "ok"}
                                assert response.headers["Cache-Control"] == "private, no-store"
                                assert response.headers["X-Request-ID"]
                                assert response.headers.get("Server") is None
                            break
                        except URLError:
                            if time.monotonic() >= deadline:
                                raise AssertionError("本轮自管服务未及时就绪") from None
                            time.sleep(0.05)
                    with opener.open(
                        f"http://127.0.0.1:{port}/api/v1/health/live", timeout=2
                    ) as response:
                        assert response.status == 200
                    request = Request(
                        f"http://127.0.0.1:{port}/synthetic-missing?token=synthetic-log-secret",
                        headers={"X-Request-ID": "untrusted"},
                    )
                    try:
                        opener.open(request, timeout=2)
                    except HTTPError as error:
                        with error:
                            body = json.load(error)
                            assert error.code == 404
                            assert error.headers["Content-Type"] == "application/problem+json"
                            assert body["request_id"] == error.headers["X-Request-ID"]
                            assert body["request_id"] != "untrusted"
                    else:
                        raise AssertionError("未实现路径不应成功")
                finally:
                    # 仅结束本函数 Popen 创建且仍在运行的子进程。
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            assert "synthetic-log-secret" not in log_path.read_text()
        print("PASS：显式迁移、只读检查、127.0.0.1 HTTP、重启恢复与访问日志脱敏；子进程已退出")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("FAIL：本地 HTTP 冒烟未通过；不输出子进程原始日志", file=sys.stderr)
        raise SystemExit(1) from None
