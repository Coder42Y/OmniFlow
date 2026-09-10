"""MCP 与结构化动作复用任务服务：权限/原消息证据/稳定幂等/数据范围。"""

import io
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from test_artifacts import confirmation, uploaded
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import accepted, create
from test_tasks import image_request
from test_tasks import media as media

from omniflow.problems import ProblemError
from omniflow.run_manager import RunManager
from omniflow.tool_gateway import RunScope, ToolGateway


def scope_for(actor, database, content="请生成一张图片：合成方块", **extra):
    cid = create(actor)
    accepted(actor, cid, content, **extra)
    manager = RunManager(database)
    job = manager.claim()
    assert job["conversation_id"] == cid
    return RunScope(job["owner_id"], cid, job["run_id"], manager.token)


def image_args():
    args = image_request(str(uuid4()))
    args.pop("conversation_id")
    args.pop("kind")
    return args


def test_rpc_only_minimal_tools_and_durable_idempotency(user, database, media):
    scope = scope_for(user, database)
    gateway = ToolGateway(database, media)
    frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    tools = gateway.rpc(scope, frame)["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "submit_image",
        "submit_video",
        "submit_local_motion",
        "read_task",
        "list_artifacts",
    }
    for tool in tools:
        assert tool["inputSchema"]["additionalProperties"] is False
        assert not {
            "owner_id",
            "conversation_id",
            "run_id",
            "model",
            "url",
            "shell",
            "confirmed",
        } & set(tool["inputSchema"]["properties"])
    request = {
        "jsonrpc": "2.0",
        "id": "stable-9",
        "method": "tools/call",
        "params": {"name": "submit_image", "arguments": image_args()},
    }
    first = gateway.rpc(scope, request)
    assert "error" not in first
    assert ToolGateway(database, media).rpc(scope, request) == first
    value = json.loads(first["result"]["content"][0]["text"])
    assert value["status"] == "queued" and value["run_id"] == scope.run_id
    altered = {
        **request,
        "params": {"name": "submit_image", "arguments": {**image_args(), "prompt": "不同"}},
    }
    assert gateway.rpc(scope, altered)["error"]["message"] == "IDEMPOTENCY_CONFLICT"
    assert gateway.rpc(scope, {**request, "id": "new-action"})["error"]["message"] == "FORBIDDEN"
    assert media.records() == []  # 不由 MCP 同步生成。


@pytest.mark.parametrize(
    "content",
    [
        "图片如何生成？",
        "请不要生成一张图片：蓝色",
        "例如请生成一张图片：蓝色",
        "如果请生成一张图片：蓝色",
        "“请生成一张图片：蓝色”",
        "视频",
        "请生成一张图片：蓝色？",
    ],
)
def test_consultation_and_ambiguous_intent_cannot_generate(user, database, media, content):
    scope = scope_for(user, database, content)
    with pytest.raises(ProblemError) as error:
        ToolGateway(database, media).call(scope, 1, "submit_image", image_args())
    assert error.value.code == "FORBIDDEN"


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "owner_id",
        "run_id",
        "conversation_id",
        "model",
        "base_url",
        "shell",
        "path",
        "confirmed",
        "action_id",
        "kind",
    ],
)
def test_model_cannot_choose_security_or_host_parameters(user, database, media, field):
    scope = scope_for(user, database)
    with pytest.raises(ProblemError) as error:
        ToolGateway(database, media).call(
            scope, 1, "submit_image", {**image_args(), field: "SYNTHETIC_SECRET"}
        )
    assert error.value.code == "VALIDATION_ERROR"
    assert "SYNTHETIC_SECRET" not in str(error.value)


def test_discuss_only_can_read_but_not_create_and_stopping_revokes_all_calls(user, database, media):
    scope = scope_for(user, database, generation_permission="discuss_only")
    gateway = ToolGateway(database, media)
    assert gateway.call(scope, 1, "list_artifacts", {}) == {"items": []}
    with pytest.raises(ProblemError) as error:
        gateway.call(scope, 2, "submit_image", image_args())
    assert error.value.code == "FORBIDDEN"
    with database.transaction() as connection:
        connection.execute("UPDATE runs SET status='stopping' WHERE id=?", (scope.run_id,))
    with pytest.raises(ProblemError) as error:
        gateway.call(scope, 1, "list_artifacts", {})
    assert error.value.code == "RUN_ALREADY_TERMINAL"


