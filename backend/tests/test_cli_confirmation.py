"""确认事实必须经真实对话/CLI/网关路径绑定，测试不替模型猜确认 ID。"""

import json
from uuid import uuid4

import pytest
from test_agy_adapter import provider
from test_artifacts import confirmation, delete, uploaded
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import problem
from test_auth import user as user
from test_conversations import accepted, create, send, snapshot
from test_tasks import finish, get, submit
from test_tasks import media as media

from omniflow.problems import ProblemError
from omniflow.run_manager import RunManager
from omniflow.task_models import VideoTaskCreate
from omniflow.tool_gateway import RunScope, ToolGateway

CONTENT = "请用已确认图片生成视频：轻轻移动"


def video_args():
    # 只有用户意图中的媒体参数，没有测试旁路注入的 reference_confirmation_id。
    return {
        "mode": "keyframe",
        "prompt": "轻轻移动",
        "seconds": 4,
        "size_tier": "720P",
        "aspect_ratio": "9:16",
    }


def confirmed_image(user, cid):
    image = uploaded(user, cid=cid)
    response = confirmation(user, cid, image["version"]["id"])
    assert response.status_code == 201, response.text
    return image, response.json()["id"]


def scope(database):
    manager = RunManager(database)
    job = manager.claim()
    assert job is not None
    return RunScope(job["owner_id"], job["conversation_id"], job["run_id"], manager.token)


@pytest.mark.parametrize("selected", [True, False])
def test_confirmed_video_full_cli_path_without_model_knowing_confirmation(
    user, admin, database, media, tmp_path, selected
):
    cid = create(user)
    image, proof = confirmed_image(user, cid)
    args = video_args()
    adapter, launcher = provider(
        tmp_path, {"actions": [{"name": "submit_video", "arguments": args}]}
    )
    run = accepted(
        user,
        cid,
        CONTENT,
        reference_confirmation_id=proof,
        **({"selected_version_id": image["version"]["id"]} if selected else {}),
    )["run"]
    manager = RunManager(database, adapter, tool_gateway=ToolGateway(database, media))
    try:
        assert manager.execute_next()
        # 捕获假进程实际 stdin，确认成功不依赖不可见标识或改造假进程偷偷补值。
        frames = [
            json.loads(line)
            for line in (launcher.opened[0][0].cwd / "received.jsonl").read_text().splitlines()
        ]
        assert len(frames) == 1
        assert proof not in json.dumps(frames)
        assert "reference_confirmation_id" not in args
        current = user[0].get(f"/api/v1/runs/{run['id']}").json()
        assert current["status"] == "completed", current
        assert len(current["task_ids"]) == 1
        tid = current["task_ids"][0]
        task = get(user, tid)
        assert task["status"] == "queued" and media.records() == []
        assert task["requested_parameters"]["reference_confirmation_id"] == proof
    finally:
        manager.close()
    # 关闭文字进程不取消任务；每次 worker 重建，完成后刷新仍能下载同一结果。
    task = finish(database, media, user, tid)
    assert task["status"] == "completed"
    assert len(media.records()) == 1
    inputs = json.loads(media.records()[0][2])
    assert inputs == [image["version"]["id"]]
    view = snapshot(user, cid)
    assistant = next(m for m in view["messages"] if m["id"] == current["assistant_message_id"])
    assert assistant["artifact_version_ids"] == task["output_version_ids"]
    assert assistant["content"] == "合成回复：" + CONTENT
    version = next(v for v in view["artifact_versions"] if v["id"] in task["output_version_ids"])
    assert version["execution_engine"] == "agnes-video-2.5-flash"
    assert version["duration_seconds"] == 1.25  # 合成文件实际值，不伪造请求的 4 秒。
    url = version["content_url"]
    downloaded = user[0].get(url)
    assert downloaded.status_code == 200 and downloaded.content
    assert user[0].head(url).status_code == 200
    assert user[0].get(url, headers={"Range": "bytes=0-7"}).content == downloaded.content[:8]
    problem(admin[0].get(url), 404, "RESOURCE_NOT_FOUND")
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM reference_confirmations").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_mcp_binds_omitted_confirmation_and_replay_checks_current_authority(user, database, media):
    cid = create(user)
    image, proof = confirmed_image(user, cid)
    accepted(user, cid, CONTENT, reference_confirmation_id=proof)
    authority = scope(database)
    gateway = ToolGateway(database, media)
    request = {
        "jsonrpc": "2.0",
        "id": "video-action-1",
        "method": "tools/call",
        "params": {"name": "submit_video", "arguments": video_args()},
    }
    tools = gateway.rpc(authority, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    spec = next(t for t in tools["result"]["tools"] if t["name"] == "submit_video")["inputSchema"]
    assert "reference_confirmation_id" not in spec["required"]
    assert "服务端绑定" in spec["properties"]["reference_confirmation_id"]["description"]
    first = gateway.rpc(authority, request)
    assert "error" not in first, first
    value = json.loads(first["result"]["content"][0]["text"])
    assert value["requested_parameters"]["reference_confirmation_id"] == proof
    assert ToolGateway(database, media).rpc(authority, request) == first
    # 旧客户端显式提供同一标识与服务端补齐是同一个语义动作。
    explicit = {
        **request,
        "params": {
            "name": "submit_video",
            "arguments": {
                **video_args(),
                "reference_confirmation_id": proof,
            },
        },
    }
    assert gateway.rpc(authority, explicit) == first
    assert "reference_confirmation_id" not in request["params"]["arguments"]
    with database.transaction() as connection:
        connection.execute("UPDATE runs SET status='stopping' WHERE id=?", (authority.run_id,))
    assert gateway.rpc(authority, request)["error"]["message"] == "RUN_ALREADY_TERMINAL"
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT version_id FROM task_artifact_uses WHERE role='input'"
            ).fetchone()[0]
            == image["version"]["id"]
        )
    assert media.records() == []


