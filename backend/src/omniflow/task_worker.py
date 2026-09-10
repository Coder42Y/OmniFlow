"""独立媒体 worker。一次领取只做一次提交、续查或保存；HTTP 生命周期无关。

租约过期不是外部未提交证明。dispatched_at 一旦落盘，任何新执行者都不得 POST。
续查与下载可以重做，最终文件名和版本ID在入队时确定，晚到执行者必须通过租约屏障。
"""

import json
import os
import stat
import threading
from contextlib import contextmanager
from uuid import uuid4

from .artifacts import ArtifactService, TaskMediaService, authorize_version, public_version
from .auth_security import expiry, now, require_generation_allowed
from .conversations import (
    ConversationService,
    dump,
    emit,
    get_message,
    get_run,
    public_message,
)
from .media_provider import (
    Accepted,
    Download,
    Observation,
    SubmissionRejected,
    check_generation,
    provider_for,
    safe_code,
)
from .media_storage import MediaStorage
from .media_validation import inspect_local_output, inspect_output
from .problems import ProblemError
from .tasks import emit_task, task_error, task_row


class LeaseLost(Exception):
    pass


def opaque(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("提供方标识无效")
    value.encode("utf-8")
    return value


class TaskWorker:
    def __init__(self, database, provider=None):
        self.database = database
        self.settings = database.settings
        self.provider = provider_for(database, provider)
        self.media = TaskMediaService(database)
        self.storage = MediaStorage(self.settings)
        self.conversations = ConversationService(database)
        self.shutdown = threading.Event()

    def fenced(self, connection, tid, token):
        row = task_row(connection, tid)
        if row is None or row["lease_token"] != token or row["lease_until"] <= now():
            raise LeaseLost
        return row

    def expire_claims(self, connection):
        # 同步图片/本地运镜已持久保存线索，但返回 ID 前崩溃：只接回原结果，不重做。
        receipts = connection.execute(
            "SELECT t.* FROM tasks t JOIN adapter_receipts a ON a.task_id=t.id "
            "WHERE t.kind IN ('image','local_motion') AND t.dispatched_at IS NOT NULL "
            "AND t.provider_task_id IS NULL "
            "AND t.status IN ('submitting','submission_unknown','needs_reconciliation') "
            "AND (t.lease_until IS NULL OR t.lease_until<=?)",
            (now(),),
        ).fetchall()
        for row in receipts:
            prefix = "image:" if row["kind"] == "image" else "local:"
            connection.execute(
                "UPDATE tasks SET status='running',provider_task_id=?,error=NULL,"
                "lease_token=NULL,lease_until=NULL,next_attempt_at=?,updated_at=? WHERE id=?",
                (prefix + row["id"], now(), now(), row["id"]),
            )
            emit_task(connection, row["id"])
        for row in connection.execute(
            "SELECT * FROM tasks WHERE status='submitting' AND lease_until<=?", (now(),)
        ).fetchall():
            unknown = row["dispatched_at"] is not None
            connection.execute(
                "UPDATE tasks SET status=?,error=?,lease_token=NULL,lease_until=NULL,"
                "updated_at=? WHERE id=?",
                (
                    "submission_unknown" if unknown else "queued",
                    task_error("RECONCILIATION_REQUIRED") if unknown else None,
                    now(),
                    row["id"],
                ),
            )
            emit_task(connection, row["id"])

    def recover_local_publications(self):
        # 本地适配器 publish 已成功、receipt 尚未提交的窗口。只核对该任务
        # 入队时预留的不可覆盖文件；不根据临时文件或其他作品猜测结果。
        # 解码/ffprobe 在写事务外，最后重查租约屏障，不能抢走活跃提交者。
        with self.database.snapshot() as connection:
            rows = connection.execute(
                "SELECT t.* FROM tasks t LEFT JOIN adapter_receipts a ON a.task_id=t.id "
                "WHERE t.kind='local_motion' AND t.execution_engine='local-ffmpeg' "
                "AND t.dispatched_at IS NOT NULL AND t.provider_task_id IS NULL "
                "AND a.task_id IS NULL "
                "AND t.status IN ('submitting','submission_unknown','needs_reconciliation') "
                "AND (t.lease_until IS NULL OR t.lease_until<=?) "
                "ORDER BY t.created_at,t.id LIMIT 100",
                (now(),),
            ).fetchall()
        for row in rows:
            try:
                existing = self.existing_file(row)
                if existing is None:
                    continue
                inspect_local_output(
                    existing.content, json.loads(row["requested_parameters"]), self.settings
                )
            except (OSError, ValueError, ProblemError):
                continue  # 坏图/坏视频/软链接/缺文件不能形成已完成证据，也不重做。
            with self.database.transaction() as connection:
                current = task_row(connection, row["id"])
                if (
                    current["status"] != row["status"]
                    or current["lease_token"] != row["lease_token"]
                    or (current["lease_until"] and current["lease_until"] > now())
                    or current["provider_task_id"] is not None
                    or current["dispatched_at"] != row["dispatched_at"]
                    or current["output_version_id"] != row["output_version_id"]
                ):
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO adapter_receipts(task_id,result_key) VALUES (?,?)",
                    (row["id"], row["output_version_id"]),
                )
                receipt = connection.execute(
                    "SELECT result_key FROM adapter_receipts WHERE task_id=?", (row["id"],)
                ).fetchone()
                if receipt[0] != row["output_version_id"]:
                    continue
                connection.execute(
                    "UPDATE tasks SET status='running',provider_task_id=?,error=NULL,"
                    "lease_token=NULL,lease_until=NULL,next_attempt_at=?,updated_at=? WHERE id=?",
                    ("local:" + row["id"], now(), now(), row["id"]),
                )
                emit_task(connection, row["id"])

    def claim(self):
        self.recover_local_publications()
        with self.database.transaction() as connection:
            self.expire_claims(connection)
            # 已受理任务不再受账号或本地暂停开关阻挡，仍应核对并落盘。
            rows = connection.execute(
                "SELECT * FROM tasks WHERE status IN ('queued','running','saving') "
                "AND next_attempt_at<=? "
                "AND (lease_until IS NULL OR lease_until<=?) ORDER BY created_at,id LIMIT 100",
                (now(), now()),
            ).fetchall()
            for row in rows:
                tid = row["id"]
                if row["status"] == "queued":
                    user = connection.execute(
                        "SELECT * FROM users WHERE id=?", (row["owner_id"],)
                    ).fetchone()
                    if user["status"] != "active" or user["auth_epoch"] != row["owner_auth_epoch"]:
                        connection.execute(
                            "UPDATE tasks SET status='canceled',error=?,updated_at=? WHERE id=?",
                            (task_error("ACCOUNT_DISABLED"), now(), tid),
                        )
                        self.media.close(connection, tid)
                        emit_task(connection, tid)
                        continue
                    try:
                        require_generation_allowed(connection, row["owner_id"], row["kind"])
                        self.conversations.storage_gate()
                    except ProblemError as exc:
                        self.defer_unclaimed(connection, row, exc.code)
                        continue
                token = str(uuid4())
                connection.execute(
                    "UPDATE tasks SET status=?,lease_token=?,lease_until=?,"
                    "intent_at=COALESCE(intent_at,?),"
                    "error=NULL,updated_at=? WHERE id=?",
                    (
                        "submitting" if row["status"] == "queued" else row["status"],
                        token,
                        expiry(self.settings.task_lease_seconds),
                        now(),
                        now(),
                        tid,
                    ),
                )
                emit_task(connection, tid)
                return dict(task_row(connection, tid))
            return None

    def defer_unclaimed(self, connection, row, code):
        error = task_error(code, retryable=True)
        connection.execute(
            "UPDATE tasks SET error=?,next_attempt_at=?,updated_at=? WHERE id=?",
            (error, expiry(self.settings.task_retry_seconds), now(), row["id"]),
        )
        if row["error"] != error:
            emit_task(connection, row["id"])

    @contextmanager
    def heartbeat(self, row):
        stop = threading.Event()

        def renew():
            while not stop.wait(self.settings.task_lease_seconds / 3):
                try:
                    with self.database.transaction() as connection:
                        self.fenced(connection, row["id"], row["lease_token"])
                        connection.execute(
                            "UPDATE tasks SET lease_until=? WHERE id=?",
                            (expiry(self.settings.task_lease_seconds), row["id"]),
                        )
                except Exception:
                    # 续租失败只停止续租；操作结束必须再检查屏障，不能重新提交。
                    return

        thread = threading.Thread(target=renew, name="media-lease", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()

    def transition(self, row, status, *, code=None, provider_id=None, result_key=None, retry=False):
        with self.database.transaction() as connection:
            self.fenced(connection, row["id"], row["lease_token"])
            connection.execute(
                "UPDATE tasks SET status=?,error=?,provider_task_id=COALESCE(?,provider_task_id),"
                "result_key=COALESCE(?,result_key),lease_token=NULL,lease_until=NULL,"
                "next_attempt_at=?,updated_at=? WHERE id=?",
                (
                    status,
                    task_error(code, retryable=retry) if code else None,
                    provider_id,
                    result_key,
                    expiry(
                        self.settings.task_retry_seconds
                        if retry
                        else self.settings.task_poll_seconds
                    ),
                    now(),
                    row["id"],
                ),
            )
            if status in ("failed", "canceled", "completed"):
                self.media.close(connection, row["id"])
            emit_task(connection, row["id"])

    def dispatch(self, row):
        # 提供方本地权益检查在最后提交事务之前；可能改变本地开关的交错必须再复核。
        try:
            check_generation(self.provider, row["kind"])
            request = json.loads(row["requested_parameters"])
            if (
                row["kind"] == "image"
                and request.get("reference_version_ids")
                and not self.provider.reference_editing_enabled
            ):
                raise ProblemError(503, "PROVIDER_UNAVAILABLE", "暂不可用", "参考图编辑尚未开放")
            with self.database.transaction() as connection:
                current = self.fenced(connection, row["id"], row["lease_token"])
                if current["dispatched_at"]:
                    raise LeaseLost
                require_generation_allowed(connection, row["owner_id"], row["kind"])
                self.conversations.storage_gate()
                self.media.activate(connection, row["id"])
                inputs = tuple(
                    r[0]
                    for r in connection.execute(
                        "SELECT version_id FROM task_artifact_uses "
                        "WHERE task_id=? AND role='input' ORDER BY version_id",
                        (row["id"],),
                    )
                )
                connection.execute(
                    "UPDATE tasks SET dispatched_at=? WHERE id=?", (now(), row["id"])
                )
        except ProblemError as exc:
            # 未越过提交边界。账号撤销后取消，不将旧授权任务重新放行。
            status = (
                "canceled" if exc.code in ("ACCOUNT_DISABLED", "RESOURCE_NOT_FOUND") else "queued"
            )
            self.transition(row, status, code=safe_code(exc), retry=status == "queued")
            return
        try:
            accepted = self.provider.submit(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "requested_parameters": request,
                    "execution_engine": row["execution_engine"],
                },
                inputs,
            )
            if type(accepted) is not Accepted:
                raise ValueError
            provider_id = opaque(accepted.task_id)
        except SubmissionRejected:
            self.transition(row, "failed", code="PROVIDER_UNAVAILABLE")
            return
        except Exception:
            self.transition(row, "submission_unknown", code="RECONCILIATION_REQUIRED")
            return
        # 若写入ID失败，意图仍留在 submitting；过期后转未知，绝不重发。
        self.transition(row, "running", provider_id=provider_id)

    def poll(self, row):
        if not row["provider_task_id"]:
            self.transition(row, "needs_reconciliation", code="RECONCILIATION_REQUIRED")
            return
        observation = self.provider.poll(row["provider_task_id"])
        if type(observation) is not Observation:
            raise ValueError
        if observation.status == "completed":
            self.transition(row, "saving", result_key=opaque(observation.result_key))
        elif observation.status == "failed":
            self.transition(row, "failed", code="PROVIDER_UNAVAILABLE")
        elif observation.status == "running":
            self.transition(row, "running")
        else:
            raise ValueError

    def existing_file(self, row):
        try:
            fd = os.open(
                self.storage.path(row["output_version_id"]),
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_size > self.settings.max_generated_bytes
            ):
                raise ValueError
            content = stream.read(self.settings.max_generated_bytes + 1)
        if row["kind"] != "image":
            mime = "video/mp4"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif content.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        else:
            mime = "image/webp"
        return Download(content, mime)

    def save(self, row):
        if not row["result_key"]:
            self.transition(row, "needs_reconciliation", code="RECONCILIATION_REQUIRED")
            return
        self.conversations.storage_gate()
        existing = self.existing_file(row)
        downloaded = existing or self.provider.download(
            row["result_key"], self.settings.max_generated_bytes
        )
        if type(downloaded) is not Download:
            raise ValueError
        info = inspect_output(downloaded.content, downloaded.media_type, row["kind"], self.settings)
        # stage 保持 flock；恢复已有稳定文件时不重新发布、不追加第二版本。
        with (
            self.storage.stage(downloaded.content) as stage,
            self.database.transaction() as connection,
        ):
            current = self.fenced(connection, row["id"], row["lease_token"])
            if current["status"] != "saving":
                raise LeaseLost
            self.conversations.storage_gate()
            aid, vid, timestamp = row["output_artifact_id"], row["output_version_id"], now()
            if not row["target_artifact_id"]:
                connection.execute(
                    "INSERT INTO artifacts(id,owner_id,kind,title,current_version_id,"
                    "version_count,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,1,?,?)",
                    (
                        aid,
                        row["owner_id"],
                        "image" if row["kind"] == "image" else "video",
                        "生成图片"
                        if row["kind"] == "image"
                        else "本地运镜（非AI）"
                        if row["kind"] == "local_motion"
                        else "AI视频",
                        vid,
                        timestamp,
                        timestamp,
                    ),
                )
            if existing is None:
                self.storage.publish(stage, vid)
            ArtifactService.append_version(
                connection,
                aid,
                vid,
                info,
                parent=row["base_version_id"],
                task_id=row["id"],
                engine=row["execution_engine"],
            )
            authorize_version(connection, row["conversation_id"], vid)
            version = public_version(
                connection.execute("SELECT * FROM artifact_versions WHERE id=?", (vid,)).fetchone()
            )
            emit(connection, row["conversation_id"], "artifact.ready", version)
            if row["run_id"]:
                run = get_run(connection, row["run_id"])
                message = get_message(connection, run["assistant_message_id"])
                if message is None:
                    raise ValueError("任务轮次缺少助手消息")
                ids = json.loads(message["artifact_version_ids"])
                if vid not in ids:
                    ids.append(vid)
                connection.execute(
                    "UPDATE messages SET artifact_version_ids=?,updated_at=? WHERE id=?",
                    (dump(ids), timestamp, message["id"]),
                )
                emit(
                    connection,
                    row["conversation_id"],
                    "message.updated",
                    public_message(get_message(connection, message["id"])),
                )
            connection.execute(
                "UPDATE tasks SET status='completed',output_version_ids=?,error=NULL,"
                "lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                (dump([vid]), timestamp, row["id"]),
            )
            self.media.close(connection, row["id"])
            emit_task(connection, row["id"])

    def execute_next(self):
        row = self.claim()
        if row is None:
            return False
        try:
            with self.heartbeat(row):
                if row["status"] == "submitting":
                    self.dispatch(row)
                elif row["status"] == "running":
                    self.poll(row)
                else:
                    self.save(row)
        except LeaseLost:
            pass  # 晚到结果不得覆盖新持有者；提交 ID 丢失需核对，不自动 POST。
        except Exception as exc:
            try:
                if row["status"] == "submitting":
                    # 提交/ID落盘故障均保守为未知；即使未知更新也失败，过期扫描仍兜底。
                    self.transition(row, "submission_unknown", code="RECONCILIATION_REQUIRED")
                else:
                    self.transition(row, row["status"], code=safe_code(exc), retry=True)
            except LeaseLost:
                pass
        return True

    def serve(self):
        while not self.shutdown.is_set():
            self.execute_next()
            self.shutdown.wait(self.settings.task_poll_seconds)
