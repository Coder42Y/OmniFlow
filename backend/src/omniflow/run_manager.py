"""持久轮次调度，不依附 HTTP 或 SSE。

适配器处理明确文字/result；阶段 05 通过封闭结构化动作或内部 MCP 网关执行工具。
默认拒绝提供方调用；测试可注入假 CLI。租约过期的执行轮次只标记待核对，绝不重放。
"""

import json
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from .auth_security import expiry, now, require_generation_allowed
from .conversations import (
    ACTIVE,
    ConversationService,
    dump,
    emit,
    emit_conversation,
    get_message,
    get_run,
    public_message,
    public_run,
)
from .problems import ProblemError


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class TurnResult:
    pass


class Session(Protocol):
    session_id: str

    def send(self, history: list[dict], permission: str) -> None: ...
    def receive(self, timeout: float) -> TextDelta | TurnResult | None: ...
    def stop(self) -> bool:
        """只停止本会话；仅严格 True 表示已确认停止，其余返回值或异常均待核对。"""
        ...

    def close(self) -> bool:
        """有界关闭，只在本会话进程及其受控子进程已确认退出后返回 True。

        超时、异常、None/False 均不是退出证明。调用必须可重复，不影响其他会话。
        """
        ...


class Provider(Protocol):
    def open(
        self, *, owner_id: str, conversation_id: str, resume_id: str | None, model: str
    ) -> Session:
        """有界打开；异常时若不能证明未留下进程，必须抛普通异常而非 ProviderUnavailable。"""
        ...


class UnavailableProvider:
    def open(self, **kwargs):
        raise ProviderUnavailable


class ProviderUnavailable(Exception):
    """open 时保证未留下进程/未提交；send 后不表示提交失败。无原始错误。"""

    def __init__(self, code="PROVIDER_UNAVAILABLE"):
        self.code = (
            code
            if code in ("PROVIDER_UNAVAILABLE", "FREE_ACCESS_UNCONFIRMED", "PROVIDER_LIMIT_REACHED")
            else "PROVIDER_UNAVAILABLE"
        )
        super().__init__(self.code)


class LeaseLost(Exception):
    pass


