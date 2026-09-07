#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电商 AI 创作工作台 - 核心 AI 统一客户端 (ai_client.py)
整合 Agnes AI 核心模型矩阵：
- 文本与全案意图调度: agnes-2.5-flash
- 视觉多模态素材解析: agnes-2.5-flash (Vision)
- 商业海报与主图生图: agnes-image-2.5-flash
- 云端视频生成(预留): agnes-video-2.5-flash
- 本地极限词排查与带货 FABE 话术编排
"""

import os
import sys
import json
import time
import re
import urllib.request
import urllib.error
from urllib.parse import urlparse

# 自动载入本地 .env 配置
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    try:
        with open(_env_path, 'r', encoding='utf-8') as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _v = _line.split('=', 1)
                    _k, _v = _k.strip(), _v.strip().strip("'").strip('"')
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass

DEFAULT_API_KEY = os.environ.get("AGNES_API_KEY", "")
DEFAULT_BASE_URL = os.environ.get("AGNES_BASE_URL", "https://api.agnes-ai.cn/v1").rstrip("/")

# 电商合规极限词库 (新广告法高频违规词与绝对化用语)
LIMIT_WORDS = [
    "第一", "顶级", "极品", "绝无仅有", "史无前例", "独家", "全网首发", "全网第一",
    "国家级", "世界级", "永久", "万能", "百分之百", "100%", "包过", "包治",
    "根治", "药到病除", "特效", "最强", "最好", "最优", "最先进", "无敌",
    "首选", "领袖", "绝佳", "王者", "神效", "纯天然无害", "唯一", "冠级"
]

class AgnesStudioClient:
    def __init__(self, api_key=None, base_url=None):
        self.api_key = api_key or DEFAULT_API_KEY
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    def _request(self, endpoint, payload, timeout=30):
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                parsed_err = json.loads(err_body)
            except Exception:
                parsed_err = {"message": err_body}
            return e.code, parsed_err
        except Exception as e:
            return 500, {"error": str(e)}

    def check_compliance(self, text):
        """
        排查文本中的电商极限词，返回匹配到的敏感词与高亮处理文本
        """
        found = []
        for word in LIMIT_WORDS:
            if word in text:
                found.append(word)
        return list(set(found))

    def chat_completion(self, messages, system_prompt=None, temperature=0.7, max_tokens=8192, timeout=75):
        """
        调用 Agnes 2.5 Flash 文本大模型，自带重试机制与 8192 超长 Token 输出支持
        """
        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        payload = {
            "model": "agnes-2.5-flash",
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        # 支持 1 次快速重试
        last_error = None
        for attempt in range(2):
            status, data = self._request("chat/completions", payload, timeout=timeout)
            if status == 200 and "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"]["content"]
                return {"success": True, "content": content, "raw": data}
            last_error = data
            time.sleep(0.5)

        return {"success": False, "status": 500, "error": last_error}

    def chat_completion_stream(self, messages, system_prompt=None, temperature=0.7, max_tokens=8192, timeout=75):
        """
        流式调用 Agnes 2.5 Flash 文本大模型，实时 yield (delta_text, is_done)
        """
        import requests
        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        payload = {
            "model": "agnes-2.5-flash",
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=(10, timeout))
            if resp.status_code != 200:
                yield f"[API 响应错误码 {resp.status_code}]", True
                return

            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8").strip()
                if line_str == "data: [DONE]":
                    break
                if line_str.startswith("data: "):
                    try:
                        chunk = json.loads(line_str[6:])
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {}).get("content", "")
                            if delta:
                                yield delta, False
                    except Exception:
                        pass
            yield "", True
        except Exception as e:
            yield f"[流式通信中断: {str(e)}]", True

    def generate_full_campaign(self, user_prompt):
        """
        FirstPage 专属全案解析：输入一句话，同时生成带货脚本、分镜海报提示词、运镜类型与花字
        """
        system = """你是一名资深电商新媒体带货内容总监。
