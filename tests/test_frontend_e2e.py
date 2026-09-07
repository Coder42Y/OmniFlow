#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前端与全链路协同端到端自动化测试套件
覆盖:
1. 前端 HTML 契约、新命名体系 (FirstPage + 三大工作区) 与陈旧词汇排查
2. Vue Setup 响应式属性、跨工作区流转 (sendToVisual, sendToMotion, sendToScript) 方法闭环
3. 静态资源与上传/裁剪目录可访问性
4. 真实用户业务流全链路穿透: 文案生成 ➔ 携带分镜出海报 ➔ 送入视频工作区 ➔ 运镜合成 ➔ ffprobe 物理校验
"""

import os
import sys
import json
import time
import re
import subprocess
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8080"

def log(msg, tag="INFO"):
    colors = {
        "INFO": "\033[94m",
        "PASS": "\033[92m",
        "FAIL": "\033[91m",
        "WARN": "\033[93m"
    }
    c = colors.get(tag, "")
    end = "\033[0m"
    print(f"{c}[{tag}] {msg}{end}")

def http_get(path):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "AIPannel-E2E-Tester"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)

def http_post(path, data):
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": "AIPannel-E2E-Tester"
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw}
    except Exception as e:
        return 0, {"error": str(e)}

def test_html_and_naming_spec():
    """测试 1: 前端页面渲染契约与新旧命名体系排查"""
    log("▶ 执行测试 1: 验证前端页面渲染与全新命名体系契约...")
    code, html = http_get("/")
    assert code == 200, f"前端首页响应非 200: {code}"
    
    # 1. 验证陈旧空洞词汇彻底清除
    forbidden_words = ["智囊", "复盘卡点", "工坊"]
    found_forbidden = []
    for fw in forbidden_words:
        matches = re.findall(rf'>[^<]*{fw}[^<]*<', html)
        if matches:
            found_forbidden.append((fw, matches[:2]))
            
    if found_forbidden:
        for fw, samples in found_forbidden:
            log(f"发现未彻底清除的陈旧命名: '{fw}' 样例: {samples}", "FAIL")
        assert False, f"前端仍含有已废弃陈旧命名词: {[x[0] for x in found_forbidden]}"
    log("✓ 已确认彻底废除 '智囊'、'复盘卡点'、'工坊' 等旧命名", "PASS")

    # 2. 验证新命名体系必须全数存在 (Omni Hub + 三大工作区)
    required_workspaces = [
        "FirstPage",
        "视觉海报工作区",
        "动态视频工作区",
        "文案脚本工作区"
    ]
    for w in required_workspaces:
        assert w in html, f"页面中未找到新命名工作区: {w}"
    log("✓ 已确认新命名体系 (Omni Hub/FirstPage + 三大工作区) 100% 存在", "PASS")

    # 3. 验证视图定义包含四大工作区容器
    nav_definitions = [
        "currentNav === 'first_page'",
        "currentNav === 'visual'",
        "currentNav === 'motion'",
        "currentNav === 'script'"
    ]
    for nd in nav_definitions:
        assert nd in html, f"缺少工作区视图绑定定义: {nd}"
    log("✓ 已确认核心工作区 v-show 视图容器完整绑定", "PASS")

    # 4. 安全铁律：前端代码严禁泄露任何明文 API Key (sk-)
    assert "sk-" not in html, "严重安全漏洞：前端页面严禁包含明文 API Key (sk-)！"
    log("✓ 已严格证实前端 HTML 源码中零明文密钥泄漏 (0 sk- keys)", "PASS")

def test_vue_setup_and_flow_methods():
    """测试 2: 验证 Vue setup 响应式变量与跨工作区流转协同方法"""
    log("▶ 执行测试 2: 验证 Vue setup 响应式属性与跨工作区流转函数...")
    code, html = http_get("/")
    assert code == 200
    
    script_matches = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
    js_code = script_matches[-1] if script_matches else ''
    assert js_code, 'HTML 中未找到 <script> 块'
    

    assert "currentNav = ref('first_page')" in js_code or 'currentNav = ref("first_page")' in js_code, \
        "默认首页必须是 first_page"
    log("✓ 已确认默认初始导航为 FirstPage", "PASS")

    required_flow_funcs = [
        "sendToVisual",
        "sendToMotion",
        "sendToScript"
    ]
    for ff in required_flow_funcs:
        assert ff in js_code, f"缺少跨工作区流转方法: {ff}"
    log("✓ 已确认跨工作区流转方法 (sendToVisual, sendToMotion, sendToScript) 就位", "PASS")

    required_api_calls = [
        "generatePoster",
        "generateMotionVideoDirect",
        "generateScript",
        "checkComplianceDirect"
    ]
    for ac in required_api_calls:
        assert ac in js_code, f"缺少工作区关键生成方法: {ac}"
    log("✓ 已确认海报渲染、运镜视频、爆款脚本与合规检测方法就位", "PASS")

def test_full_cross_workspace_lifecycle():
    """测试 3: 模拟用户端到端全链路流转操作"""
    log("▶ 执行测试 3: 模拟用户跨工作区全案创作真实流转...")

    # 步骤 1: 用户在文案脚本工作区生成脚本
    log("1. 在【文案脚本工作区】生成爆款口播与 FABE 拆解...")
    c_status, c_data = http_post("/api/script/generate", {
        "product_name": "黑曜石男士淡雅冷香水",
        "highlights": "24小时持久留香，瑞士奇华顿香精，冷冽雪松与佛手柑，不含酒精刺激",
        "target_audience": "25-35岁高品位职场精英男士"
    })
    assert c_status == 200, f"生成脚本失败: {c_data}"
    oral = c_data.get("oral_script", "")
    visual_prompt = c_data.get("visual_prompt", "")
    assert len(oral) > 20, "口播脚本内容过短"
    assert len(visual_prompt) > 5, "未能提炼出分镜海报提示词"
    log(f"   ✓ 成功生成带货口播与分镜: {visual_prompt[:40]}...", "PASS")

    # 步骤 2: 用户携带提炼分镜提示词，流转至【视觉海报工作区】渲染海报
    log("2. 携带提炼分镜流转至【视觉海报工作区】生成商业海报...")
    p_status, p_data = http_post("/api/generate-image", {
        "prompt": f"{visual_prompt}，大牌高级黑金质感，棚拍商业柔光箱",
        "provider": "agnes"
    })
    assert p_status == 200, f"海报渲染接口异常: {p_data}"
    poster_url = p_data.get("image_url", "")
    assert poster_url and poster_url.startswith("/uploads/"), f"海报 URL 非预期: {poster_url}"
    log(f"   ✓ 成功渲染商业海报并落盘: {poster_url}", "PASS")

    # 步骤 3: 用户将海报一键流转至【动态视频工作区】，制作 5 秒平滑运镜短视频
    log("3. 一键将海报流转至【动态视频工作区】合成 9:16 平滑运镜视频...")
    m_status, m_data = http_post("/api/motion-video/create", {
        "image_url": poster_url,
        "motion_type": "dolly_in",
        "title": "黑曜石冷香极境",
        "subtitle": "24小时持久留香 · 高冷商务气场",
        "tags": "男士冷香, 新品首发, 专柜同香",
        "aspect_ratio": "9:16",
        "duration": 3.5
    })
    assert m_status == 200, f"运镜视频合成失败: {m_data}"
    video_url = m_data.get("video_url", "")
    assert video_url and video_url.startswith("/clips/"), f"视频 URL 非预期: {video_url}"
    log(f"   ✓ 运镜引擎秒级合成完成: {video_url}", "PASS")

    # 步骤 4: 物理校验视频物理文件
    log("4. 对产出的短视频进行 ffprobe 物理检测...")
    video_rel = video_url.lstrip("/")
    video_abs = os.path.abspath(video_rel)
    assert os.path.exists(video_abs), f"生成的短视频文件物理不存在: {video_abs}"
    assert os.path.getsize(video_abs) > 50000, f"视频文件尺寸异常: {os.path.getsize(video_abs)} bytes"

    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,r_frame_rate",
        "-of", "json",
        video_abs
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"ffprobe 检测异常: {res.stderr}"
    probe_meta = json.loads(res.stdout)
    streams = probe_meta.get("streams", [])
    assert len(streams) > 0, "视频未包含有效视频流"
    v_info = streams[0]
    width = v_info.get("width")
    height = v_info.get("height")
    codec = v_info.get("codec_name")
    log(f"   ✓ 物理视频参数: {width}x{height}, 编码: {codec}", "PASS")
    assert width == 720 and height == 1280, f"分辨率不符合 9:16 竖屏要求: {width}x{height}"
    assert codec == "h264", f"编码非标准 H.264: {codec}"

def run_all():
    log("==================================================")
    log("开始执行 AIPannel 前端与跨工作区协同端到端测试")
    log("==================================================")
    start_time = time.time()
    
    try:
        test_html_and_naming_spec()
        test_vue_setup_and_flow_methods()
        test_full_cross_workspace_lifecycle()
        elapsed = time.time() - start_time
        log(f"🎉 全部前端与全链路 E2E 测试 100% 通过！耗时: {elapsed:.2f}s", "PASS")
    except AssertionError as e:
        log(f"❌ 测试断言失败: {e}", "FAIL")
        sys.exit(1)
    except Exception as e:
        log(f"❌ 测试运行发生未捕获异常: {e}", "FAIL")
        sys.exit(1)

if __name__ == "__main__":
    run_all()
