"""可信 agy 启动器：空根文件系统＋独立 PID/网络/IPC/用户空间，最小文件挂载。"""

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

from .agy_adapter import private_directory
from .process_transport import ProcessTransport
from .runtime_config import CredentialSnapshot, RuntimeConfigurationError, private_read
from .runtime_egress import EgressBroker


def image_digest(root):
    """签名核验记录绑定实际只读运行镜像，不信任仅有 manifest 文件的声明。"""
    root = Path(root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise RuntimeConfigurationError
    records = []
    for path in sorted([root, *root.rglob("*")]):
        info = path.lstat()
        name = str(path.relative_to(root))
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            # 镜像仅允许相对、不会越过镜像根的链接。
            if target.startswith("/") or not path.resolve().is_relative_to(root):
                raise RuntimeConfigurationError
            records.append([name, "link", target])
            continue
        if info.st_mode & 0o022 or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise RuntimeConfigurationError
        kind = "file" if stat.S_ISREG(info.st_mode) else "directory"
        content_digest = None
        if kind == "file":
            with path.open("rb") as stream:
                content_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        records.append([name, kind, stat.S_IMODE(info.st_mode), content_digest])
    # 规范 JSON 明确文件、目录、链接与内容摘要边界，不拼接有歧义的裸字节。
    return hashlib.sha256(
        json.dumps(records, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


class IsolatedTransport(ProcessTransport):
    def close(self):
        confirmed = super().close()
        if confirmed:
            self.broker.close()
            self.temporary.cleanup()
        return confirmed


class AgyLauncher:
    def __init__(self, config, credentials=None):
        self.config = config
        self.credentials = credentials if credentials is not None else CredentialSnapshot(config)

    def check(self):
        self.credentials.checked("text")
        if image_digest(self.config.rootfs) != self.config.rootfs_sha256:
            raise RuntimeConfigurationError
        if any(
            p.name not in {"usr", "bin", "sbin", "lib", "lib64", "etc", "opt"}
            for p in self.config.rootfs.iterdir()
        ):
            raise RuntimeConfigurationError
        for relative in ("usr/local/bin/agy", "usr/bin/python3"):
            if not os.access(self.config.rootfs / relative, os.X_OK):
                raise RuntimeConfigurationError
        for mount in self.config.auth_files:
            # 最小材料已与证据快照核对；不把内容放入 HOME、argv 或提示。
            path = mount.source
            if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
                raise RuntimeConfigurationError
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeConfigurationError
        if not os.access("/usr/bin/bwrap", os.X_OK):
            raise RuntimeConfigurationError

    def __call__(self, plan):
        self.check()  # 任何生产进程创建前重新校验镜像与最小挂载。
        home = Path(plan.env["HOME"])
        config_dir = home / ".gemini" / "antigravity-cli"
        private_directory(config_dir)
        settings_path = config_dir / "settings.json"
        settings = {
            **plan.settings,
            "permissions": {
                "allow": [],
                "ask": [],
                "deny": [*plan.settings["permissions"]["deny"], "mcp(*)"],
            },
        }
        # 当前使用官方 structured_output→可信 ToolGateway 路径，不加载外部 MCP。
        # deny 全部 MCP 与允许五工具同时存在会冲突，因此此入口不声明虚假 allow。
        if settings_path.exists():
            if private_read(settings_path) != json.dumps(settings, sort_keys=True).encode():
                raise RuntimeConfigurationError
        else:
            fd = os.open(settings_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(settings, sort_keys=True).encode())
        temporary = tempfile.TemporaryDirectory(prefix="omniflow-egress-")
        try:
            broker = EgressBroker(Path(temporary.name) / "socket", self.config.google_hosts)
        except BaseException:
            temporary.cleanup()
            raise
        argv = [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/",
            *[
                item
                for path in sorted(self.config.rootfs.iterdir())
                for item in ("--ro-bind", str(path), "/" + path.name)
            ],
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/run",
            "--bind",
            str(home),
            str(home),
            "--bind",
            str(plan.cwd),
            str(plan.cwd),
            "--ro-bind",
            str(settings_path),
            str(settings_path),
            "--ro-bind",
            str(broker.path),
            "/run/egress.sock",
            "--ro-bind",
            str(Path(__file__).with_name("sandbox_entry.py")),
            "/runtime-entry.py",
        ]
        targets = set()
        auth_fds = []
        try:
            auth_values = self.credentials.checked("text")
            for index, (mount, value) in enumerate(
                zip(self.config.auth_files, auth_values, strict=True)
            ):
                target = home / mount.target
                if target in targets or target == settings_path:
                    raise RuntimeConfigurationError
                targets.add(target)
                private_directory(target.parent)
                # 密封内存快照，避免原文件在核验后轮换；不产生宿主临时凭据副本。
                fd = os.memfd_create(
                    f"omniflow-auth-{index}", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC
                )
                auth_fds.append(fd)
                with os.fdopen(os.dup(fd), "wb") as stream:
                    stream.write(value)
                os.lseek(fd, 0, os.SEEK_SET)
                fcntl.fcntl(
                    fd,
                    fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_SEAL
                    | fcntl.F_SEAL_SHRINK
                    | fcntl.F_SEAL_GROW
                    | fcntl.F_SEAL_WRITE,
                )
                argv += ["--ro-bind-data", str(fd), str(target)]
            argv += [
                "--chdir",
                str(plan.cwd),
                "--",
                "/usr/bin/python3",
                "-I",
                "/runtime-entry.py",
                *plan.argv,
            ]
            transport = IsolatedTransport(
                argv,
                cwd=plan.cwd,
                env={
                    **plan.env,
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                },
                pass_fds=tuple(auth_fds),
            )
            transport.broker, transport.temporary = broker, temporary
            return transport
        except BaseException:
            broker.close()
            temporary.cleanup()
            raise
        finally:
            for fd in auth_fds:
                os.close(fd)  # bwrap接收独立副本，失败或宿主退出也不遗留磁盘秘密。
