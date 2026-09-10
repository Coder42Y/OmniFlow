"""停止确认必须为严格的 True，错误形状不能冒充已经停止；仅使用假 CLI。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_auth import admin as admin
from test_auth import browsers as browsers
from test_auth import post
from test_auth import user as user
from test_conversations import accepted, create, read_run, snapshot, wait_for
from test_conversations import manager as manager


@pytest.mark.parametrize("before_send", [False, True])
@pytest.mark.parametrize(
    "confirmation", [False, None, 1, "false", {"stopped": False}, "raise", True]
)
def test_stop_requires_explicit_confirmation(user, manager, monkeypatch, before_send, confirmation):
    cid = create(user)
    rid = accepted(user, cid, "等待释放")["run"]["id"]
    following = accepted(user, cid, "未知停止之后不可盲目执行")["run"]["id"]
    original_open = manager.provider.open

    def instrumented_open(**kwargs):
        session = original_open(**kwargs)
        original_stop = session.stop

        def uncertain_stop():
            original_stop()  # 回收自身子进程，但不能用测试的旁路知识代替返回确认。
            if confirmation == "raise":
                raise TimeoutError("synthetic-stop-timeout")
            return confirmation

        patch.setattr(session, "stop", uncertain_stop)
        if before_send:
            assert post(user[0], f"/runs/{rid}/cancel", user[1]).json()["status"] == "stopping"
        return session

    with monkeypatch.context() as patch:
        patch.setattr(manager.provider, "open", instrumented_open)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(manager.execute_next)
            try:
                if not before_send:
                    wait_for(lambda: snapshot(user, cid)["messages"][1]["content"] == "离线假CLI：")
                    response = post(user[0], f"/runs/{rid}/cancel", user[1])
                    assert response.status_code == 202
                    assert response.json()["status"] == "stopping"
                assert future.result(timeout=5)
            finally:
                if not future.done():
                    manager.shutdown.set()

    result = read_run(user, rid)
    if confirmation is True:
        assert result["status"] == "canceled" and result["error"] is None
    else:
        assert result["status"] == "needs_reconciliation"
        assert result["error"]["code"] == "RECONCILIATION_REQUIRED"
        assert result["error"]["retryable"] is False
        assert not manager.execute_next()
        assert read_run(user, following)["status"] == "queued"
    assistant = snapshot(user, cid)["messages"][1]
    assert assistant["status"] == "interrupted"
    assert assistant["content"] == ("" if before_send else "离线假CLI：")
    assert len(manager.provider.opened) == 1
