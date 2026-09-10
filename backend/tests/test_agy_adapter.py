"""官方协议解析、实际假进程隔离及 RunManager 接线，不运行 agy。"""

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import user as user
from test_conversations import accepted, create
from test_tasks import finish, get
from test_tasks import media as media

from omniflow.agy_adapter import AgyProvider, TurnFailure, launch_plan
from omniflow.problems import ProblemError
from omniflow.process_transport import ProcessTransport, decode_frame
from omniflow.provider_gate import MODELS, AccessEvidence, EvidenceGate
from omniflow.run_manager import ProviderUnavailable, RunManager, TextDelta, TurnResult
from omniflow.tool_gateway import ToolGateway


def evidence(kind, **changes):
    current = time.time()
    return AccessEvidence(
        MODELS[kind],
        current - 1,
        current + 120,
        available=True,
        free=True,
        overages_disabled=True,
        isolation_verified=True,
        **changes,
    )


def gate():
    return EvidenceGate(evidence)


class Launcher:
    def __init__(self, scenario=None):
        self.scenario = scenario or {}
        self.opened = []

    def __call__(self, plan):
        transport = ProcessTransport(
            [sys.executable, "-I", str(Path(__file__).with_name("fake_agy.py")), *plan.argv[1:]],
            cwd=plan.cwd,
            env={**plan.env, "SYNTHETIC_SCENARIO": json.dumps(self.scenario)},
        )
        self.opened.append((plan, transport))
        return transport


def provider(tmp_path, scenario=None):
    launcher = Launcher(scenario)
    return AgyProvider(tmp_path / "agy", gate=gate(), launcher=launcher), launcher


def open_session(adapter, owner=None, cid=None, resume=None):
    return adapter.open(
        owner_id=owner or str(uuid4()),
        conversation_id=cid or str(uuid4()),
        resume_id=resume,
        model=MODELS["text"],
    )


def history(content):
    return [{"role": "user", "content": content}]


def consume(session):
    events = []
    for _ in range(100):
        event = session.receive(0.1)
        if event:
            events.append(event)
        if isinstance(event, (TurnResult, TurnFailure)):
            return events
    raise AssertionError("缺少 result")


def test_defaults_and_fresh_evidence_required_before_spawn(tmp_path):
    launcher = Launcher()
    adapter = AgyProvider(tmp_path, launcher=launcher)
    with pytest.raises(ProviderUnavailable):
        open_session(adapter)
    assert launcher.opened == []
    for kind in MODELS:
        with pytest.raises(Exception) as denied:
            EvidenceGate().check(kind)
        assert denied.value.code == "FREE_ACCESS_UNCONFIRMED"


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"expires_at": 0}, "FREE_ACCESS_UNCONFIRMED"),
        ({"checked_at": float("nan")}, "FREE_ACCESS_UNCONFIRMED"),
        ({"model": "auto"}, "FREE_ACCESS_UNCONFIRMED"),
        ({"free": False}, "FREE_ACCESS_UNCONFIRMED"),
        ({"overages_disabled": None}, "FREE_ACCESS_UNCONFIRMED"),
        ({"overages_disabled": 1}, "FREE_ACCESS_UNCONFIRMED"),
        ({"limit_reached": True}, "PROVIDER_LIMIT_REACHED"),
        ({"isolation_verified": False}, "PROVIDER_UNAVAILABLE"),
        ({"available": False}, "PROVIDER_UNAVAILABLE"),
        ({"checked_at": time.time() + 500}, "FREE_ACCESS_UNCONFIRMED"),
    ],
)
def test_evidence_fail_closed(changes, code):
    proof = replace(evidence("text"), **changes)
    with pytest.raises(Exception) as denied:
        EvidenceGate(lambda kind: proof).check("text")
    assert denied.value.code == code


def test_official_persistent_protocol_new_context_explicit_resume_and_counters(tmp_path):
    adapter, launcher = provider(tmp_path)
    owner, a, b = str(uuid4()), str(uuid4()), str(uuid4())
    sessions = []
    try:
        first = open_session(adapter, owner, a)
        sessions.append(first)
        first.send(history("随机代号 A"), "requested_only")
        with pytest.raises(ValueError):
            first.send(history("不能抢先"), "requested_only")
        events = consume(first)
        assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "合成回复：随机代号 A"
        assert "SECRET" not in str(events)
        first.send(history("随机代号 A") + history("第二轮"), "requested_only")
        consume(first)
        assert first.cumulative["input_tokens"] == 20  # 不是 10+20，也不叠加 step usage。
        other = open_session(adapter, owner, b)
        sessions.append(other)
        other.send(history("B 独立"), "discuss_only")
        consume(other)
        third = open_session(adapter, str(uuid4()), str(uuid4()))
        sessions.append(third)
        assert len({s.session_id for s in sessions}) == 3
        assert len({transport.process.pid for _, transport in launcher.opened}) == 3
        assert len({plan.cwd for plan, _ in launcher.opened}) == 3
        plan = launcher.opened[0][0]
        frames = [
            json.loads(line) for line in (plan.cwd / "received.jsonl").read_text().splitlines()
        ]
        assert len(frames) == 2 and "随机代号 A" not in frames[1]["message"]["content"]
        assert "随机代号 A" not in (launcher.opened[1][0].cwd / "received.jsonl").read_text()
        assert "--continue" not in plan.argv and "--conversation" not in plan.argv
        assert plan.settings["useG1Credits"] is False
        assert "mcp(*)" not in plan.settings["permissions"]["deny"]
        assert "command(*)" in plan.settings["permissions"]["deny"]
        assert first.close() is True
        resumed = open_session(adapter, owner, a, first.session_id)
        sessions.append(resumed)
        assert resumed.session_id == first.session_id
        assert launcher.opened[-1][0].argv[-2:] == ("--conversation", first.session_id)
    finally:
        for session in sessions:
            assert session.close() is True


