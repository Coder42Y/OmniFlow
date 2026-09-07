#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉海报工作区全新预设与5层工业级摄影装配体系端到端自动化测试套件
测试目标:
1. 前端契约校验: 6大电商爆款预设、4维拍摄调配器(底座/光效/机位/画幅)与实时编译提示词
2. Vue Setup 属性契约校验: 状态变量、维度选项、装配函数、复制函数完备性
3. 纯洁性排查: 杜绝陈旧废弃词汇 (智囊/工坊/复盘卡点)
4. 真实渲染接口穿透: 分别测试 1:1 方图与 3:4 种草竖图渲染，验证本地物理落盘与尺寸
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
    req = urllib.request.Request(url, headers={"User-Agent": "VisualPresetTester"})
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
        "User-Agent": "VisualPresetTester"
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
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

def test_frontend_visual_presets_contract():
    """测试 1: 验证前端页面中 6 大电商行业预设与 4 维调配器契约"""
    log("▶ 执行测试 1: 验证视觉海报工作区 6 大行业预设与摄影调配面板契约...")
    code, html = http_get("/")
    assert code == 200, f"首页响应失败: {code}"

    # 1. 验证 6 大行业爆款预设卡片相关内容
    industry_keywords = [
        "水凝轻奢",
        "暗夜曜石",
        "极客几何",
        "璀璨星芒",
        "晨露鲜萃",
        "暖阳日杂"
    ]
    for kw in industry_keywords:
        assert kw in html, f"页面缺少行业爆款预设: {kw}"
    log("✓ 已确认 6 大行业爆款摄影预设卡片完整定义", "PASS")

    # 2. 验证 4 大摄影维度在界面上的展示
    dimensions = [
        "展台底座 (Podium & Surface)",
        "影棚布光 (Studio Lighting)",
        "镜头机位 (Camera Framing)",
        "画幅比例 (Aspect Ratio)",
        "装配好的商业级提示词 (Compiled Prompt)"
    ]
    for dim in dimensions:
        assert dim in html, f"页面缺少摄影调配维度组件: {dim}"
    log("✓ 已确认 4 大商业摄影维度与 Compiled Prompt 实时预览面板就位", "PASS")

    # 3. 验证陈旧词汇绝对零容忍
    forbidden = ["工坊", "智囊", "复盘卡点"]
    for fb in forbidden:
        assert not re.search(rf'>[^<]*{fb}[^<]*<', html), f"发现废弃词汇: {fb}"
    log("✓ 已确认命名严格规范，无任何废弃词汇", "PASS")

def test_vue_setup_visual_architecture():
    """测试 2: 验证 Vue setup 中的响应式状态与装配编译函数"""
    log("▶ 执行测试 2: 验证 Vue setup 响应式摄影装配引擎与导出契约...")
    code, html = http_get("/")
    assert code == 200

    script_matches = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
    assert script_matches, "未找到 script 标签"
    js = script_matches[-1]

    expected_symbols = [
        "visualIndustryPresets",
        "selectIndustryPreset",
        "podiumOptions",
        "visualPodium",
        "lightingOptions",
        "visualLighting",
        "framingOptions",
        "visualFraming",
        "ratioOptions",
        "visualRatio",
        "compilePrompt",
        "copyPrompt",
        "generatePoster"
    ]
    for sym in expected_symbols:
        assert sym in js, f"Vue setup 缺少摄影装配符号: {sym}"
    log("✓ 已确认 Vue 摄影装配引擎核心状态与函数导出完备", "PASS")

def test_multiratio_image_generation():
    """测试 3: 真实测试多画幅比例 (3:4 种草竖图 与 1:1 电商方图) 的商拍海报渲染与落盘"""
    log("▶ 执行测试 3: 真实穿透 Agnes Image 2.5 Flash 渲染多画幅商拍海报...")

    # 场景 A: 3:4 小红书美妆水凝轻奢海报
    prompt_skincare = "Commercial product photography of a luxury hydrating essence bottle with silver pump, clear acrylic podium over water ripples, morning sunbeam caustics, hero 3/4 angle, master studio lighting, 8k resolution"
    log("1. 测试 3:4 比例 (小红书/美妆爆款) 海报渲染...")
    status_a, res_a = http_post("/api/generate-image", {
        "prompt": prompt_skincare,
        "style": "水凝轻奢",
        "aspect_ratio": "3:4",
        "provider": "agnes"
    })
    assert status_a == 200, f"3:4 海报渲染请求失败: {res_a}"
    assert res_a.get("success") is True, f"3:4 渲染响应错误: {res_a}"
    img_url_a = res_a.get("image_url", "")
    assert img_url_a.startswith("/uploads/"), f"返回的图片 URL 异常: {img_url_a}"
    
    # 物理落盘检查
    local_path_a = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), img_url_a.lstrip("/"))
    assert os.path.exists(local_path_a), f"本地未找到生成的 3:4 海报: {local_path_a}"
    size_bytes_a = os.path.getsize(local_path_a)
    assert size_bytes_a > 30000, f"生成的图片文件过小: {size_bytes_a} bytes"
    log(f"   ✓ 3:4 美妆海报渲染并落盘成功: {img_url_a} ({size_bytes_a} 字节, 尺寸: {res_a.get('size')})", "PASS")

    # 场景 B: 1:1 电商主图暗夜曜石男香
    prompt_perfume = "Commercial product photography of an obsidian luxury men perfume bottle, rough dark basalt rock, dramatic cool blue rim lighting, hero three-quarter, master studio lighting, 8k"
    log("2. 测试 1:1 比例 (电商标准方图) 海报渲染...")
    status_b, res_b = http_post("/api/generate-image", {
        "prompt": prompt_perfume,
        "style": "暗夜曜石",
        "aspect_ratio": "1:1",
        "provider": "agnes"
    })
    assert status_b == 200, f"1:1 海报渲染请求失败: {res_b}"
    assert res_b.get("success") is True, f"1:1 渲染响应错误: {res_b}"
    img_url_b = res_b.get("image_url", "")
    assert img_url_b.startswith("/uploads/"), f"返回的图片 URL 异常: {img_url_b}"
    
    local_path_b = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), img_url_b.lstrip("/"))
    assert os.path.exists(local_path_b), f"本地未找到生成的 1:1 海报: {local_path_b}"
    size_bytes_b = os.path.getsize(local_path_b)
    assert size_bytes_b > 30000, f"生成的图片文件过小: {size_bytes_b} bytes"
    log(f"   ✓ 1:1 男香海报渲染并落盘成功: {img_url_b} ({size_bytes_b} 字节, 尺寸: {res_b.get('size')})", "PASS")

def run_all():
    log("==================================================================")
    log("开始执行视觉海报工作区 5层装配与全新预设体系端到端自动化测试")
    log("==================================================================")
    t0 = time.time()
    try:
        test_frontend_visual_presets_contract()
        test_vue_setup_visual_architecture()
        test_multiratio_image_generation()
        cost = time.time() - t0
        log(f"🎉 视觉海报工作区全部专项测试 100% 通过！总耗时: {cost:.2f}s", "PASS")
    except AssertionError as e:
        log(f"❌ 测试断言失败: {e}", "FAIL")
        sys.exit(1)
    except Exception as e:
        log(f"❌ 运行发生未捕获异常: {e}", "FAIL")
        sys.exit(1)

if __name__ == "__main__":
    run_all()
