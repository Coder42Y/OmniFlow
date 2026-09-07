#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OmniFlow 全模态协同工作台端到端自动化测试
覆盖：
1. OmniFlow 品牌契约与 𝓥 流线 Logo：
   - HTML Title: 'OmniFlow'
   - 流线 Logo: '𝓥'
   - 导航主入口: 'Omni Hub' (FirstPage 兼容)
2. Codex 风格极客极简滚动条契约：
   - 5px 极细半透明滚动条 (::-webkit-scrollbar)
   - 胶囊全圆角 (9999px)
   - 暗色面板极客微隐滚动条规范
3. 会话历史 (Session History) 与新增对话 (New Chat) 架构：
   - 可折叠会话抽屉 (isSessionPanelOpen)
   - 新建对话 (createNewSession)
   - 会话无缝切换 (switchSession)
   - 会话管理 (deleteSession, renameSession, clearAllSessions)
   - 时间分组渲染 (groupedSessions: today, previous7Days, older)
   - 本地持久化存储键 ('omniflow_chat_sessions_v1')
4. 协同指示器与无废弃命名：
   - 'Powered by Muti-Agent' 提示词存在
   - Context Window 1M Tokens 环形指示器存在
   - 绝无 '工坊'、'智囊'、'复盘卡点' 废弃命名
