"""媒体真实回环 HTTP 冒烟：合成图片/账号/任务范围，自管临时服务，不调用供应商。"""

import io
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

from PIL import Image
from smoke_conversations import Client

from omniflow.artifacts import TaskMediaService
from omniflow.auth_security import expiry
from omniflow.auth_service import AuthService
from omniflow.config import Settings
from omniflow.db import Database

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv/bin/python"


def request(client, path, *, method="GET", data=None, headers=None, status=200):
    hdr = {"Cookie": "; ".join(f"{k}={v}" for k, v in client.cookies.items())}
    if method in ("POST", "DELETE"):
        hdr.update({"Origin": "https://localhost:8443", "X-CSRF-Token": client.csrf or ""})
    hdr.update(headers or {})
    try:
        response = client.opener.open(
            Request(client.base + path, method=method, headers=hdr, data=data),
            timeout=5,
        )
    except HTTPError as error:
        response = error
    with response:
        assert response.status == status
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        return response.headers, response.read()


def main():
    root = ROOT / "backend/var/smoke"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="artifacts-", dir=root) as directory:
        directory = Path(directory)
        settings = Settings(environment="test", data_dir=directory / "data")
        database = Database(settings)
        database.migrate()
        owner = str(
            AuthService(database)
            .create_admin("synthetic_owner", "Synthetic Media Password 123!")
            .id
        )
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        browser, provider = Client(port), Client(port)
        environment = {
            "OMNIFLOW_ENVIRONMENT": "test",
            "OMNIFLOW_DATA_DIR": str(settings.data_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        raw = io.BytesIO()
        with Image.new("RGB", (16, 10), "blue") as image:
            image.save(raw, format="PNG")
        image_bytes = raw.getvalue()
        payload = (
            b'--synthetic\r\nContent-Disposition: form-data; name="file"; '
            b'filename="../../bad.png"\r\n'
            b"Content-Type: image/png\r\n\r\n" + image_bytes + b"\r\n--synthetic--\r\n"
        )
        upload_headers = {
            "Content-Type": "multipart/form-data; boundary=synthetic",
            "Idempotency-Key": "synthetic-upload-once",
        }
        result, grant_url, secret = None, None, None
        for restart in range(2):
            log_path = directory / f"media-{restart}.log"
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
                            assert browser.json("/health/ready")["status"] == "ok"
                            break
                        except URLError:
                            if time.monotonic() >= deadline:
                                raise AssertionError("本次媒体服务未及时启动") from None
                            time.sleep(0.05)
                    if restart == 0:
                        browser.json("/auth/csrf")
                        browser.json(
                            "/auth/login",
                            {
                                "username": "synthetic_owner",
                                "password": "Synthetic Media Password 123!",
                            },
                        )
                        cid = browser.json(
                            "/conversations", {"title": "合成媒体冒烟"}, "synthetic-conversation"
                        )["id"]
                        _, body = request(
                            browser,
                            "/uploads",
                            method="POST",
                            data=payload,
                            headers=upload_headers,
                            status=201,
                        )
                        result = json.loads(body)
                        vid, aid = result["version"]["id"], result["artifact"]["id"]
                        confirmed = browser.json(
                            f"/conversations/{cid}/reference-confirmations",
                            {"version_id": vid, "purpose": "video_first_frame"},
                            "synthetic-confirm",
                        )
                        assert confirmed["version_id"] == vid
                        # 仅测试授权范围生命周期；不是调用真实任务或媒体提供方。
                        task_id = "44444444-4444-4444-8444-444444444444"
                        media = TaskMediaService(database)
                        with database.transaction() as connection:
                            media.reserve(
                                connection,
                                task_id=task_id,
                                owner=owner,
                                cid=cid,
                                input_version_ids=[vid],
                            )
                            media.activate(connection, task_id)
                            gid, secret = media.issue(connection, task_id, vid)
                        grant_url = f"/media-grants/{gid}/content?token={secret}"
                    else:
                        assert browser.json(f"/artifacts/{aid}") == result["artifact"]
                    content_path = result["version"]["content_url"].removeprefix("/api/v1")
                    hdr, body = request(browser, content_path)
                    assert body == image_bytes and int(hdr["Content-Length"]) == len(image_bytes)
                    hdr, body = request(
                        browser, content_path, headers={"Range": "bytes=0-4"}, status=206
                    )
                    assert (
                        body == image_bytes[:5]
                        and hdr["Content-Range"] == f"bytes 0-4/{len(image_bytes)}"
                    )
                    hdr, body = request(
                        browser, content_path, method="HEAD", headers={"Range": "invalid"}
                    )
                    assert body == b"" and int(hdr["Content-Length"]) == len(image_bytes)
                    assert "Content-Range" not in hdr
                    hdr, _ = request(
                        browser, content_path, headers={"Range": "bytes=0-1,3-4"}, status=416
                    )
                    assert hdr["Content-Range"] == f"bytes */{len(image_bytes)}"
                    request(provider, content_path, method="HEAD", status=401)
                    assert request(provider, grant_url)[1] == image_bytes
                    assert request(provider, grant_url, method="HEAD")[1] == b""
                    hdr, body = request(
                        browser,
                        "/uploads",
                        method="POST",
                        data=payload,
                        headers=upload_headers,
                        status=201,
                    )
                    assert json.loads(body) == result and hdr["Idempotency-Replayed"] == "true"
                    request(browser, f"/artifacts/{aid}", method="DELETE", status=409)
                    if restart == 1:
                        with database.transaction() as connection:
                            media.close(connection, task_id)
                        request(provider, grant_url, status=404)
                        request(provider, grant_url, method="HEAD", status=404)
                        _, body = request(browser, f"/artifacts/{aid}", method="DELETE", status=202)
                        assert json.loads(body)["status"] == "access_revoked"
                        request(browser, content_path, status=404)
                        request(browser, content_path, method="HEAD", status=404)
                        request(
                            browser,
                            "/uploads",
                            method="POST",
                            data=payload,
                            headers=upload_headers,
                            status=410,
                        )
                        with database.transaction() as connection:
                            connection.execute(
                                "UPDATE artifacts SET purge_target_at=? WHERE id=?",
                                (expiry(-1), aid),
                            )
                        subprocess.run(
                            [str(PYTHON), "-m", "omniflow.cli", "purge-artifacts"],
                            cwd=ROOT,
                            env=environment,
                            capture_output=True,
                            check=True,
                            timeout=10,
                        )
                        assert list(settings.media_dir.iterdir()) == []
                finally:
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            log_text = log_path.read_text()
            assert "Traceback" not in log_text and "Synthetic Media Password" not in log_text
            assert secret not in log_text
            assert all(cookie not in log_text for cookie in browser.cookies.values())
        print(
            "PASS：127.0.0.1 图片上传／重传、HEAD／Range、短期取图、"
            "重启恢复、占用／墓碑及显式清理；自管进程已退出"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("FAIL：合成媒体冒烟未通过；不输出凭据、完整签名 URL 或原始日志", file=sys.stderr)
        raise SystemExit(1) from None
