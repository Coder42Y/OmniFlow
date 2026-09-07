#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电商 AI 创作工作台 - 电影级平滑运镜与花字动画引擎 (Motion Engine)
支持：
1. Dolly-in (缓入推进镜头)
2. Pan-left / Pan-right (平滑横移扫镜)
3. Dynamic-float (微呼吸动效)
4. 电商卖点花字与大字幕淡入渲染
5. 输出 9:16 / 16:9 H.264 竖屏/横屏短视频
"""

import os
import sys
import math
import subprocess
import shutil
from PIL import Image, ImageDraw, ImageFont, ImageFilter

NOTO_BOLD_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
NOTO_REGULAR_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

def get_font(font_path, size):
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()

def render_caption_layer(width, height, title="", subtitle="", tags=None):
    """
    使用 Pillow 渲染精致的电商卖点花字图层 (RGBA 透明底图)
    """
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_title = get_font(NOTO_BOLD_FONT, int(width * 0.058))
    font_sub = get_font(NOTO_REGULAR_FONT, int(width * 0.036))
    font_tag = get_font(NOTO_BOLD_FONT, int(width * 0.030))

    bottom_margin = int(height * 0.18)

    # 1. 底部轻微渐变暗角，提升文字可读性
    gradient = Image.new("RGBA", (width, int(height * 0.35)), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(gradient)
    for y in range(gradient.height):
        alpha = int(160 * (y / gradient.height) ** 1.5)
        g_draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    overlay.paste(gradient, (0, height - gradient.height), gradient)

    # 2. 标签气泡 (Tag Pills)
    if tags:
        tag_text = "  ·  ".join(tags)
        bbox = draw.textbbox((0, 0), tag_text, font=font_tag)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        px, py = int(width * 0.03), int(height * 0.008)
        pill_w, pill_h = tw + px * 2, th + py * 2
        pill_x = int((width - pill_w) / 2)
        pill_y = height - bottom_margin - int(height * 0.12)
        draw.rounded_rectangle([pill_x, pill_y, pill_x + pill_w, pill_y + pill_h], radius=int(pill_h / 2), fill=(245, 158, 11, 230))
        draw.text((pill_x + px, pill_y + py), tag_text, font=font_tag, fill=(0, 0, 0, 255))

    # 3. 主标题花字
    if title:
        bbox = draw.textbbox((0, 0), title, font=font_title)
        tw = bbox[2] - bbox[0]
        tx = int((width - tw) / 2)
        ty = height - bottom_margin - int(height * 0.065)
        # 阴影
        draw.text((tx + 3, ty + 3), title, font=font_title, fill=(0, 0, 0, 200))
        # 主文字
        draw.text((tx, ty), title, font=font_title, fill=(255, 255, 255, 255))

    # 4. 副标题/卖点利益点
    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=font_sub)
        tw = bbox[2] - bbox[0]
        tx = int((width - tw) / 2)
        ty = height - bottom_margin
        draw.text((tx + 2, ty + 2), subtitle, font=font_sub, fill=(0, 0, 0, 180))
        draw.text((tx, ty), subtitle, font=font_sub, fill=(243, 244, 246, 240))

    return overlay

def generate_motion_video(
    image_path,
    output_path,
    motion_type="dolly_in",
    aspect_ratio="9:16",
    duration=5.0,
    fps=25,
    title="",
    subtitle="",
    tags=None,
    ffmpeg_bin=None
):
    """
    基于源图像与电影级平滑运镜参数，渲染出高质感短视频
    :param image_path: 输入源海报/主图路径
    :param output_path: 输出 MP4 路径
    :param motion_type: 'dolly_in' | 'dolly_out' | 'pan_left' | 'pan_right' | 'dynamic_float'
    :param aspect_ratio: '9:16' (720x1280) | '16:9' (1280x720) | '1:1' (1080x1080)
    :param duration: 视频时长(秒)
    :param fps: 帧率
    :param title: 视频大字幕主标题
    :param subtitle: 视频副标题/痛点利益点
    :param tags: 卖点标签列表，如 ['专柜同款', '留香24H']
    """
    if ffmpeg_bin is None:
        ffmpeg_bin = shutil.which("ffmpeg") or "/home/kris/.local/bin/ffmpeg"

    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Source image not found: {image_path}")

    # 目标分辨率
    if aspect_ratio == "9:16":
        target_w, target_h = 720, 1280
    elif aspect_ratio == "16:9":
        target_w, target_h = 1280, 720
    else:
        target_w, target_h = 1080, 1080

    total_frames = int(duration * fps)

    # 1. 预先生成花字水印图层 (如果指定了标题或副标)
    overlay_path = None
    if title or subtitle or tags:
        overlay_img = render_caption_layer(target_w, target_h, title=title, subtitle=subtitle, tags=tags)
        overlay_path = output_path + ".overlay.png"
        overlay_img.save(overlay_path, "PNG")

    # 2. 构造 FFmpeg 平滑运镜滤镜表达式
    # 采用 scale + crop + zoompan 或平滑动态表达式
    # 为了保证画质和毫秒级稳定渲染，先将原图 scale2ref/scale 成稍大画幅，再平滑移动/缩放
    
    # 构建运镜 filter
    # 使用 zoompan filter，d 参数必须为整数帧数
    if motion_type == "dolly_in":
        # 缓推镜头：从 1.0 推进到 1.15，平滑居中推进
        z_expr = "min(zoom+0.0012,1.15)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type == "dolly_out":
        # 拉远镜头：从 1.15 缓缓拉回到 1.0
        z_expr = "if(eq(on,1),1.15,max(1.0,zoom-0.0012))"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type == "pan_left":
        # 从右往左缓慢横移
        z_expr = "1.12"
        x_expr = "(iw-iw/zoom)*(1-on/{})".format(total_frames)
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion_type == "pan_right":
        # 从左往右缓慢横移
        z_expr = "1.12"
        x_expr = "(iw-iw/zoom)*(on/{})".format(total_frames)
        y_expr = "ih/2-(ih/zoom/2)"
    else: # dynamic_float 微呼吸运镜
        z_expr = "1.08+0.04*sin(2*PI*on/{})".format(total_frames)
        x_expr = "iw/2-(iw/zoom/2)+10*sin(2*PI*on/{})".format(total_frames)
        y_expr = "ih/2-(ih/zoom/2)+8*cos(2*PI*on/{})".format(total_frames)

    # 缩放至填满目标画布的高画质 filter 链
    # zoompan 从单张静态图片生成 total_frames 帧平滑运镜
    # 注意：zoompan 本身会根据 d 参数产生多帧输出，输入图片绝不能加 -loop 1
    video_filter = (
        f"scale={target_w*2}:{target_h*2}:force_original_aspect_ratio=increase,"
        f"crop={target_w*2}:{target_h*2},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s={target_w}x{target_h}:fps={fps}"
    )

    cmd = [
        ffmpeg_bin, "-y",
        "-i", image_path
    ]

    if overlay_path and os.path.exists(overlay_path):
        cmd.extend(["-loop", "1", "-t", str(duration), "-i", overlay_path])
        # 叠加花字图层，并带淡入效果
        full_filter = f"[0:v]{video_filter}[bg];[1:v]format=rgba,fade=t=in:st=0.3:d=0.8:alpha=1[fg];[bg][fg]overlay=0:0[outv]"
        map_opt = ["-map", "[outv]"]
    else:
        full_filter = video_filter
        map_opt = []

    cmd.extend([
        "-filter_complex", full_filter
    ])
    cmd.extend(map_opt)
    cmd.extend([
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path
    ])

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg failed (code {res.returncode}): {res.stderr[-400:]}")
    finally:
        if overlay_path and os.path.exists(overlay_path):
            try:
                os.remove(overlay_path)
            except Exception:
                pass

    return output_path

if __name__ == "__main__":
    print("Motion engine module compiled successfully.")
