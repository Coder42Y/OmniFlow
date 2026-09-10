"""受保护的 UUID 文件仓库：不接受用户文件名/路径，原子发布且不覆盖版本。"""

import fcntl
import hashlib
import io
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from .auth_security import fail
from .db import _no_symlinks

MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "text/plain": "txt",
    "video/mp4": "mp4",
}


@dataclass(frozen=True)
class MediaInfo:
    media_type: str
    byte_size: int
    sha256: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    fps: float | None = None


def inspect_image(content, declared_type, settings):
    if len(content) > settings.max_upload_bytes:
        fail(413, "UPLOAD_TOO_LARGE", "图片超过单次上传大小上限")
    if declared_type not in MIMES.values():
        fail(415, "UNSUPPORTED_MEDIA_TYPE", "仅支持 JPEG、PNG 和 WebP 图片")
    try:
        with Image.open(io.BytesIO(content), formats=list(MIMES)) as image:
            if MIMES.get(image.format) != declared_type:
                fail(415, "UNSUPPORTED_MEDIA_TYPE", "声明类型与实际图片格式不符")
            width, height = image.size
            if width < 1 or height < 1 or width * height > settings.max_image_pixels:
                fail(422, "IMAGE_DECODE_FAILED", "图片超过解码像素上限")
            if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                fail(415, "UNSUPPORTED_MEDIA_TYPE", "不接受动画或多帧图片")
            image.verify()
        with Image.open(io.BytesIO(content), formats=list(MIMES)) as image:
            image.load()  # verify 不能替代实际解码；拒绝截断图和损坏压缩数据。
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        fail(422, "IMAGE_DECODE_FAILED", "图片无法安全解码")
    return MediaInfo(
        declared_type, len(content), hashlib.sha256(content).hexdigest(), width, height
    )


class MediaStorage:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.media_dir

    def check_root(self):
        _no_symlinks(self.root)
        if not self.root.is_dir() or self.root.stat().st_mode & 0o077:
            fail(507, "STORAGE_UNAVAILABLE", "素材存储不可用")

    def path(self, version_id):
        # 即使内部数据损坏也不能拼出任意路径。
        if not isinstance(version_id, str) or str(UUID(version_id)) != version_id:
            raise ValueError("内部版本标识无效")
        self.check_root()
        return self.root / (version_id + ".blob")

    @contextmanager
    def stage(self, content):
        self.check_root()
        path = None
        try:
            fd, name = tempfile.mkstemp(prefix="stage-", suffix=".part", dir=self.root)
            path = Path(name)
            with os.fdopen(fd, "w+b") as stream:
                # 维护进程不能删除活跃上传，即使上传进程被暂停很久。
                fcntl.flock(stream, fcntl.LOCK_EX)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                yield path
        except OSError:
            fail(507, "STORAGE_UNAVAILABLE", "素材无法保存，未创建新版本")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def publish(self, stage, version_id):
        # link 不覆盖已有文件；临时文件已 fsync，事务提交后移除临时名字。
        os.link(stage, self.path(version_id), follow_symlinks=False)
        self.sync_directory()

    def sync_directory(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def open(self, version):
        try:
            path = self.path(version["id"])
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            stream = os.fdopen(fd, "rb")
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_size != version["byte_size"]
            ):
                stream.close()
                fail(503, "STORAGE_UNAVAILABLE", "素材文件暂不可读")
            return stream
        except OSError:
            fail(503, "STORAGE_UNAVAILABLE", "素材文件暂不可读")


def byte_range(raw, size):
    """仅调用者已授权之后解析，避免用 416 泄露大小。"""
    if raw is None:
        return 0, size - 1, 200
    if len(raw) <= 100 and (match := re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", raw)):
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
        elif last and int(last) > 0:
            start, end = max(0, size - int(last)), size - 1
        else:
            return None
        if 0 <= start <= end < size:
            return start, end, 206
    return None