"""

import os
import sys
import json
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
    req = urllib.request.Request(url, headers={"User-Agent": "OmniFlowTester"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)

def test_1_omniflow_brand_and_logo():
    """测试 1: OmniFlow 品牌名称、𝓥 流线 Logo 与 Omni Hub 导航契约"""
    log("▶ 执行测试 1: 验证 OmniFlow 品牌、𝓥 流线 Logo 与 Omni Hub 导航...")
    code, html = http_get("/")
    assert code == 200, f"HTTP 请求失败: code {code}"

    # 1. Title 品牌检查
    assert "<title>OmniFlow" in html, "网页 <title> 必须以 OmniFlow 开头"
    log("✓ 已确认网页 Title 成功命名为 OmniFlow", "PASS")

    # 2. 𝓥 流线 Logo 检查
    assert "𝓥" in html, "侧栏 Logo 中必须包含 𝓥 极速流线标志"
    log("✓ 已确认品牌 Logo 采用 𝓥 极速流线徽标", "PASS")

    # 3. 导航 Omni Hub 与 FirstPage 兼容检查
    assert "Omni Hub" in html, "导航中必须包含 Omni Hub"
    assert "FirstPage" in html, "代码与导航中需兼容 FirstPage 标识"
    log("✓ 已确认 Omni Hub (FirstPage) 协同中心导航契约完备", "PASS")

def test_2_codex_style_scrollbar():
    """测试 2: Codex 极客极简滚动条 CSS 契约"""
    log("▶ 执行测试 2: 验证 Codex 风格极客极简滚动条 CSS 契约...")
    code, html = http_get("/")
    assert code == 200

    # 提取 <style> 内容
    style_matches = re.findall(r'<style>(.*?)</style>', html, re.DOTALL)
    assert style_matches, "HTML 中未找到 <style> 样式标签"
    style_content = "\n".join(style_matches)

    # 1. 5px 细滚动条
    assert "::-webkit-scrollbar" in style_content, "缺少 ::-webkit-scrollbar 定义"
    assert "width: 5px" in style_content or "height: 5px" in style_content, "Codex 滚动条必须设置为 5px 极细规范"
    log("✓ 已确认滚动条宽度定义为 5px 极客细尺寸", "PASS")

    # 2. 胶囊全圆角与半透明
    assert "border-radius: 9999px" in style_content, "滚动条滑块必须具有 9999px 胶囊全圆角"
    assert "rgba(0, 0, 0" in style_content, "滚动条滑块需采用微妙半透明配色"
    log("✓ 已确认滚动条滑块具备全圆角与微隐半透明规范", "PASS")

    # 3. Codex 暗色面板滚动条
    assert "codex-dark-scrollbar" in style_content or "rgba(255, 255, 255" in style_content, "需支持暗色模式/面板滚动条"
    log("✓ 已确认 Codex 暗色面板滚动条规范已就绪", "PASS")

def test_3_session_history_and_new_chat():
    """测试 3: 会话历史 (Session History) 与新增对话 (New Chat) 闭环"""
    log("▶ 执行测试 3: 验证会话历史与新增对话前端逻辑与模板契约...")
    code, html = http_get("/")
    assert code == 200

    # 1. 模板抽屉与操作按钮
    assert "新建对话" in html, "会话侧栏必须具备 '新建对话' 按钮"
    assert "isSessionPanelOpen" in html, "必须具备会话抽屉折叠展开控制状态"
    assert "groupedSessions.today" in html, "必须支持按今天分组历史会话"
    assert "switchSession" in html, "必须提供切换会话方法"
    assert "deleteSession" in html, "必须提供删除会话方法"
    assert "renameSession" in html, "必须提供重命名会话方法"
    assert "clearAllSessions" in html, "必须提供一键清空全部历史会话方法"
    log("✓ 已确认会话抽屉、时间轴分组、新建/切换/删除/重命名交互模板 100% 具备", "PASS")

    # 2. Vue setup 逻辑与持久化
    script_matches = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
    js_code = script_matches[-1] if script_matches else ''
    assert "omniflow_chat_sessions_v1" in js_code, "必须使用 'omniflow_chat_sessions_v1' 本地持久化键"
    assert "loadSessions" in js_code, "必须具备 loadSessions 加载历史函数"
    assert "saveSessions" in js_code, "必须具备 saveSessions 持久化函数"
    assert "createNewSession" in js_code, "必须具备 createNewSession 函数"
    assert "groupedSessions" in js_code, "必须具备 groupedSessions 计算属性"
    log("✓ 已确认 Vue setup 会话数据流与 localStorage 持久化机制完备", "PASS")

def test_4_multi_agent_indicators_and_no_stale_terms():
    """测试 4: Multi-Agent 提示词、Context Window 环形指示器与排查废弃词"""
    log("▶ 执行测试 4: 验证 Multi-Agent 提示词、Context Window 小圆环与禁止词...")
    code, html = http_get("/")
    assert code == 200

    # 1. 提示词与小圆环与思考态
    assert "Powered by Muti-Agent" in html, "输入栏提示词必须为 'Powered by Muti-Agent'"
    assert "Analyzing and Deep Thinking..." in html, "思考加载态文本必须为 'Analyzing and Deep Thinking...'"
    assert "contextRingProgress" in html, "Context Window 环形 SVG 进度条必须存在"
    assert "1,000,000 Tokens" in html, "1M 超大上下文 Tooltip 说明必须存在"
    log("✓ 已确认 'Powered by Muti-Agent'、'Analyzing and Deep Thinking...' 与 Context Window 环形指示器完好", "PASS")

    # 2. 绝对排查废除命名
    forbidden_words = ["工坊", "智囊", "复盘卡点"]
    found_forbidden = []
    for fw in forbidden_words:
        matches = re.findall(rf'>[^<]*{fw}[^<]*<', html)
        if matches:
            found_forbidden.append((fw, matches[:2]))
            
    assert not found_forbidden, f"前端依然存在陈旧废弃命名: {found_forbidden}"
    log("✓ 已确认页面彻底废除 '工坊'、'智囊'、'复盘卡点' 等历史陈旧命名", "PASS")

def main():
    print("=" * 65)
    log("🚀 开始执行 OmniFlow 品牌升级、会话历史与 Codex 滚动条端到端验证")
    print("=" * 65)
    
    test_1_omniflow_brand_and_logo()
    test_2_codex_style_scrollbar()
    test_3_session_history_and_new_chat()
    test_4_multi_agent_indicators_and_no_stale_terms()
    
    print("=" * 65)
    log("🎉 恭喜！OmniFlow 全模态协同工作台全部 4 大核心契约端到端自动化测试 100% 通过！", "PASS")
    print("=" * 65)

if __name__ == "__main__":
    main()