def test_old_library_other_user_and_future_queue_never_enter_tool_scope(
    user, admin, database, media
):
    old = uploaded(user)
    alien = uploaded(admin)
    scope = scope_for(user, database)
    future = uploaded(user)
    accepted(
        user, scope.conversation_id, "后排才引用", attachment_version_ids=[future["version"]["id"]]
    )
    gateway = ToolGateway(database, media)
    assert gateway.call(scope, 1, "list_artifacts", {}) == {"items": []}
    for item in (old, alien, future):
        with pytest.raises(ProblemError) as error:
            gateway.call(
                scope,
                2,
                "submit_image",
                {**image_args(), "reference_version_ids": [item["version"]["id"]]},
            )
        assert error.value.code == "RESOURCE_NOT_FOUND"
    with pytest.raises(ProblemError):
        gateway.call(replace(scope, owner_id=str(uuid4())), 1, "list_artifacts", {})
    with pytest.raises(ProblemError):
        gateway.call(replace(scope, manager_token=str(uuid4())), 1, "list_artifacts", {})


def test_explicit_old_version_reference_and_revoke(user, database, media):
    old = uploaded(user)
    scope = scope_for(user, database, attachment_version_ids=[old["version"]["id"]])
    gateway = ToolGateway(database, media)
    result = gateway.call(scope, 1, "list_artifacts", {})
    assert [v["id"] for v in result["items"]] == [old["version"]["id"]]
    assert "content_url" not in str(result)
    with database.transaction() as connection:
        connection.execute("UPDATE users SET auth_epoch=auth_epoch+1 WHERE id=?", (scope.owner_id,))
    with pytest.raises(ProblemError) as error:
        gateway.call(scope, 1, "list_artifacts", {})
    assert error.value.code == "ACCOUNT_DISABLED"


def test_video_requires_exact_user_confirmation_not_model_claim(user, database, media):
    cid = create(user)
    image = uploaded(user, cid=cid)
    vid = image["version"]["id"]
    confirmed = confirmation(user, cid, vid).json()["id"]
    accepted(
        user,
        cid,
        "请用已确认图片生成视频：轻轻移动",
        selected_version_id=vid,
        reference_confirmation_id=confirmed,
    )
    manager = RunManager(database)
    job = manager.claim()
    scope = RunScope(job["owner_id"], cid, job["run_id"], manager.token)
    gateway = ToolGateway(database, media)
    args = {
        "prompt": "轻轻移动",
        "seconds": 4,
        "size_tier": "720P",
        "aspect_ratio": "9:16",
        "mode": "keyframe",
        "reference_confirmation_id": confirmed,
    }
    with pytest.raises(ProblemError) as error:
        gateway.call(scope, 1, "submit_video", {**args, "reference_confirmation_id": str(uuid4())})
    assert error.value.code == "REFERENCE_CONFIRMATION_REQUIRED"
    text = {k: v for k, v in args.items() if k != "reference_confirmation_id"}
    with pytest.raises(ProblemError) as error:
        gateway.call(scope, 1, "submit_video", {**text, "mode": "text"})
    assert error.value.code == "FORBIDDEN"
    assert (
        gateway.call(scope, 1, "submit_video", args)["requested_parameters"][
            "reference_confirmation_id"
        ]
        == confirmed
    )


def test_explicit_direct_text_video_is_allowed(user, database, media):
    scope = scope_for(user, database, "请直接文生视频：蓝色海洋")
    task = ToolGateway(database, media).call(
        scope,
        1,
        "submit_video",
        {
            "mode": "text",
            "prompt": "蓝色海洋",
            "seconds": 4,
            "size_tier": "720P",
            "aspect_ratio": "16:9",
        },
    )
    assert task["kind"] == "ai_video"


def test_mcp_stdio_is_real_jsonrpc_no_anonymous_http_no_secret_errors(user, database, media):
    scope = scope_for(user, database)
    gateway = ToolGateway(database, media)
    frames = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "shell", "arguments": {"secret": "SYNTHETIC_SECRET"}},
        },
    ]
    reader = io.BytesIO(b"".join((json.dumps(f) + "\n").encode() for f in frames))
    writer = io.BytesIO()
    gateway.serve_stdio(scope, reader, writer)
    responses = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert len(responses) == 3 and responses[0]["result"]["serverInfo"]["name"] == "omniflow"
    assert responses[-1]["error"]["message"] == "FORBIDDEN"
    assert b"SYNTHETIC_SECRET" not in writer.getvalue()
    with pytest.raises(ValueError):
        gateway.serve_stdio(scope, io.BytesIO(b"x" * 65537), io.BytesIO())