class RunManager:
    def __init__(self, database, provider=None, *, tool_gateway=None):
        self.database = database
        self.settings = database.settings
        self.service = ConversationService(database)
        if provider is None and self.settings.provider_mode == "real":
            from .runtime import get_runtime
            from .tool_gateway import ToolGateway

            provider = get_runtime(database).text
            tool_gateway = ToolGateway(database)
        self.provider = provider if provider is not None else UnavailableProvider()
        self.token = str(uuid4())
        self.sessions: dict[str, Session] = {}
        self.shutdown = threading.Event()
        self.tool_gateway = tool_gateway

    @staticmethod
    def finish(connection, row, status, code=None):
        error = (
            {"code": code, "message": "轮次未能继续，请查看状态后处理。", "retryable": False}
            if code
            else None
        )
        connection.execute(
            "UPDATE runs SET status=?,error=?,updated_at=? WHERE id=?",
            (status, dump(error) if error else None, now(), row["id"]),
        )
        if row["assistant_message_id"]:
            message_status = {"completed": "completed", "failed": "failed"}.get(
                status, "interrupted"
            )
            connection.execute(
                "UPDATE messages SET status=?,updated_at=? WHERE id=?",
                (message_status, now(), row["assistant_message_id"]),
            )
            emit(
                connection,
                row["conversation_id"],
                "message.updated",
                public_message(get_message(connection, row["assistant_message_id"])),
            )
        emit(
            connection,
            row["conversation_id"],
            "run.updated",
            public_run(get_run(connection, row["id"])),
        )
        emit_conversation(connection, row["conversation_id"])

    def recover_expired(self, connection):
        rows = connection.execute(
            "SELECT r.* FROM runs r JOIN conversations c ON c.id=r.conversation_id "
            "WHERE r.status IN ('running','stopping') "
            "AND (c.lease_until IS NULL OR c.lease_until<=?)",
            (now(),),
        ).fetchall()
        for row in rows:
            self.finish(connection, row, "needs_reconciliation", "RECONCILIATION_REQUIRED")
            connection.execute(
                "UPDATE conversations SET manager_token=NULL,lease_until=NULL WHERE id=?",
                (row["conversation_id"],),
            )
        # 明确回收失败/升级遗留的占用需公开为待核对；每对话只标首个排队轮次。
        uncertain = connection.execute(
            f"""SELECT r.* FROM runs r JOIN cli_process_holds h
            ON h.conversation_id=r.conversation_id JOIN messages m ON m.id=r.user_message_id
            WHERE r.status='queued' AND h.state='uncertain'
            AND NOT EXISTS(SELECT 1 FROM runs a WHERE a.conversation_id=r.conversation_id
                AND a.status IN {ACTIVE})
            AND NOT EXISTS(SELECT 1 FROM runs q JOIN messages qm ON qm.id=q.user_message_id
                WHERE q.conversation_id=r.conversation_id AND q.status='queued' AND qm.seq<m.seq)
            """
        ).fetchall()
        for row in uncertain:
            self.finish(connection, row, "needs_reconciliation", "RECONCILIATION_REQUIRED")
        # 停用/重置后重新启用也不能复活已排队的旧授权动作。
        stale = connection.execute(
            "SELECT r.* FROM runs r JOIN conversations c ON c.id=r.conversation_id "
            "JOIN users u ON u.id=c.owner_id WHERE r.status='queued' "
            "AND (u.status!='active' OR r.owner_auth_epoch!=u.auth_epoch)"
        ).fetchall()
        for row in stale:
            self.finish(connection, row, "canceled", "ACCOUNT_DISABLED")

    def claim(self):
        with self.database.transaction() as connection:
            self.recover_expired(connection)
            row = connection.execute(
                f"""SELECT r.* FROM runs r JOIN conversations c ON c.id=r.conversation_id
                JOIN messages m ON m.id=r.user_message_id JOIN users u ON u.id=c.owner_id
                JOIN generation_policy p ON p.singleton=1
                WHERE r.status='queued' AND c.deleted_at IS NULL AND u.status='active'
                AND p.text_enabled=1 AND r.owner_auth_epoch=u.auth_epoch
                AND (c.manager_token IS NULL OR c.manager_token=? OR c.lease_until<=?)
                AND NOT EXISTS(SELECT 1 FROM cli_process_holds h WHERE h.conversation_id=c.id
                    AND (h.manager_token!=? OR h.state!='open'))
                AND NOT EXISTS(SELECT 1 FROM runs a WHERE a.conversation_id=c.id
                    AND a.status IN {ACTIVE})
                AND NOT EXISTS(SELECT 1 FROM runs q JOIN messages qm ON qm.id=q.user_message_id
                    WHERE q.conversation_id=c.id AND q.status='queued' AND qm.seq<m.seq)
                ORDER BY r.created_at,r.id LIMIT 1""",
                (self.token, now(), self.token),
            ).fetchone()
            if row is None:
                return None
            self.service.storage_gate()
            cid = row["conversation_id"]
            conv = connection.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
            require_generation_allowed(connection, conv["owner_id"], "text")
            connection.execute(
                "UPDATE conversations SET manager_token=?,lease_until=? WHERE id=?",
                (self.token, expiry(self.settings.run_lease_seconds), cid),
            )
            mid = row["assistant_message_id"] or str(uuid4())
            user_message = get_message(connection, row["user_message_id"])
            if row["assistant_message_id"]:
                # 发送前暂停可重领，但保留原助手 ID/seq，不能重复建立或遗漏事件。
                connection.execute(
                    "UPDATE messages SET status='streaming',updated_at=? WHERE id=?",
                    (now(), mid),
                )
            else:
                connection.execute(
                    "INSERT INTO messages(id,conversation_id,seq,role,content,status,"
                    "run_id,created_at,updated_at) "
                    "VALUES (?,?,?,'assistant','','streaming',?,?,?)",
                    (mid, cid, user_message["seq"] + 1, row["id"], now(), now()),
                )
            connection.execute(
                "UPDATE runs SET status='running',assistant_message_id=?,updated_at=? WHERE id=?",
                (mid, now(), row["id"]),
            )
            emit(
                connection,
                cid,
                "message.updated" if row["assistant_message_id"] else "message.created",
                public_message(get_message(connection, mid)),
            )
            emit(connection, cid, "run.updated", public_run(get_run(connection, row["id"])))
            emit_conversation(connection, cid)
            # 已为每轮预留相邻序号；历史仅包含该用户消息及更早轮次，不包含后排消息。
            history = [
                public_message(r)
                for r in connection.execute(
                    "SELECT * FROM messages WHERE conversation_id=? AND seq<=? ORDER BY seq",
                    (cid, user_message["seq"]),
                )
            ]
            return {
                "run_id": row["id"],
                "conversation_id": cid,
                "owner_id": conv["owner_id"],
                "resume_id": conv["cli_session_id"],
                "previous_manager": conv["manager_token"],
                "history": history,
                "permission": json.loads(row["input_json"])["generation_permission"],
            }

    def guarded(self, connection, job):
        row = connection.execute(
            "SELECT r.*,c.manager_token,c.lease_until,c.owner_id,u.status AS owner_status,"
            "u.auth_epoch FROM runs r JOIN conversations c ON c.id=r.conversation_id "
            "JOIN users u ON u.id=c.owner_id WHERE r.id=?",
            (job["run_id"],),
        ).fetchone()
        if (
            row is None
            or row["manager_token"] != self.token
            or not row["lease_until"]
            or row["lease_until"] <= now()
            or row["status"] not in ("running", "stopping")
        ):
            raise LeaseLost
        return row

    def tick(self, job, *, before_send=False):
        with self.database.transaction() as connection:
            row = self.guarded(connection, job)
            connection.execute(
                "UPDATE conversations SET lease_until=? WHERE id=?",
                (expiry(self.settings.run_lease_seconds), job["conversation_id"]),
            )
            if row["owner_status"] != "active" or row["owner_auth_epoch"] != row["auth_epoch"]:
                if row["status"] == "running":
                    connection.execute(
                        "UPDATE runs SET status='stopping',updated_at=? WHERE id=?",
                        (now(), row["id"]),
                    )
                    emit(
                        connection,
                        job["conversation_id"],
                        "run.updated",
                        public_run(get_run(connection, row["id"])),
                    )
                return False
            if before_send and row["status"] == "running":
                enabled = connection.execute(
                    "SELECT text_enabled FROM generation_policy WHERE singleton=1"
                ).fetchone()[0]
                if not enabled:
                    connection.execute(
                        "UPDATE runs SET status='queued',updated_at=? WHERE id=?",
                        (now(), row["id"]),
                    )
                    connection.execute(
                        "UPDATE messages SET status='queued',updated_at=? WHERE id=?",
                        (now(), row["assistant_message_id"]),
                    )
                    emit(
                        connection,
                        job["conversation_id"],
                        "message.updated",
                        public_message(get_message(connection, row["assistant_message_id"])),
                    )
                    emit(
                        connection,
                        job["conversation_id"],
                        "run.updated",
                        public_run(get_run(connection, row["id"])),
                    )
                    emit_conversation(connection, job["conversation_id"])
                    return None  # 尚未 send；不是失败/取消，开关恢复后可继续同一轮。
            return row["status"] == "running"

    def reserve_process(self, job):
        # 在 open 之前持久占位。即使管理器崩溃或 open 未返回句柄，租约过期也不解锁。
        with self.database.transaction() as connection:
            self.guarded(connection, job)
            connection.execute(
                "INSERT INTO cli_process_holds VALUES (?,?,'opening')",
                (job["conversation_id"], self.token),
            )

    def bind(self, job, session):
        if (
            not isinstance(session.session_id, str)
            or not 1 <= len(session.session_id) <= 256
            or not session.session_id.isascii()
        ):
            raise ValueError("适配器会话标识无效")
        with self.database.transaction() as connection:
            self.guarded(connection, job)
            if job["resume_id"] is not None and session.session_id != job["resume_id"]:
                raise ValueError("拒绝恢复到不同会话")
            connection.execute(
                "UPDATE conversations SET cli_session_id=? WHERE id=?",
                (session.session_id, job["conversation_id"]),
            )
            connection.execute(
                "UPDATE cli_process_holds SET state='open' "
                "WHERE conversation_id=? AND manager_token=?",
                (job["conversation_id"], self.token),
            )

    def accept(self, job, event):
        with self.database.transaction() as connection:
            row = self.guarded(connection, job)
            if (
                row["status"] != "running"
                or row["owner_status"] != "active"
                or row["owner_auth_epoch"] != row["auth_epoch"]
            ):
                return False
            if isinstance(event, TextDelta):
                if not isinstance(event.text, str):
                    raise ValueError("增量必须为文本")
                event.text.encode("utf-8")
                message = get_message(connection, row["assistant_message_id"])
                if len(message["content"]) + len(event.text) > self.settings.run_output_max_chars:
                    raise ValueError("输出超过单轮安全上限")
                index = message["chunk_index"] + 1
                connection.execute(
                    "UPDATE messages SET content=content||?,chunk_index=?,updated_at=? WHERE id=?",
                    (event.text, index, now(), message["id"]),
                )
                emit(
                    connection,
                    job["conversation_id"],
                    "message.delta",
                    {
                        "message_id": message["id"],
                        "run_id": row["id"],
                        "chunk_index": index,
                        "delta": event.text,
                    },
                )
            elif isinstance(event, TurnResult):
                self.finish(connection, row, "completed")
            else:
                # 工具原始输出/CLI 思考过程不能当文字；05 接入受控工具分支。
                raise ValueError("未支持的提供方事件")
            return True

    def end(self, job, status, code=None):
        with self.database.transaction() as connection:
            row = self.guarded(connection, job)
            self.finish(connection, row, status, code)

    def drop_session(self, cid):
        session = self.sessions.get(cid)
        confirmed = False
        if session is not None:
            # 保留失败的句柄供原持有者再次确认；不记录可能含路径/秘密的异常。
            with suppress(Exception):
                confirmed = session.close() is True
        with self.database.transaction() as connection:
            if confirmed:
                connection.execute(
                    "DELETE FROM cli_process_holds WHERE conversation_id=? AND manager_token=?",
                    (cid, self.token),
                )
            else:
                connection.execute(
                    "UPDATE cli_process_holds SET state='uncertain' "
                    "WHERE conversation_id=? AND manager_token=?",
                    (cid, self.token),
                )
            connection.execute(
                "UPDATE conversations SET manager_token=NULL,lease_until=NULL "
                "WHERE id=? AND manager_token=? AND NOT EXISTS(SELECT 1 FROM runs "
                f"WHERE conversation_id=conversations.id AND status IN {ACTIVE}) "
                "AND NOT EXISTS(SELECT 1 FROM cli_process_holds WHERE conversation_id=?)",
                (cid, self.token, cid),
            )
        if confirmed:
            self.sessions.pop(cid, None)

    def execute_next(self):
        """每个 manager 实例由一个调度线程使用；多个实例用数据库租约互斥。"""
        if self.shutdown.is_set():
            return False
        self.reap_idle()
        job = self.claim()
        if job is None:
            return False
        cid, session = job["conversation_id"], None
        # 一个执行线程转去其他对话之前，先回收自己的旧空闲进程并释放租约。
        # 不能一边长时间忙于 B，一边让 A 的空闲租约过期却仍占着原 CLI 进程。
        for idle_cid in list(self.sessions):
            if idle_cid != cid:
                self.drop_session(idle_cid)
        submission_started = False
        try:
            if job["previous_manager"] != self.token:
                self.drop_session(cid)
            if not self.tick(job):
                self.end(job, "canceled")
                return True
            session = self.sessions.get(cid)
            if session is None:
                self.reserve_process(job)
                session = self.provider.open(
                    owner_id=job["owner_id"],
                    conversation_id=cid,
                    resume_id=job["resume_id"],
                    model=self.settings.text_model,
                )
                self.sessions[cid] = session
                self.bind(job, session)
            ready = self.tick(job, before_send=True)
            if ready is None:
                self.drop_session(cid)
                return False
            if not ready:
                stopped = session.stop() is True
                self.end(
                    job,
                    "canceled" if stopped else "needs_reconciliation",
                    None if stopped else "RECONCILIATION_REQUIRED",
                )
                self.drop_session(cid)
                return True
            check_ready = getattr(session, "check_ready", None)
            if check_ready is not None:
                check_ready()  # 仅本地证据检查，尚未开始向 CLI 写本轮消息。
            submission_started = True
            session.send(job["history"], job["permission"])
            deadline = time.monotonic() + self.settings.run_max_seconds
            while True:
                running = self.tick(job)
                if not running or self.shutdown.is_set() or time.monotonic() >= deadline:
                    stopped = session.stop() is True
                    status = (
                        ("canceled" if not running else "interrupted")
                        if stopped
                        else "needs_reconciliation"
                    )
                    self.end(job, status, None if stopped else "RECONCILIATION_REQUIRED")
                    self.drop_session(cid)
                    break
                event = session.receive(self.settings.run_poll_seconds)
                if event is None:
                    continue
                from .agy_adapter import StructuredActions, TurnFailure
                from .tool_gateway import RunScope

                if isinstance(event, TurnFailure):
                    status = "failed" if event.status == "ERROR" else "needs_reconciliation"
                    self.end(
                        job,
                        status,
                        "PROVIDER_UNAVAILABLE" if status == "failed" else "RECONCILIATION_REQUIRED",
                    )
                    self.drop_session(cid)
                    break
                if isinstance(event, StructuredActions):
                    if self.tool_gateway is None:
                        raise ValueError("没有已绑定工具网关")
                    scope = RunScope(job["owner_id"], cid, job["run_id"], self.token)
                    try:
                        for index, action in enumerate(event.actions):
                            if not isinstance(action, dict) or set(action) != {"name", "arguments"}:
                                raise ValueError("结构化动作包含未声明字段")
                            self.tool_gateway.call(
                                scope, f"structured:{index}", action["name"], action["arguments"]
                            )
                    except ProblemError as exc:
                        self.end(job, "failed", exc.code)
                        self.drop_session(cid)
                        break
                    continue
                if not self.accept(job, event):
                    continue
                if isinstance(event, TurnResult):
                    break  # 只有明确 result 之后才允许下轮。
        except ProviderUnavailable as exc:
            if session is None and not submission_started:
                # open 的明确无进程失败；普通异常绝不走这一安全释放分支。
                with self.database.transaction() as connection:
                    connection.execute(
                        "DELETE FROM cli_process_holds WHERE conversation_id=? AND manager_token=?",
                        (cid, self.token),
                    )
            with suppress(LeaseLost):
                self.end(
                    job,
                    "needs_reconciliation" if submission_started else "failed",
                    "RECONCILIATION_REQUIRED" if submission_started else exc.code,
                )
            self.drop_session(cid)
        except LeaseLost:
            self.drop_session(cid)  # 旧执行者不能写入，也不能重放。
        except Exception:
            with suppress(LeaseLost):
                self.end(job, "needs_reconciliation", "RECONCILIATION_REQUIRED")
            self.drop_session(cid)
        finally:
            self.service.prune_events(cid)
        return True

    def reap_idle(self):
        # 空闲过期后回收自己的进程；不清除明确恢复 ID、不碰其他管理器。
        for cid in list(self.sessions):
            with self.database.snapshot() as connection:
                row = connection.execute(
                    "SELECT * FROM conversations WHERE id=?", (cid,)
                ).fetchone()
                user = (
                    connection.execute(
                        "SELECT status FROM users WHERE id=?", (row["owner_id"],)
                    ).fetchone()
                    if row
                    else None
                )
            if (
                row is None
                or row["deleted_at"]
                or row["manager_token"] != self.token
                or not row["lease_until"]
                or row["lease_until"] <= now()
                or not user
                or user["status"] != "active"
            ):
                self.drop_session(cid)

    def serve(self):
        try:
            while not self.shutdown.is_set():
                try:
                    worked = self.execute_next()
                except ProblemError as exc:
                    if exc.code != "STORAGE_UNAVAILABLE":
                        raise
                    worked = False  # 低磁盘暂停领取，保留队列并等待空间恢复。
                if not worked:
                    self.shutdown.wait(self.settings.run_poll_seconds)
        finally:
            self.close()

    def close(self):
        self.shutdown.set()
        for cid in list(self.sessions):
            self.drop_session(cid)
        with self.database.transaction() as connection:
            # 活跃轮次仍保留租约至过期核对；只释放明确空闲会话。
            connection.execute(
                "UPDATE conversations SET manager_token=NULL,lease_until=NULL "
                "WHERE manager_token=? "
                "AND NOT EXISTS(SELECT 1 FROM runs WHERE conversation_id=conversations.id "
                f"AND status IN {ACTIVE}) "
                "AND NOT EXISTS(SELECT 1 FROM cli_process_holds "
                "WHERE conversation_id=conversations.id)",
                (self.token,),
            )
