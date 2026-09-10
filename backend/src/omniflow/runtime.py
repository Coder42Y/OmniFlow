"""API、文字管理器与独立媒体 worker 共用的真实装配入口，无 tests 依赖。"""

import json
import os
import re
import threading
import time
from contextlib import AbstractContextManager
from uuid import uuid4

from .agnes_adapter import AgnesProvider
from .agy_adapter import AgyProvider, private_directory
from .auth_security import fail
from .ffmpeg_adapter import FFmpegProvider, MediaRouter
from .media_provider import DisabledMediaProvider
from .provider_gate import EvidenceGate
from .run_manager import UnavailableProvider
from .runtime_config import SignedEvidence, load_config, private_read
from .runtime_launcher import AgyLauncher
from .safe_http import PinnedHTTPSExchange, SafeHTTP


class Presence:
    ttl = 10

    def __init__(self, settings, fingerprint):
        self.directory = settings.data_dir / "runtime-presence"
        self.fingerprint = fingerprint

    def check(self, role):
        try:
            value = json.loads(private_read(self.directory / (role + ".json")))
            current = time.time()
            if (
                value["fingerprint"] != self.fingerprint
                or not 0 <= current - value["updated_at"] < self.ttl
                or not value["updated_at"] < value["expires_at"] <= value["updated_at"] + self.ttl
                or current >= value["expires_at"]
            ):
                raise ValueError
            # 本机跨进程检查。PID 复用仍受随机 token、极短时效与配置摘要约束。
            os.kill(value["pid"], 0)
        except Exception:
            fail(503, "PROVIDER_UNAVAILABLE", "独立执行器未就绪或心跳已过期")

    def worker(self, role):
        if role not in ("text", "media"):
            raise ValueError
        return WorkerPresence(self, role)


class WorkerPresence(AbstractContextManager):
    def __init__(self, presence, role):
        self.presence, self.role = presence, role
        self.token = str(uuid4())
        self.stop = threading.Event()
        self.path = presence.directory / (role + ".json")

    def publish(self):
        current = time.time()
        data = {
            "fingerprint": self.presence.fingerprint,
            "token": self.token,
            "pid": os.getpid(),
            "updated_at": current,
            "expires_at": current + Presence.ttl,
        }
        temporary = self.path.with_name(self.token + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(data).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def pulse(self):
        while not self.stop.wait(2):
            try:
                self.publish()
            except Exception:
                return  # 不再发布，已有心跳自然到期，不假称继续就绪。

    def __enter__(self):
        private_directory(self.presence.directory)
        self.publish()
        self.thread = threading.Thread(target=self.pulse, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(3)
        try:
            if json.loads(private_read(self.path))["token"] == self.token:
                self.path.unlink()
        except (OSError, ValueError):
            pass


class ReadyGate:
    def __init__(self, evidence, presence, enabled_kinds):
        self.evidence, self.presence = evidence, presence
        self.enabled_kinds = enabled_kinds

    def check(self, kind):
        if kind not in self.enabled_kinds:
            fail(503, "PROVIDER_UNAVAILABLE", "此能力未装配，不能通过管理员开关启用")
        proof = self.evidence.check(kind)
        self.presence.check("text" if kind == "text" else "media")
        return proof


class RuntimeMedia(MediaRouter):
    def __init__(self, agnes, local, gate):
        super().__init__(agnes, local)
        self.gate = gate

    def check(self, kind):
        proof = self.gate.check(kind)
        if kind == "text":
            return proof
        return super().check(kind)


class Runtime:
    def __init__(self, database):
        self.config, self.fingerprint, self.credentials = load_config(
            database.settings, with_credentials=True
        )
        self.presence = Presence(database.settings, self.fingerprint)
        self.gate = ReadyGate(
            EvidenceGate(SignedEvidence(self.config, self.fingerprint, self.credentials)),
            self.presence,
            self.config.enabled_kinds,
        )
        self.launcher = (
            AgyLauncher(self.config, self.credentials)
            if "text" in self.config.enabled_kinds
            else None
        )
        self.text = (
            AgyProvider(database.settings.data_dir / "cli", gate=self.gate, launcher=self.launcher)
            if self.launcher
            else UnavailableProvider()
        )
        self.media = RuntimeMedia(
            AgnesProvider(
                database,
                http=SafeHTTP(exchange=PinnedHTTPSExchange()),
                authorization=self.authorization,
                gate=self.gate,
                download_hosts=self.config.download_hosts,
                reference_editing_enabled=self.config.reference_editing_enabled,
            )
            if {"image", "ai_video"}.intersection(self.config.enabled_kinds)
            else DisabledMediaProvider(),
            FFmpegProvider(database, gate=self.gate)
            if "local_motion" in self.config.enabled_kinds
            else DisabledMediaProvider(),
            self.gate,
        )

    def authorization(self):
        token = self.credentials.checked("agnes")[0].decode("ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,512}", token):
            raise ValueError("提供方授权材料格式无效")
        return token

    def check_materials(self, role):
        # 只检查该角色已装配的能力；视频不依赖 Google、agy 或本地运镜执行文件。
        if role == "text" and self.launcher is not None:
            self.launcher.check()
        elif role == "media" and set(self.config.enabled_kinds) - {"text"}:
            if {"image", "ai_video"}.intersection(self.config.enabled_kinds):
                self.authorization()
            if "local_motion" in self.config.enabled_kinds and self.media.local.executable is None:
                raise ValueError("本地运镜不可用")
        else:
            raise ValueError("此执行器未装配")

    def worker(self, role):
        # 证据过期不妨碍已受理媒体续查/下载，但不能偷偷换账号查询。
        self.check_materials(role)
        return self.presence.worker(role)


def get_runtime(database):
    if database.settings.provider_mode != "real":
        raise ValueError("未选择真实运行")
    if not hasattr(database, "_runtime"):
        database._runtime = Runtime(database)
    return database._runtime
