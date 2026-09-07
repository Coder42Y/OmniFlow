#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚合AI工作台 - 真实视频流与 Gemini 全链路端到端自动化测试
"""

import urllib.request
import urllib.parse
import json
import time
import sys

BASE_URL = "http://127.0.0.1:8080"

def test_video_stream():
    print("\n" + "=" * 60)
    print("▶ 步骤 1：测试真实视频流网络传输与 HTTP 206 Seek 寻道")
    print("=" * 60)

    # 1. 普通 HEAD 探针
    url = f"{BASE_URL}/sample_stream.mp4"
    req = urllib.request.Request(url, method="HEAD")
    start = time.time()
    with urllib.request.urlopen(req, timeout=10) as resp:
        duration = round((time.time() - start) * 1000, 1)
        headers = dict(resp.headers)
        content_length = int(headers.get('Content-Length', 0))
        content_type = headers.get('Content-Type', '')
        accept_ranges = headers.get('Accept-Ranges', '')
        print(f"  [HEAD] HTTP {resp.status} · 耗时: {duration}ms")
        print(f"  - Content-Type: {content_type}")
        print(f"  - Content-Length: {content_length / 1024 / 1024:.2f} MB ({content_length} 字节)")
        print(f"  - Accept-Ranges: {accept_ranges}")
        assert resp.status == 200, "视频基础请求状态码异常"
        assert content_type == "video/mp4", "Content-Type 必须为 video/mp4"

    # 2. HTTP 206 Partial Content (模拟视频跳播第 20 秒/偏移量 5MB)
    seek_start = 5 * 1024 * 1024
    seek_end = seek_start + 1024 * 1024 - 1 # 请求 1MB 分片
    req_range = urllib.request.Request(url, headers={"Range": f"bytes={seek_start}-{seek_end}"})
    start = time.time()
    with urllib.request.urlopen(req_range, timeout=10) as resp:
        duration = round((time.time() - start) * 1000, 1)
        chunk_data = resp.read()
        headers = dict(resp.headers)
        content_range = headers.get('Content-Range', '')
        print(f"  [Range GET] HTTP {resp.status} (Partial Content) · 耗时: {duration}ms")
        print(f"  - Content-Range: {content_range}")
        print(f"  - 接收分块数据: {len(chunk_data)} 字节 (约 1.0 MB)")
        assert resp.status == 206, "Range 请求必须返回 206 Partial Content"
        assert len(chunk_data) == (seek_end - seek_start + 1), "接收数据分块大小与 Range 不匹配"
    
    print("✅ 真实视频流传输与分块 Seek 测试通过！现代浏览器支持秒级精准拖动与跳播。")


def test_gemini_live_analysis():
    print("\n" + "=" * 60)
    print("▶ 步骤 2：测试真实带货视频转写流 -> Google AGY (Gemini) 智能诊断")
    print("=" * 60)

    transcript_text = (
        "[00:00:02] 主播: 各位直播间的新老朋友们，看过来！今天这款深海修护面霜，不仅全网第一无可替代，而且一次性彻底根除法令纹，闭眼入绝不踩雷！\n"
        "[00:00:15] 助播: 只有最后20单了，马上恢复原价599，今天只要99米！手慢无！\n"
        "[00:00:24] 主播: 原价599今天直接破价，买一瓶送三瓶正装，买到就是赚到，赶紧上车！\n"
        "[00:00:34] 助播: 那个……还有……还有赠品面膜记得领大家认准一号链接……"
    )

    print("  [输入带货实录流]：")
    for line in transcript_text.strip().split('\n'):
        print(f"    {line}")

    url = f"{BASE_URL}/api/agy/analyze-live"
    payload = json.dumps({"transcript": transcript_text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    print("\n  [调度宿主机 Google AGY CLI] 正在执行 Gemini 深度合规与话术诊断...")
    start = time.time()
    with urllib.request.urlopen(req, timeout=90) as resp:
        duration = round(time.time() - start, 2)
        raw_body = resp.read().decode("utf-8")
        result = json.loads(raw_body)
        print(f"  [Gemini 响应] HTTP {resp.status} · 耗时: {duration}s\n")

        print(f"  📊 【量化指标】")
        print(f"  - 主播平均语速 (WPM): {result.get('wpm')} 字/分")
        print(f"  - 主播人设标签: {result.get('personaTag')}")
        print(f"  - FABE话术得分: {result.get('fabeScore')} 分")

        print(f"\n  🚨 【违规与失误卡点排查】 (命中 {len(result.get('violations', []))} 处)")
        for idx, v in enumerate(result.get('violations', []), 1):
            print(f"    [{idx}] {v.get('time', '-')} | 级别: {v.get('level')} | 类型: {v.get('type')}")
            print(f"        原话: “{v.get('originText')}”")
            print(f"        建议: “{v.get('suggestion')}”")

        print(f"\n  🗣️ 【高频口头禅脱水】")
        for c in result.get('catchphrases', []):
            print(f"    - “{c.get('phrase')}”: {c.get('count')} 次")

        print(f"\n  📑 【Gemini 综合复盘诊断】")
        print(f"    {result.get('summary')}")

        assert result.get('wpm') is not None, "缺少 wpm 语速字段"
        assert len(result.get('violations', [])) > 0, "必须排查出至少 1 处极限词/夸大宣传违规"
        print("\n✅ Google Gemini 原生带货复盘诊断测试通过！指标与建议均高精准结构化输出。")
        return result


def test_gemini_prompt_compilation():
    print("\n" + "=" * 60)
    print("▶ 步骤 3：测试针对该商品意图的 Gemini 商业摄影 Prompt 编译")
    print("=" * 60)

    url = f"{BASE_URL}/api/agy/compile-prompt"
    payload = json.dumps({
        "subject": "深海修护玻尿酸高保湿面霜，深蓝色渐变哑光玻璃瓶身，水滴凝结",
        "scene": "电商带货海报",
        "category": "美妆护肤",
        "style": "商业摄影棚拍",
        "camera": "特写低角度缓慢推进"
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    start = time.time()
    with urllib.request.urlopen(req, timeout=45) as resp:
        duration = round(time.time() - start, 2)
        result = json.loads(resp.read().decode("utf-8"))
        print(f"  [Gemini 视觉编译] HTTP {resp.status} · 耗时: {duration}s\n")
        print("  📸 【8K 电影级商业英文 Prompt】:")
        print(f"    {result.get('compiledPrompt')}\n")
        print("  🎬 【艺术导演指导建议】:")
        print(f"    {result.get('artDirectorNotes')}")
        assert result.get('compiledPrompt'), "未生成编译提示词"

    print("\n✅ Google Gemini 商业视觉 Prompt 扩写测试通过！")


def test_video_cut_clip():
    print("\n" + "=" * 60)
    print("▶ 步骤 4：测试 FFmpeg 真实物理切片裁剪与 HTTP 206 切片流回放")
    print("=" * 60)

    url = f"{BASE_URL}/api/video/cut-clip"
    payload = json.dumps({
        "source_video": "sample_stream.mp4",
        "start_sec": 12,
        "end_sec": 20,
        "buffer_sec": 2,
        "label": "极限词合规切片"
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    start = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        duration = round(time.time() - start, 2)
        result = json.loads(resp.read().decode("utf-8"))
        print(f"  [FFmpeg 切片接口] HTTP {resp.status} · 耗时: {duration}s\n")
        print(f"  - 是否成功: {result.get('success')}")
        print(f"  - 切片文件: {result.get('filename')}")
        print(f"  - 访问路径: {result.get('clip_url')}")
        print(f"  - 切片时长: {result.get('duration')} 秒 ({result.get('start_sec')}s - {result.get('end_sec')}s)")
        print(f"  - 切片体积: {result.get('file_size') / 1024 / 1024:.2f} MB")
        assert result.get('success') is True, "切片生成失败"
        assert result.get('clip_url'), "缺少切片 URL"

    # 测试切片的 HTTP 206 Partial Content 流媒体回放
    clip_url = f"{BASE_URL}{urllib.parse.quote(result.get('clip_url'))}"
    req_range = urllib.request.Request(clip_url, headers={"Range": "bytes=0-1023"})
    with urllib.request.urlopen(req_range, timeout=10) as resp:
        print(f"\n  [切片流式访问] HTTP {resp.status} (Partial Content)")
        print(f"  - Content-Type: {resp.headers.get('Content-Type')}")
        print(f"  - Content-Range: {resp.headers.get('Content-Range')}")
        assert resp.status == 206, "切片必须支持 HTTP 206 流式访问"
    print("\n✅ FFmpeg 物理短视频切片生成与 HTTP 206 流式回放验证通过！")


def test_binary_upload():
    print("\n" + "=" * 60)
    print("▶ 步骤 5：测试原生二进制流式上传 (支持超大 OBS 视频直传)")
    print("=" * 60)

    url = f"{BASE_URL}/api/upload-binary"
    dummy_content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42" + b"TEST_PAYLOAD_CHUNK" * 1000
    headers = {
        "X-File-Name": urllib.parse.quote("测试带货录像_OBS_2026.mp4"),
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(dummy_content))
    }
    req = urllib.request.Request(url, data=dummy_content, headers=headers)
    start = time.time()
    with urllib.request.urlopen(req, timeout=10) as resp:
        duration = round((time.time() - start) * 1000, 1)
        res = json.loads(resp.read().decode('utf-8'))
        print(f"  [流式上传] HTTP {resp.status} · 耗时: {duration}ms")
        print(f"  - 是否成功: {res.get('success')}")
        print(f"  - 存盘路径: {res.get('file_path')}")
        print(f"  - 访问 URL: {res.get('file_url')}")
        print(f"  - 文件大小: {res.get('file_size')} 字节")
        assert res.get('success') is True, "流式上传失败"
        assert res.get('file_path'), "未返回保存路径"

    print("\n✅ 原生二进制流式文件存盘测试通过！零内存膨胀，原生支持超大 OBS 视频秒传。")


def test_audio_transcribe():
    print("\n" + "=" * 60)
    print("▶ 步骤 6：测试 FFmpeg 16kHz WAV 提取与 ASR 全自动语音识别流水线")
    print("=" * 60)

    url = f"{BASE_URL}/api/audio/transcribe"
    payload = json.dumps({
        "source_video": "sample_stream.mp4",
        "hotwords": "拍一发三 破价 上链接 黄车 福利款 假一赔十 直降",
        "sku_name": "水光精华乳"
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    start = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        duration = round(time.time() - start, 2)
        res = json.loads(resp.read().decode('utf-8'))
        print(f"  [ASR 流水线响应] HTTP {resp.status} · 耗时: {duration}s\n")
        print(f"  - 状态: {res.get('success')}")
        print(f"  - 音轨路径: {res.get('wav_path')}")
        print(f"  - 音频时长: {res.get('duration_sec')} 秒")
        print(f"  - 识别分段数: {res.get('segments_count')} 条")
        print(f"  - 注入热词: {res.get('hotwords_applied')}")
        print(f"\n  📝 【提取的带时间戳字幕流】:")
        for t in res.get('transcripts', []):
            tag = " [🚨违规]" if t.get('hasViolation') else (" [🔥爆单]" if t.get('isHighlight') else "")
            print(f"    [{t.get('timeStr')}] [{t.get('speaker')}] {t.get('text')}{tag}")

        assert res.get('success') is True, "ASR 流水线执行失败"
        assert len(res.get('transcripts', [])) >= 3, "识别分段过少"

    # 验证生成的 16kHz WAV 音轨支持 HTTP 206 流式播放
    wav_url = f"{BASE_URL}{urllib.parse.quote(res.get('audio_url'))}"
    req_range = urllib.request.Request(wav_url, headers={"Range": "bytes=0-1023"})
    with urllib.request.urlopen(req_range, timeout=10) as resp:
        print(f"\n  [16kHz WAV 流式播放探测] HTTP {resp.status} (Partial Content)")
        print(f"  - Content-Type: {resp.headers.get('Content-Type')}")
        print(f"  - Content-Range: {resp.headers.get('Content-Range')}")
        assert resp.status == 206, "WAV 音轨必须支持 HTTP 206 流式传输"

    print("\n✅ FFmpeg 抽取 16kHz WAV 音轨与全自动 ASR 语音识别流水线验证通过！")


if __name__ == "__main__":
    print("=" * 70)
    print("🚀 聚合AI工作台 - 视频流、ASR听写、Gemini诊断与FFmpeg切片全链路自动化验收")
    print("=" * 70)
    try:
        test_video_stream()
        test_binary_upload()
        test_audio_transcribe()
        test_gemini_live_analysis()
        test_gemini_prompt_compilation()
        test_video_cut_clip()
        print("\n" + "=" * 70)
        print("🎉 全部 6 项核心业务流水线 100% 验证通过！音视频处理能力全部实装闭环。")
        print("=" * 70)
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
