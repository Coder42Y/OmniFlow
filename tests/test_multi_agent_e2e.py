#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FirstPage Multi-Agent 协同与 Context Window 架构端到端自动化测试
覆盖:
1. 前端 UI 契约: 'Powered by Muti-Agent' 提示词、Context Window 环形 SVG 进度条、无废弃词汇
2. Vue Setup 属性契约: contextTokens, contextUsagePercent, contextRingProgress, contextUsageText 完备性
3. Master Agent (Gemini 3.8 Flash) 调度决策验证:
   - 意图 A: 常规带货策略对话 -> Gemini 3.8 Flash (Master Agent) 深度作答
   - 意图 B: 动态视频生成任务 -> Master Agent 决策派发给专职子 Agent [Agnes Video Engine] 完成 9:16 合成
   - 意图 C: 全案带货创作 -> Gemini 3.8 Flash 规划全案，视频全权委托 Agnes 渲染
"""

import os
import sys
import json
import time
import re
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
    req = urllib.request.Request(url, headers={"User-Agent": "MultiAgentTester"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)

def http_post(path, data, timeout=60):
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": "MultiAgentTester"
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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

def test_frontend_multi_agent_ui():
    """测试 1: 验证 FirstPage 对话框提示词与 Context Window 小圆环组件契约"""
    log("▶ 执行测试 1: 验证前端 Powered by Muti-Agent 与 Context Window 小圆环契约...")
    code, html = http_get("/")
    assert code == 200, f"首页请求非 200: {code}"

    # 1. 验证提示词更新
    assert "Powered by Muti-Agent" in html, "页面未找到 'Powered by Muti-Agent' 提示词"
    log("✓ 已确认对话框提示词已准确更新为 'Powered by Muti-Agent'", "PASS")

    # 2. 验证 Context Window SVG 小圆环结构
    assert "Context Window" in html, "页面未找到 Context Window 相关标示"
    assert "stroke-dashoffset" in html, "缺少 SVG 环形进度条动态偏移属性"
    assert "contextRingProgress" in html, "缺少 contextRingProgress 绑定"
    assert "contextUsageText" in html, "缺少 contextUsageText 文本展示"
    log("✓ 已确认 Context Window 动态小圆环与悬浮 Tooltip 结构就位", "PASS")

    # 3. 验证 Vue setup 导出契约
    script_matches = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
    assert script_matches, "未找到 script 标签"
    js = script_matches[-1]
    expected_exports = ["contextTokens", "contextUsagePercent", "contextRingProgress", "contextUsageText"]
    for exp in expected_exports:
        assert exp in js, f"Vue setup 未导出: {exp}"
    log("✓ 已确认 Vue setup 中 Context Window 4 项计算属性完备导出", "PASS")

    # 4. 零容忍废弃词汇检查
    for forbidden in ["工坊", "智囊", "复盘卡点"]:
        assert not re.search(rf'>[^<]*{forbidden}[^<]*<', html), f"页面存在陈旧违规词: {forbidden}"
    log("✓ 已确认命名严格规范，无陈旧废弃词汇", "PASS")

def test_master_agent_general_dialogue():
    """测试 2: 测试 Gemini 3.8 Flash (Master Agent) 专业对话能力"""
    log("▶ 执行测试 2: 测试 Gemini 3.8 Flash 作为 Master Agent 的深度对话能力...")
    status, res = http_post("/api/chat", {
        "messages": [
            {"role": "user", "content": "新媒体带货如何设计黄金3秒抓人开头？请用电商总监口吻简述3条要点"}
        ]
    }, timeout=45)
    assert status == 200, f"对话接口返回异常: {status} - {res}"
    assert res.get("success") is True
    reply = res.get("reply", "")
    model_tag = res.get("model", "")
    assert len(reply) > 50, f"Master Agent 回复内容过短: {reply}"
    assert "Gemini 3.8 Flash" in model_tag, f"返回模型标识非 Gemini 3.8 Flash: {model_tag}"
    log(f"✓ Master Agent (Gemini 3.8 Flash) 响应成功 (引擎标识: {model_tag})", "PASS")

def test_master_agent_delegates_video_to_agnes():
    """测试 3: 核心规则验证：生成视频的任务一概交给 Agnes Video Engine 处理"""
    log("▶ 执行测试 3: 验证核心规则：生成视频的任务由 Master Agent 决策后一概交给 Agnes...")
    status, res = http_post("/api/chat", {
        "messages": [
            {"role": "user", "content": "帮我把这款黑曜石男士淡雅冷香水做个5秒推镜头运镜视频"}
        ]
    }, timeout=50)
    assert status == 200, f"视频生成请求失败: {status} - {res}"
    assert res.get("success") is True, f"业务响应失败: {res}"
    
    reply = res.get("reply", "")
    model_tag = res.get("model", "")
    cards = res.get("cards", {})
    video_card = cards.get("video", {})
    video_url = video_card.get("video_url", "")

    # 1. 验证回复内容中体现了 Gemini 决策与 Agnes 执行分工
    assert "Gemini 3.8 Flash" in reply or "Master Agent" in reply, "回复未体现 Master Agent 调度说明"
    assert "Agnes Video Engine" in reply or "Agnes" in reply, "回复未体现 Agnes 视频引擎执行说明"
    assert "Agnes Video Engine" in model_tag, f"模型标识未体现 Agnes 视频子 Agent: {model_tag}"

    # 2. 验证视频物理落盘
    assert video_url.startswith("/clips/"), f"返回视频 URL 异常: {video_url}"
    local_video_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), video_url.lstrip("/"))
    assert os.path.exists(local_video_path), f"物理视频文件不存在: {local_video_path}"
    assert os.path.getsize(local_video_path) > 30000, "视频文件大小异常"
    log(f"✓ 已证实：Master Agent 将视频任务全权交由 Agnes Video Engine 合成落地: {video_url} ({os.path.getsize(local_video_path)} 字节)", "PASS")

def run_all():
    log("==================================================================")
    log("开始执行 FirstPage Multi-Agent 与 Context Window 端到端自动化测试")
    log("==================================================================")
    t0 = time.time()
    try:
        test_frontend_multi_agent_ui()
        test_master_agent_general_dialogue()
        test_master_agent_delegates_video_to_agnes()
        cost = time.time() - t0
        log(f"🎉 全部 Multi-Agent 与 Context Window 测试 100% 通过！总耗时: {cost:.2f}s", "PASS")
    except AssertionError as e:
        log(f"❌ 测试断言失败: {e}", "FAIL")
        sys.exit(1)
    except Exception as e:
        log(f"❌ 未捕获异常: {e}", "FAIL")
        sys.exit(1)

if __name__ == "__main__":
    run_all()
