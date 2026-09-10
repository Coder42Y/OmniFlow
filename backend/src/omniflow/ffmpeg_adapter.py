"""明确标识 local-ffmpeg 的本地运镜；固定滤镜/参数，输入只取任务占用的图片。

不是 AI 视频，没有 URL/命令行透传。测试可注入 runner，也可用合成图实际运行 FFmpeg。
"""

import io
import math
import os
import shutil
import subprocess
from uuid import UUID

from PIL import Image

from .artifacts import TaskMediaService, version_owned
from .media_provider import Accepted, Download, Observation, SubmissionRejected
from .media_storage import MediaStorage, inspect_image
from .media_validation import inspect_local_output
from .provider_gate import EvidenceGate
from .task_models import LocalMotionTaskCreate


def motion_filter(data):
    frames = math.ceil(data.seconds * 24)
    width, height = (720, 1280) if data.aspect_ratio == "9:16" else (1280, 720)
    # on 是 FFmpeg 自身输出帧编号；所有表达式来自封闭枚举，不插入提示词。
    position = {
        "dolly_in": (f"1+0.12*on/{frames}", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"),
        "pan_left": ("1.12", f"(iw-iw/zoom)*(1-on/{frames})", "ih/2-ih/zoom/2"),
        "pan_right": ("1.12", f"(iw-iw/zoom)*on/{frames}", "ih/2-ih/zoom/2"),
        "dynamic_float": (
            "1.12",
            f"(iw-iw/zoom)*(0.5+0.3*sin(on/{frames}*6.28))",
            "ih/2-ih/zoom/2",
        ),
    }
    zoom, x, y = position[data.motion_type]
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},zoompan=z='{zoom}':x='{x}':y='{y}':"
        f"d={frames}:s={width}x{height}:fps=24,format=yuv420p"
    ), frames


class FFmpegProvider:
    reference_editing_enabled = False

    def __init__(self, database, *, gate=None, runner=subprocess.run):
        self.database = database
        self.settings = database.settings
        self.gate = gate or EvidenceGate()
        self.runner = runner
        self.executable = shutil.which("ffmpeg")
        self.storage = MediaStorage(self.settings)

    def check(self, kind):
        if kind != "local_motion":
            raise ValueError("不是本地运镜")
        self.gate.check(kind)
        if self.executable is None:
            raise ValueError("本地 FFmpeg 尚不可用")

    def submit(self, task, inputs):
        try:
            self.check(task["kind"])
            data = LocalMotionTaskCreate.model_validate(task["requested_parameters"])
            vid = str(data.image_version_id)
            if inputs != (vid,):
                raise ValueError
            with self.database.snapshot() as connection:
                scope = TaskMediaService.scope(connection, task["id"])
                if not scope["activated_at"]:
                    raise ValueError
                if (
                    connection.execute(
                        "SELECT 1 FROM task_artifact_uses "
                        "WHERE task_id=? AND version_id=? AND role='input'",
                        (task["id"], vid),
                    ).fetchone()
                    is None
                ):
                    raise ValueError
                version = version_owned(connection, vid, scope["owner_id"], image=True)
                output = connection.execute(
                    "SELECT output_version_id FROM tasks WHERE id=?", (task["id"],)
                ).fetchone()[0]
                with self.storage.open(version) as stream:
                    content = stream.read(self.settings.max_upload_bytes + 1)
                mime = version["media_type"]
            inspect_image(content, mime, self.settings)
            # 重新编码单帧 RGB PNG，剥离复杂元数据，不把任意输入交给 FFmpeg 探测协议。
            with Image.open(io.BytesIO(content)) as image:
                encoded = io.BytesIO()
                image.convert("RGB").save(encoded, format="PNG")
            filters, frames = motion_filter(data)
            self.gate.check("local_motion")
        except Exception:
            raise SubmissionRejected from None
        with self.storage.stage(b"") as target:
            args = [
                self.executable,
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-protocol_whitelist",
                "file,pipe",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "-i",
                "pipe:0",
                "-vf",
                filters,
                "-frames:v",
                str(frames),
                "-an",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-fs",
                str(self.settings.max_generated_bytes),
                "-f",
                "mp4",
                "-y",
                str(target),
            ]
            result = self.runner(
                args,
                input=encoded.getvalue(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=90,
                check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
            if result.returncode != 0 or target.stat().st_size >= self.settings.max_generated_bytes:
                raise ValueError("运镜失败或达到容量上限")
            with target.open("rb") as stream:
                content = stream.read(self.settings.max_generated_bytes + 1)
            inspect_local_output(content, task["requested_parameters"], self.settings)
            # 先完成文件基本校验，再发布稳定目标；worker 只补齐最终版本与事实。
            with target.open("rb") as stream:
                os.fsync(stream.fileno())
            self.storage.publish(target, output)
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO adapter_receipts VALUES (?,?)", (task["id"], output)
                )
        return Accepted("local:" + task["id"])

    def poll(self, task_id):
        if not task_id.startswith("local:"):
            raise ValueError
        tid = str(UUID(task_id[6:]))
        with self.database.snapshot() as connection:
            row = connection.execute(
                "SELECT result_key FROM adapter_receipts WHERE task_id=?", (tid,)
            ).fetchone()
        if row is None:
            raise ValueError
        return Observation("completed", "local:" + row[0])

    def download(self, result_key, max_bytes):
        # 正常 worker 会直接复用稳定文件；此接口保留同一结果读取而非重新运镜。
        if not result_key.startswith("local:"):
            raise ValueError
        path = self.storage.path(str(UUID(result_key[6:])))
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError
        return Download(content, "video/mp4")


class MediaRouter:
    def __init__(self, agnes, local):
        self.agnes, self.local = agnes, local
        self.reference_editing_enabled = agnes.reference_editing_enabled

    def check(self, kind):
        return (self.local if kind == "local_motion" else self.agnes).check(kind)

    def submit(self, task, inputs):
        return (self.local if task["kind"] == "local_motion" else self.agnes).submit(task, inputs)

    def poll(self, task_id):
        return (self.local if task_id.startswith("local:") else self.agnes).poll(task_id)

    def download(self, result_key, max_bytes):
        return (self.local if result_key.startswith("local:") else self.agnes).download(
            result_key, max_bytes
        )