@pytest.mark.parametrize(
    "bad_proof",
    [
        None,
        "random",
        "same_version_new_proof",
        "other_version",
        "other_conversation",
        "other_owner",
    ],
)
def test_model_cannot_override_trusted_confirmation(user, admin, database, media, bad_proof):
    cid = create(user)
    image, proof = confirmed_image(user, cid)
    if bad_proof == "random":
        bad = str(uuid4())
    elif bad_proof == "same_version_new_proof":
        bad = confirmation(user, cid, image["version"]["id"]).json()["id"]
    elif bad_proof == "other_version":
        _, bad = confirmed_image(user, cid)
    elif bad_proof == "other_conversation":
        bad = confirmation(user, create(user), image["version"]["id"]).json()["id"]
    elif bad_proof == "other_owner":
        _, bad = confirmed_image(admin, create(admin))
    else:
        bad = None
    accepted(user, cid, CONTENT, reference_confirmation_id=proof)
    authority = scope(database)
    gateway = ToolGateway(database, media)
    with pytest.raises(ProblemError) as error:
        gateway.call(
            authority, 1, "submit_video", {**video_args(), "reference_confirmation_id": bad}
        )
    assert error.value.code == "REFERENCE_CONFIRMATION_REQUIRED"
    assert user[0].get("/api/v1/tasks").json()["items"] == []


@pytest.mark.parametrize("case", ["other_owner", "other_conversation", "other_version", "deleted"])
def test_message_api_rejects_invalid_confirmation_before_cli(user, admin, database, case):
    cid = create(user)
    image, proof = confirmed_image(user, cid)
    vid = image["version"]["id"]
    if case == "other_owner":
        _, proof = confirmed_image(admin, create(admin))
    elif case == "other_conversation":
        proof = confirmation(user, create(user), vid).json()["id"]
    elif case == "other_version":
        vid = uploaded(user, cid=cid)["version"]["id"]
    else:
        assert delete(user, image).status_code == 202
    response = send(user, cid, CONTENT, selected_version_id=vid, reference_confirmation_id=proof)
    problem(
        response,
        404 if case in ("other_owner", "deleted") else 409,
        "RESOURCE_NOT_FOUND"
        if case in ("other_owner", "deleted")
        else "REFERENCE_CONFIRMATION_REQUIRED",
    )
    with database.snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM cli_process_holds").fetchone()[0] == 0


@pytest.mark.parametrize("source", ["unbound", "future", "history"])
def test_full_cli_cannot_inherit_confirmation_from_another_message(
    user, database, media, tmp_path, source
):
    cid = create(user)
    image, proof = confirmed_image(user, cid)
    adapter, _ = provider(
        tmp_path, {"actions": [{"name": "submit_video", "arguments": video_args()}]}
    )
    manager = RunManager(database, adapter, tool_gateway=ToolGateway(database, media))
    try:
        if source == "history":
            earlier = accepted(user, cid, CONTENT, reference_confirmation_id=proof)["run"]
            assert manager.execute_next()
            assert user[0].get(f"/api/v1/runs/{earlier['id']}").json()["status"] == "completed"
        # 选中图片不等于本轮确认；即使旧轮或后排含有效确认也不能借用。
        run = accepted(user, cid, CONTENT, selected_version_id=image["version"]["id"])["run"]
        if source == "future":
            later = accepted(user, cid, CONTENT, reference_confirmation_id=proof)["run"]
        assert manager.execute_next()
        result = user[0].get(f"/api/v1/runs/{run['id']}").json()
        assert result["status"] == "failed" and result["task_ids"] == []
        assert result["error"]["code"] == "REFERENCE_CONFIRMATION_REQUIRED"
        if source == "future":
            assert user[0].get(f"/api/v1/runs/{later['id']}").json()["status"] == "queued"
    finally:
        manager.close()


