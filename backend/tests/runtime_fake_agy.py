#!/usr/bin/python3
"""只进入测试构造的只读镜像；官方参数/协议替身，无真实生成或授权。"""

import json
import os
import socket
import sys
from pathlib import Path
from uuid import uuid4

args = sys.argv
schema = json.loads(args[args.index("--json-schema") + 1])
session_id = args[args.index("--conversation") + 1] if "--conversation" in args else str(uuid4())
state = Path.home() / (session_id + ".json")
history = json.loads(state.read_text()) if state.exists() else []
settings = json.loads((Path.home() / ".gemini/antigravity-cli/settings.json").read_text())
assert settings["useG1Credits"] is False
assert "read_file(*)" in settings["permissions"]["deny"]
assert settings["permissions"]["allow"] == []
assert (Path.home() / ".gemini/synthetic-auth.json").read_text() == "synthetic-google-only"
assert not Path("/home/host-secret-canary").exists()
assert "SYNTHETIC_HOST_SECRET" not in os.environ
s = socket.socket()
s.settimeout(0.2)
assert s.connect_ex(("127.0.0.1", 9)) != 0
s.close()
print(
    json.dumps(
        {
            "event": "init",
            "conversation_id": session_id,
            "init": {
                "model": args[args.index("--model") + 1],
                "permission_mode": "request-review",
                "cwd": os.getcwd(),
                "json_schema": schema,
            },
        }
    ),
    flush=True,
)
for line in sys.stdin:
    content = json.loads(json.loads(line)["message"]["content"])
    text = content["user_message"]
    if text == "bad-protocol":
        print('{"event":"unexpected"}', flush=True)
        continue
    if text.startswith("inspect:"):
        forbidden = Path(text.removeprefix("inspect:"))
        assert not forbidden.exists()
        assert len(list(Path("/proc").glob("[0-9]*"))) <= 4
        text = "隔离检查通过"
    if text == "proxy-check":
        from urllib.parse import urlsplit

        proxy = urlsplit(os.environ["HTTPS_PROXY"])
        with socket.create_connection((proxy.hostname, proxy.port), timeout=2) as stream:
            stream.sendall(b"CONNECT oauth2.googleapis.com:443 HTTP/1.1\r\n\r\n")
            response = b""
            while b"\r\n\r\n" not in response:
                response += stream.recv(1)
            assert response.startswith(b"HTTP/1.1 200")
            stream.sendall(b"SYNTHETIC-PING")
            assert stream.recv(64) == b"SYNTHETIC-PONG"
        text = "出口检查通过"
    history.append(text)
    state.write_text(json.dumps(history))
    actions = []
    if text.startswith("请生成一张图片："):
        actions = [
            {
                "name": "submit_image",
                "arguments": {
                    "prompt": "合成蓝色方块，无品牌",
                    "aspect_ratio": "1:1",
                    "size_tier": "1K",
                    "reference_version_ids": [],
                },
            }
        ]
    reply = {"text": " / ".join(history), "actions": actions}
    print(
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": session_id,
                    "status": "SUCCESS",
                    "num_turns": len(history),
                    "structured_output": reply,
                    "response": json.dumps(reply),
                    "json_schema": schema,
                },
            }
        ),
        flush=True,
    )