用户会给你一句话描述（如商品、卖点或创作诉求）。
请严格按照以下 JSON 格式输出，不要输出任何多余的解释或 markdown 标记外的文字：
{
  "script": {
    "title": "爆款口播标题",
    "hook": "黄金3秒抓人开头",
    "fabe": {
      "feature": "产品特性",
      "advantage": "核心优势",
      "benefit": "带给用户的直接利益",
      "evidence": "背书与实证数据"
    },
    "call_to_action": "促单行动号召"
  },
  "poster": {
    "style": "国潮奢华 / 极简科技 / 大牌质感 / 日系清透 / 赛博潮流",
    "prompt": "一段适合商业海报生成的详细英文提示词，强调商品主体、构图、材质、冷暖光影与4k棚拍质感",
    "title_caption": "海报上的主标题文案(4-8字)",
    "sub_caption": "海报上的利益点副标(8-16字)"
  },
  "motion": {
    "type": "dolly_in 或 pan_left 或 pan_right 或 dynamic_float",
    "title": "短视频花字主标题(4-8字)",
    "subtitle": "短视频利益点字幕(8-16字)",
    "tags": ["核心卖点1", "核心卖点2", "核心卖点3"]
  }
}
"""
        res = self.chat_completion([{"role": "user", "content": user_prompt}], system_prompt=system, temperature=0.6, max_tokens=4096, timeout=60)
        
        # 降级方案模版函数
        def make_fallback(prompt_text):
            p_clean = prompt_text.replace("商品名称：", "").replace("核心卖点：", "").replace("目标人群：", "，").strip()
            name_part = p_clean.split("，")[0][:15] if "，" in p_clean else p_clean[:15]
            return {
                "script": {
                    "title": f"热卖爆款 · {name_part}",
                    "hook": f"为什么懂行的人都在用这款{name_part}？真正的好物一上手就能感受到差距！",
                    "fabe": {
                        "feature": f"精湛工艺打造，匠心甄选材质，核心卖点契合{p_clean[:30]}",
                        "advantage": "质感突出，兼顾高效实用与商业美学，拒绝同质化",
                        "benefit": "大幅提升生活品质与日常体验，随心使用省时省心",
                        "evidence": "多轮实测口碑好评，主播私享自用款强烈力荐"
                    },
                    "call_to_action": "趁现在现货速发，戳右下角立即锁定限时尝鲜优惠！"
                },
                "poster": {
                    "style": "大牌质感",
                    "prompt": f"Commercial product photography of {name_part}, resting on dark volcanic basalt rock, dramatic cool rim lighting, master studio setup, 8k resolution, ultra-realistic",
                    "title_caption": name_part,
                    "sub_caption": "匠心甄选 · 高阶质感"
                },
                "motion": {
                    "type": "dolly_in",
                    "title": name_part,
                    "subtitle": "高阶质感 · 限时首发",
                    "tags": ["现货速发", "高质感好物", "达人推荐"]
                },
                "risk_words": []
            }

        if not res["success"]:
            fallback = make_fallback(user_prompt)
            return {"success": True, "campaign": fallback, "warning": "API超时已启用智能降级全案"}

        # 尝试剥离 markdown 或用正则提取最外层 JSON
        content = res.get("content", "").strip()
        json_str = content
        if "```" in json_str:
            match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', json_str)
            if match:
                json_str = match.group(1).strip()
        
        # 提取 { ... }
        brace_match = re.search(r'\{[\s\S]*\}', json_str)
        if brace_match:
            json_str = brace_match.group(0).strip()

        try:
            parsed = json.loads(json_str)
            # 合规性检测
            full_text = json.dumps(parsed, ensure_ascii=False)
            risk_words = self.check_compliance(full_text)
            parsed["risk_words"] = risk_words
            return {"success": True, "campaign": parsed}
        except Exception as e:
            fallback = make_fallback(user_prompt)
            return {"success": True, "campaign": fallback, "warning": f"JSON解析降级: {str(e)}", "raw_content": content}

    def generate_image(self, prompt, style="大牌质感", aspect_ratio="1:1", output_dir="uploads"):
        """
        调用 Agnes Image 2.5 Flash 生成商业摄影级海报
        :param prompt: 主体及摄影提示词
        :param style: 风格预设（大牌质感/水凝轻奢/暗夜曜石/极客几何等）
        :param aspect_ratio: 画幅比例 ('1:1', '3:4', '9:16', '16:9')
        :param output_dir: 本地落盘目录
        """
        style_modifiers = {
            "大牌质感": "luxury brand commercial photography, sleek minimalist studio lighting, high-end cosmetics aesthetic, 8k resolution, sharp focus",
            "水凝轻奢": "dewy aesthetic, crystal clear water ripples, gentle caustic light reflections, morning softbox, luxury skincare editorial, 8k",
            "暗夜曜石": "moody dark studio, black volcanic basalt rock, dramatic cool blue rim lighting, deep contact shadows, luxury masculine, 8k",
            "极客几何": "futuristic tech minimalism, microcement geometric pedestal, dual diffused softbox lighting, clean studio render, 8k",
            "璀璨星芒": "macro close-up photography, high reflection black mirror surface, overhead spotlight with prismatic diamond glints, luxury catalog, 8k",
            "晨露鲜萃": "bright morning sunlight backlight, fresh cold condensation droplets, natural light oak wood tabletop, organic fresh beverage commercial, 8k",
            "暖阳日杂": "warm morning sunbeam through sheer curtains, linen cloth texture, cozy Nordic interior atmosphere, Kinfolk editorial, 8k",
            "国潮奢华": "modern Chinese oriental chic, elegant gold and jade accents, traditional aesthetics with contemporary luxury, dramatic volumetric lighting",
            "极简科技": "clean futuristic tech minimalism, clean geometric shadows, matte texture, soft cool rim light, premium product showcase",
            "清新日常": "bright natural morning sunlight, warm cozy lifestyle aesthetic, soft pastel tones, clean desk setting",
            "赛博潮流": "vibrant neon cyberpunk lighting, dark reflective glossy floor, dynamic contrast, high-tech streetwear aesthetic"
        }
        modifier = style_modifiers.get(style, style_modifiers["大牌质感"])
        
        # 如果 prompt 自身已经包含了高定摄影细节，保证不突兀
        if any(term in prompt.lower() for term in ['8k', 'commercial', 'studio lighting', 'photography']):
            full_prompt = prompt
        else:
            full_prompt = f"{prompt}, {modifier}"

        # 尺寸映射
        size_map = {
            "1:1": "1024x1024",
            "3:4": "768x1024",
            "9:16": "1024x1792",
            "16:9": "1792x1024"
        }
        image_size = size_map.get(aspect_ratio, "1024x1024")

        payload = {
            "model": "agnes-image-2.5-flash",
            "prompt": full_prompt,
            "n": 1,
            "size": image_size
        }

        status, data = self._request("images/generations", payload, timeout=35)
        if status == 200 and "data" in data and len(data["data"]) > 0:
            remote_url = data["data"][0].get("url", "")
            # 下载到本地保存备份
            local_filename = f"agnes_poster_{int(time.time())}.png"
            os.makedirs(output_dir, exist_ok=True)
            local_path = os.path.join(output_dir, local_filename)

            try:
                req = urllib.request.Request(remote_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r, open(local_path, "wb") as f:
                    f.write(r.read())
            except Exception as e:
                pass

            return {
                "success": True,
                "remote_url": remote_url,
                "local_url": f"/uploads/{local_filename}" if os.path.exists(local_path) else remote_url,
                "local_path": local_path if os.path.exists(local_path) else None,
                "aspect_ratio": aspect_ratio,
                "size": image_size
            }
        else:
            return {"success": False, "status": status, "error": data}

if __name__ == "__main__":
    client = AgnesStudioClient()
    print("Agnes studio client initialized.")
