#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电商 AI 创作工作台 - 后端核心能力端到端自动化测试套件
覆盖：
1. 极限词独立合规检测 (/api/script/check-compliance)
2. 文案脚本工作区爆款生成 (/api/script/generate)
3. 视觉海报工作区海报生成 (/api/generate-image)
4. 动态视频工作区运镜视频生成 (/api/motion-video/create)
5. FirstPage 对话与全案生成 (/api/chat)
"""

import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8080"

def post_json(endpoint, data, timeout=60):
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def test_1_compliance_check():
    print("\n[TEST 1] 广告法极限词独立检测 (/api/script/check-compliance)...")
    text_with_violations = "这款香水是全网第一、顶级品质，保证100%永久留香，绝无仅有的神效！"
    status, res = post_json("/api/script/check-compliance", {"text": text_with_violations})
    assert status == 200, f"Status {status}"
    assert res.get("success") is True
    assert res.get("is_compliant") is False
    assert len(res.get("risk_words", [])) >= 3
    print(f"  PASS: 成功排查出极限违规词: {res.get('risk_words')}")

    # 合规测试
    text_clean = "冷冽木质香调，前调雪松中调琥珀，散发沉稳内敛气质。"
    status2, res2 = post_json("/api/script/check-compliance", {"text": text_clean})
    assert res2.get("is_compliant") is True
    assert len(res2.get("risk_words")) == 0
    print("  PASS: 正常合规文案检测通过，零误报。")

def test_2_script_generate():
    print("\n[TEST 2] 文案脚本工作区生成 (/api/script/generate)...")
    t0 = time.time()
    payload = {
        "product_name": "黑曜石冷香淡雅男士香水",
        "selling_points": "冷冽雪松木质香，商务宴请高冷精英感，长效留香",
        "target_audience": "25-35岁职场商务男性"
    }
    status, res = post_json("/api/script/generate", payload, timeout=90)
    assert status == 200, f"Status {status}"
    assert res.get("success") is True
    data = res.get("data", {})
    script = data.get("script", {})
    assert "hook" in script, "缺少黄金3秒抓人开头"
    assert "fabe" in script, "缺少 FABE 话术结构"
    assert "call_to_action" in script, "缺少促单号召"
    print(f"  PASS: 成功生成带货脚本 (耗时 {time.time()-t0:.2f}s):")
    print(f"    - Hook: {script.get('hook')}")
    print(f"    - FABE: {script.get('fabe')}")

def test_3_generate_image():
    print("\n[TEST 3] 视觉海报工作区海报生成 (/api/generate-image)...")
    t0 = time.time()
    payload = {
        "prompt": "A modern sleek perfume bottle standing on obsidian marble, dramatic studio light",
        "style": "大牌质感"
    }
    status, res = post_json("/api/generate-image", payload, timeout=45)
    assert status == 200, f"Status {status}"
    assert res.get("success") is True
    img_url = res.get("image_url")
    assert img_url, "未返回图片 URL"
    print(f"  PASS: 成功生成商业海报 (耗时 {time.time()-t0:.2f}s): {img_url}")
    return img_url

def test_4_motion_video(img_url):
    print("\n[TEST 4] 动态视频工作区运镜合成 (/api/motion-video/create)...")
    t0 = time.time()
    payload = {
        "image_url": img_url,
        "motion_type": "dolly_in",
        "aspect_ratio": "9:16",
        "duration": 4.0,
        "title": "黑曜石男士淡雅",
        "subtitle": "24H持久冷香 · 商务高冷范",
        "tags": ["商务男香", "冷冽木质", "留香持久"]
    }
    status, res = post_json("/api/motion-video/create", payload, timeout=30)
    assert status == 200, f"Status {status}"
    assert res.get("success") is True
    video_url = res.get("video_url")
    assert video_url, "未返回视频 URL"

    # 物理验证视频文件与编码
    local_path = os.path.join("/home/kris/Codes/AIPannel", video_url.lstrip("/"))
    assert os.path.exists(local_path), f"物理视频文件不存在: {local_path}"
    file_size = os.path.getsize(local_path)
    assert file_size > 100000, f"视频文件过小: {file_size}"

    probe = subprocess.run([
        "/home/kris/.local/bin/ffprobe", "-v", "error",
        "-show_entries", "stream=width,height,duration,codec_name",
        "-of", "json", local_path
    ], capture_output=True, text=True)
    probe_data = json.loads(probe.stdout)
    stream = probe_data["streams"][0]
    assert stream["codec_name"] == "h264"
    assert stream["width"] == 720
    assert stream["height"] == 1280
    assert float(stream["duration"]) >= 3.8

    print(f"  PASS: 运镜视频生成并通过 ffprobe 物理检验 (耗时 {time.time()-t0:.2f}s):")
    print(f"    - URL: {video_url}")
    print(f"    - Resolution: {stream['width']}x{stream['height']} (9:16)")
    print(f"    - Codec: {stream['codec_name']}, Duration: {stream['duration']}s, Size: {file_size} bytes")

def test_5_firstpage_chat():
    print("\n[TEST 5] FirstPage 智能总览对话 (/api/chat)...")
    t0 = time.time()
    # 1. 常规专业解答
    status, res = post_json("/api/chat", {
        "messages": [{"role": "user", "content": "新媒体带货如何设计黄金3秒抓人开头？请简述3点"}]
    }, timeout=45)
    assert status == 200, f"Status {status}"
    assert res.get("success") is True
    reply = res.get("reply", "")
    assert len(reply) > 50, "回复内容过短"
    print(f"  PASS: FirstPage 专业问答成功响应 (耗时 {time.time()-t0:.2f}s)")

if __name__ == "__main__":
    print("==========================================================")
    print("🚀 开始运行电商 AI 创作工作台后端端到端自动化测试套件")
    print("==========================================================")
    try:
        test_1_compliance_check()
        test_2_script_generate()
        img_url = test_3_generate_image()
        test_4_motion_video(img_url)
        test_5_firstpage_chat()
        print("\n==========================================================")
        print("🎉 全部 5 项后端端到端测试 100% 通过！")
        print("==========================================================")
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