@pytest.mark.parametrize("resume", [False, True])
def test_queued_confirmations_bind_each_exact_version_across_cli_turns(
    user, database, media, tmp_path, resume
):
    cid = create(user)
    images, proofs, runs = [], [], []
    for _ in range(2):
        image, proof = confirmed_image(user, cid)
        images.append(image)
        proofs.append(proof)
        runs.append(
            accepted(
                user,
                cid,
                CONTENT,
                selected_version_id=image["version"]["id"],
                reference_confirmation_id=proof,
            )["run"]
        )
    adapter, launcher = provider(
        tmp_path, {"actions": [{"name": "submit_video", "arguments": video_args()}]}
    )
    manager = RunManager(database, adapter, tool_gateway=ToolGateway(database, media))
    try:
        for index, run in enumerate(runs):
            if resume and index:
                manager.close()
                manager = RunManager(database, adapter, tool_gateway=ToolGateway(database, media))
            assert manager.execute_next()
            result = user[0].get(f"/api/v1/runs/{run['id']}").json()
            assert result["status"] == "completed", result
            task = get(user, result["task_ids"][0])
            assert task["requested_parameters"]["reference_confirmation_id"] == proofs[index]
        assert len(launcher.opened) == (2 if resume else 1)
        frames = [
            json.loads(line)
            for line in (launcher.opened[0][0].cwd / "received.jsonl").read_text().splitlines()
        ]
        assert len(frames) == 2
        for index, frame in enumerate(frames):
            body = json.loads(frame["message"]["content"])
            assert body["selected_version_id"] == images[index]["version"]["id"]
            assert images[1 - index]["version"]["id"] not in frame["message"]["content"]
    finally:
        manager.close()
    for run in runs:
        tid = user[0].get(f"/api/v1/runs/{run['id']}").json()["task_ids"][0]
        assert finish(database, media, user, tid)["status"] == "completed"
    assert len(media.records()) == 2
    assert [json.loads(r[2]) for r in media.records()] == [[i["version"]["id"]] for i in images]


@pytest.mark.parametrize("when", ["before_gateway", "before_task_transaction"])
def test_deleted_confirmation_cannot_be_bound_after_message_acceptance(
    user, database, media, tmp_path, monkeypatch, when
):
    cid = create(user)
    image, proof = confirmed_image(user, cid)
    run = accepted(user, cid, CONTENT, reference_confirmation_id=proof)["run"]
    gateway = ToolGateway(database, media)
    if when == "before_gateway":
        assert delete(user, image).status_code == 202
    else:
        original = gateway.tasks.create_tool

        def revoked(data, authority):
            assert delete(user, image).status_code == 202
            return original(data, authority)

        monkeypatch.setattr(gateway.tasks, "create_tool", revoked)
    adapter, _ = provider(
        tmp_path, {"actions": [{"name": "submit_video", "arguments": video_args()}]}
    )
    manager = RunManager(database, adapter, tool_gateway=gateway)
    try:
        assert manager.execute_next()
        result = user[0].get(f"/api/v1/runs/{run['id']}").json()
        assert result["status"] == "failed" and result["task_ids"] == []
        assert result["error"]["code"] == "RESOURCE_NOT_FOUND"
    finally:
        manager.close()
    assert user[0].get("/api/v1/tasks").json()["items"] == []
    assert media.records() == []


def test_task_transaction_rechecks_exact_run_confirmation(user, database, media):
    cid = create(user)
    image, proof = confirmed_image(user, cid)
    other = confirmation(user, cid, image["version"]["id"]).json()["id"]
    accepted(user, cid, CONTENT, reference_confirmation_id=proof)
    authority = scope(database)
    gateway = ToolGateway(database, media)
    original = gateway.tasks.create_tool

    def wrong_binding(data, trusted):
        # 在可信适配层与事务之间故障注入，不能只依赖网关早先的校验。
        replaced = VideoTaskCreate.model_validate(
            {**data.model_dump(mode="json"), "reference_confirmation_id": other}
        )
        return original(replaced, trusted)

    gateway.tasks.create_tool = wrong_binding
    with pytest.raises(ProblemError) as error:
        gateway.call(authority, 1, "submit_video", video_args())
    assert error.value.code == "REFERENCE_CONFIRMATION_REQUIRED"
    assert user[0].get("/api/v1/tasks").json()["items"] == []


def test_public_video_request_still_requires_explicit_confirmation(user, media):
    cid = create(user)
    confirmed_image(user, cid)
    problem(
        submit(user, {**video_args(), "kind": "ai_video", "conversation_id": cid}),
        422,
        "VALIDATION_ERROR",
    )
