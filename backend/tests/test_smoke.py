import subprocess
import sys
from pathlib import Path


def test_real_loopback_server_restart_and_cleanup():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "backend/tools/smoke_health.py"],
        cwd=root,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS：" in result.stdout
    assert "子进程已退出" in result.stdout


def test_real_loopback_artifact_delivery_restart_and_purge():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "backend/tools/smoke_artifacts.py"],
        cwd=root,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS：" in result.stdout
    assert "自管进程已退出" in result.stdout
