"""只用于离线测试的官方 NDJSON 协议替身；不调用模型或网络。"""

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("--model")
parser.add_argument("--input-format")
parser.add_argument("--output-format")
parser.add_argument("--json-schema")
parser.add_argument("--sandbox", action="store_true")
parser.add_argument("--conversation")
args = parser.parse_args()
cid = args.conversation or str(uuid4())
scenario = json.loads(os.environ.get("SYNTHETIC_SCENARIO", "{}"))


def emit(event, value):
    print(json.dumps({"event": event, event: value}), flush=True)


print(
    json.dumps(
        {
            "event": "init",
            "conversation_id": cid,
            "init": {
                "cwd": str(Path.cwd()),
                "model": scenario.get("model", args.model),
                "permission_mode": scenario.get("permission", "request-review"),
                "tools": [],
                "json_schema": json.loads(args.json_schema) if args.json_schema else None,
            },
        }
    ),
    flush=True,
)
turn = 0


def interrupt(signum, frame):
    emit("result", {"conversation_id": cid, "status": "INTERRUPTED", "response": ""})
    raise SystemExit(0)


signal.signal(signal.SIGINT, interrupt)
for line in sys.stdin:
    request = json.loads(line)
    assert request["event"] == "user" and set(request) == {"event", "message"}
    with Path("received.jsonl").open("a") as stream:
        stream.write(line)
    turn += 1
    if scenario.get("eof"):
        raise SystemExit(0)
    if scenario.get("oversize"):
        print("x" * 300000, flush=True)
        continue
    if scenario.get("hold"):
        signal.pause()
    content = json.loads(request["message"]["content"])["user_message"]
    answer = "合成回复：" + content
    actions = scenario.get("actions", [])
    if scenario.get("e2e"):
        # 只供真实 API 浏览器联调；规则可预测，不冒充模型理解或真实媒体。
        if content.startswith("请生成一张图片："):
            actions = [
                {
                    "name": "submit_image",
                    "arguments": {
                        "prompt": "合成蓝色方块",
                        "aspect_ratio": "9:16",
                        "size_tier": "1K",
                    },
                }
            ]
        elif content.startswith("请用已确认图片生成视频："):
            actions = [
                {
                    "name": "submit_video",
                    "arguments": {
                        "mode": "keyframe",
                        "prompt": "合成轻轻移动",
                        "aspect_ratio": "9:16",
                        "seconds": 4,
                        "size_tier": "720P",
                    },
                }
            ]
    structured = {"text": answer, "actions": actions}
    # 官方 --json-schema 协议：response 是 structured_output 的序列化，
    # agent_response 增量也可能是包含工具参数的 JSON，不能直接展示。
    response = json.dumps(structured, ensure_ascii=False)
    emit(
        "step_update",
        {
            "conversation_id": cid,
            "step_type": "tool",
            "state": "DONE",
            "tool_info": {"name": "run_command", "output": "SYNTHETIC_SECRET_PATH"},
        },
    )
    emit(
        "step_update",
        {
            "conversation_id": cid,
            "step_type": "agent_response",
            "state": "ACTIVE",
            "text_delta": response[:3],
        },
    )
    emit(
        "step_update",
        {
            "conversation_id": cid,
            "step_type": "agent_response",
            "state": "DONE",
            "text_delta": response[3:],
        },
    )
    if scenario.get("eof_after_deltas"):
        raise SystemExit(0)
    result = {
        "conversation_id": scenario.get("result_cid", cid),
        "status": scenario.get("status", "SUCCESS"),
        "response": response,
        "structured_output": structured,
        "json_schema": json.loads(args.json_schema) if args.json_schema else None,
        "num_turns": turn,
        "duration_seconds": turn * 0.5,
        "usage": {"input_tokens": turn * 10, "output_tokens": turn * 5},
    }
    emit("result", result)
    if scenario.get("duplicate_result"):
        emit("result", result)
