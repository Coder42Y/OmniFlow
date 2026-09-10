"""生成文件的基本校验；requested 不参与实际宽高/时长的计算。"""

import hashlib
import json
import math
import subprocess

from .auth_security import fail
from .media_storage import MediaInfo, inspect_image
from .task_models import LocalMotionTaskCreate


def inspect_local_output(content, request, settings):
    """本地运镜稳定文件还须符合该任务固定的时长、画幅及帧率。

    仅用于本地适配器发布及其丢失 receipt 的恢复，不套用在供应商结果上
    将 requested 冒充 actual。
    """
    data = LocalMotionTaskCreate.model_validate(request)
    info = inspect_output(content, "video/mp4", "local_motion", settings)
    width, height = (720, 1280) if data.aspect_ratio == "9:16" else (1280, 720)
    if (
        abs(info.duration_seconds - math.ceil(data.seconds * 24) / 24) > 0.1
        or (info.width, info.height) != (width, height)
        or abs(info.fps - 24) > 0.01
    ):
        raise ValueError("本地输出不完整或与任务不符")
    return info


def inspect_output(content, media_type, kind, settings):
    if type(content) is not bytes or not content or len(content) > settings.max_generated_bytes:
        fail(422, "VALIDATION_ERROR", "生成文件为空或超过单次容量上限")
    if kind == "image":
        return inspect_image(content, media_type, settings)
    if media_type != "video/mp4" or len(content) < 12 or content[4:8] != b"ftyp":
        fail(422, "VALIDATION_ERROR", "输出不是可识别的 MP4 文件")
    try:
        # 无 shell、无模型参数；强制 mov 解复用及 pipe 协议，输入不能联网取引用。
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "pipe",
                "-f",
                "mov",
                "-i",
                "pipe:0",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,duration,avg_frame_rate:format=duration",
                "-of",
                "json",
            ],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
        if len(result.stdout) > 65536:
            raise ValueError
        metadata = json.loads(result.stdout)
        stream = metadata["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        duration = float(stream.get("duration", metadata.get("format", {}).get("duration")))
        numerator, denominator = stream["avg_frame_rate"].split("/")
        fps = float(numerator) / float(denominator)
        if (
            stream["codec_name"] not in ("h264", "hevc", "av1", "vp9", "mpeg4")
            or not 1 <= width * height <= settings.max_image_pixels
            or not math.isfinite(duration)
            or not 0 < duration <= 120
            or not math.isfinite(fps)
            or not 0 < fps <= 240
        ):
            raise ValueError
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        ZeroDivisionError,
    ):
        fail(422, "VALIDATION_ERROR", "视频无法通过基本校验，保留原任务等待核对")
    return MediaInfo(
        "video/mp4", len(content), hashlib.sha256(content).hexdigest(), width, height, duration, fps
    )
