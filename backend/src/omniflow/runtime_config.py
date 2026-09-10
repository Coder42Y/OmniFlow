"""显式私有运行配置及签名核验证据；绝不扫描 HOME/.env，不签发或刷新证据。"""

import base64
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .process_transport import decode_frame
from .provider_gate import AccessEvidence


class RuntimeConfigurationError(ValueError):
    def __init__(self):
        super().__init__("真实运行配置或核验证据无效")


def private_read(path, maximum=65536):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeConfigurationError
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise RuntimeConfigurationError
        value = stream.read(maximum + 1)
    if len(value) > maximum:
        raise RuntimeConfigurationError
    return value


class AuthMount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    source: Path
    target: str

    @field_validator("source")
    @classmethod
    def absolute_source(cls, value):
        if not value.is_absolute():
            raise ValueError("授权源须为显式绝对路径")
        return value

    @field_validator("target")
    @classmethod
    def exact_file(cls, value):
        # 官方安装版本的最小授权文件清单须现场核对；不挂目录、配置、插件或历史。
        parts = Path(value).parts
        if (
            not value.startswith(".gemini/")
            or any(p in ("..", ".") for p in value.split("/"))
            or len(parts) < 2
            or value.endswith("/")
            or any(p in ("settings.json", "mcp_config.json") for p in parts)
        ):
            raise ValueError("授权目标必须为已核验的单个文件")
        return value


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    version: Literal[1]
    enabled_kinds: tuple[Literal["text", "image", "ai_video", "local_motion"], ...] = Field(
        default=("text", "image", "ai_video", "local_motion"), min_length=1, max_length=4
    )
    rootfs: Path | None = None
    rootfs_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    auth_files: tuple[AuthMount, ...] = Field(default=(), max_length=8)
    agnes_key_file: Path | None = None
    evidence_file: Path
    evidence_public_key: Path
    download_hosts: tuple[str, ...] = Field(default=(), max_length=16)
    google_hosts: tuple[str, ...] = Field(default=(), max_length=16)
    reference_editing_enabled: bool = False

    @model_validator(mode="after")
    def required_materials(self):
        if len(set(self.enabled_kinds)) != len(self.enabled_kinds):
            raise ValueError("启用能力不能重复")
        if "text" in self.enabled_kinds and not (
            self.rootfs and self.rootfs_sha256 and self.auth_files and self.google_hosts
        ):
            raise ValueError("文字能力缺少运行材料")
        if {"image", "ai_video"}.intersection(self.enabled_kinds) and not (
            self.agnes_key_file and self.download_hosts
        ):
            raise ValueError("Agnes能力缺少运行材料")
        if self.reference_editing_enabled and "image" not in self.enabled_kinds:
            raise ValueError("未装配生图不能启用参考编辑")
        return self

    @field_validator("download_hosts", "google_hosts")
    @classmethod
    def hosts(cls, value):
        import re

        if any(not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}", h) for h in value):
            raise ValueError("目的地必须为精确域名")
        return value

    @field_validator("rootfs", "agnes_key_file", "evidence_file", "evidence_public_key")
    @classmethod
    def absolute(cls, value):
        if value is not None and not value.is_absolute():
            raise ValueError("必须显式指定绝对路径")
        return value


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


class CredentialSnapshot:
    """只读取显式启用提供方的最小材料；摘要绑定证据，执行使用同一份不可变字节。"""

    def __init__(self, config):
        self.paths = {}
        if "text" in config.enabled_kinds:
            self.paths["text"] = tuple(m.source for m in config.auth_files)
        if {"image", "ai_video"}.intersection(config.enabled_kinds):
            self.paths["agnes"] = (config.agnes_key_file,)
        self.values = {provider: self.read(provider) for provider in self.paths}

    def read(self, provider):
        maximum = 512 if provider == "agnes" else 65536
        return tuple(private_read(path, maximum) for path in self.paths[provider])

    def checked(self, provider):
        value = self.values[provider]
        if self.read(provider) != value:
            raise RuntimeConfigurationError
        return value  # 绝不在核对后重读并使用可能已替换的材料。

    def identity(self):
        return {
            provider: [hashlib.sha256(value).hexdigest() for value in values]
            for provider, values in self.values.items()
        }


class SignedEvidence:
    """RSA/SHA256 公钥验签。私钥留在账号核验者手中，不挂到任何服务或 CLI。

    每份签名必须绑定当前配置摘要及逐项人工/只读核验记录摘要；没有官方费用查询
    协议时不编造采集器。重读只验证原 checked_at，不以本地观察时间续期。
    """

    def __init__(self, config, fingerprint, credentials):
        self.config, self.fingerprint = config, fingerprint
        self.credentials = credentials
        self.public_key = private_read(config.evidence_public_key)

    def __call__(self, kind):
        try:
            if kind not in self.config.enabled_kinds:
                return None
            if kind != "local_motion":
                self.credentials.checked("text" if kind == "text" else "agnes")
            envelope = decode_frame(private_read(self.config.evidence_file))
            if set(envelope) != {"payload", "signature"}:
                return None
            payload = envelope["payload"]
            if set(payload) != {"config_sha256", "checks", "evidence"}:
                return None
            if payload["config_sha256"] != self.fingerprint:
                return None
            checks = payload["checks"]
            if set(checks) != {"billing", "overages", "isolation", "authorization", "protocol"}:
                return None
            for record in checks.values():
                if (
                    set(record) != {"method", "record_sha256"}
                    or record["method"] not in ("account-holder-readonly", "offline-probe")
                    or len(record["record_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in record["record_sha256"])
                ):
                    return None
            if any(
                checks[name]["method"] != "account-holder-readonly"
                for name in ("billing", "overages", "authorization")
            ):
                return None
            key = private_read(self.config.evidence_public_key)
            if key != self.public_key:
                return None  # 公钥轮换须重启重算配置身份，不让旧心跳接受新的签发者。
            signature = base64.b64decode(envelope["signature"], validate=True)
            if not 128 <= len(signature) <= 1024:
                return None
            with tempfile.TemporaryDirectory(prefix="omniflow-verify-") as directory:
                key_path, sig_path = Path(directory) / "public.pem", Path(directory) / "signature"
                key_path.write_bytes(key)
                sig_path.write_bytes(signature)
                result = subprocess.run(
                    [
                        "/usr/bin/openssl",
                        "dgst",
                        "-sha256",
                        "-verify",
                        str(key_path),
                        "-signature",
                        str(sig_path),
                    ],
                    input=canonical(payload),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                    check=False,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                )
            if result.returncode:
                return None
            return AccessEvidence(**payload["evidence"][kind])
        except Exception:
            return None


def load_config(settings, *, with_credentials=False):
    try:
        raw = private_read(settings.runtime_config)
        config = RuntimeConfig.model_validate(decode_frame(raw))
        key = private_read(config.evidence_public_key)
        credentials = CredentialSnapshot(config)
        fingerprint = hashlib.sha256(
            canonical(
                {
                    "config": config.model_dump(mode="json"),
                    "public_key_sha256": hashlib.sha256(key).hexdigest(),
                    "credential_sha256": credentials.identity(),
                    "data_dir": str(settings.data_dir),
                    "public_origin": settings.public_origin,
                }
            )
        ).hexdigest()
        if with_credentials:
            return config, fingerprint, credentials
        return config, fingerprint
    except Exception:
        raise RuntimeConfigurationError from None