@pytest.mark.parametrize("scenario", [{"model": "auto"}, {"permission": "always-proceed"}])
def test_init_mismatch_closes_only_own_process(tmp_path, scenario):
    adapter, launcher = provider(tmp_path, scenario)
    with pytest.raises(ValueError):
        open_session(adapter)
    assert launcher.opened[0][1].process.poll() is not None


@pytest.mark.parametrize(
    "scenario", [{"eof": True}, {"oversize": True}, {"result_cid": str(uuid4())}]
)
def test_bad_stream_never_becomes_success(tmp_path, scenario):
    adapter, _ = provider(tmp_path, scenario)
    session = open_session(adapter)
    try:
        session.send(history("测试"), "requested_only")
        with pytest.raises((EOFError, ValueError)):
            consume(session)
    finally:
        assert session.close()


@pytest.mark.parametrize(
    "status", ["ERROR", "CANCELED", "INTERRUPTED", "WAITING", "INVALID", "RUNNING"]
)
def test_exit_zero_is_not_success(tmp_path, status):
    adapter, _ = provider(tmp_path, {"status": status})
    session = open_session(adapter)
    try:
        session.send(history("测试"), "requested_only")
        events = consume(session)
        assert isinstance(events[-1], TurnFailure)
        assert events[-1].status == status
        with pytest.raises(ValueError):
            session.send(history("不能继续"), "requested_only")
    finally:
        session.close()


def test_stop_waits_for_official_confirmation_and_does_not_stop_another_session(tmp_path):
    adapter, launcher = provider(tmp_path, {"hold": True})
    first, other = open_session(adapter), open_session(adapter)
    try:
        first.send(history("暂停"), "requested_only")
        # 看到记录才表明假进程进入输入循环，防止信号抢在注册处理器前面。
        deadline = time.monotonic() + 2
        while not (launcher.opened[0][0].cwd / "received.jsonl").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert first.stop() is True
        assert launcher.opened[1][1].process.poll() is None
    finally:
        first.close()
        other.close()


def test_live_gate_rechecked_before_second_turn(tmp_path):
    adapter, launcher = provider(tmp_path)
    session = open_session(adapter)
    try:
        session.send(history("一次"), "requested_only")
        consume(session)
        session.gate = EvidenceGate()
        with pytest.raises(ProblemError):
            session.send(history("不能收费"), "requested_only")
        assert len((launcher.opened[0][0].cwd / "received.jsonl").read_text().splitlines()) == 1
    finally:
        session.close()


def test_duplicate_result_cannot_complete_a_later_turn(tmp_path):
    adapter, _ = provider(tmp_path, {"duplicate_result": True})
    session = open_session(adapter)
    try:
        session.send(history("第一轮"), "requested_only")
        consume(session)
        session.send(history("第二轮"), "requested_only")
        with pytest.raises(ValueError, match="result"):
            consume(session)
    finally:
        assert session.close()


def test_duplicate_json_fields_and_unsafe_directory_rejected(tmp_path):
    with pytest.raises(ValueError):
        decode_frame(b'{"event":"result","event":"init"}')
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        launch_plan(link, str(uuid4()), str(uuid4()), None)


def test_real_manager_official_adapter_structured_tools_media_closed_loop(
    user, database, media, tmp_path
):
    cid = create(user)
    args = {"prompt": "合成方块", "aspect_ratio": "9:16", "size_tier": "1K"}
    adapter, _ = provider(tmp_path, {"actions": [{"name": "submit_image", "arguments": args}]})
    manager = RunManager(database, adapter, tool_gateway=ToolGateway(database, media))
    run = accepted(user, cid, "请生成一张图片：合成方块")["run"]
    try:
        assert manager.execute_next()
        result = user[0].get(f"/api/v1/runs/{run['id']}").json()
        assert result["status"] == "completed" and len(result["task_ids"]) == 1
        task = get(user, result["task_ids"][0])
        completed = finish(database, media, user, task["id"])
        assert completed["status"] == "completed" and len(media.records()) == 1
        messages = user[0].get(f"/api/v1/conversations/{cid}/messages").json()["items"]
        assert messages[-1]["artifact_version_ids"] == completed["output_version_ids"]
        assert "SYNTHETIC_SECRET" not in json.dumps(messages)
    finally:
        manager.close()
