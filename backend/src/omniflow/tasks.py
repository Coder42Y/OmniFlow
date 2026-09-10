"""统一持久任务服务：HTTP 与可信工具入口共用创建事务，不负责执行生成。"""

import json
from dataclasses import dataclass
from uuid import uuid4

from .artifacts import (
    ArtifactService,
    TaskMediaService,
    artifact_owned,
    authorize_version,
    confirmation_owned,
    version_owned,
)
from .auth_security import fail, now, require_admin, require_generation_allowed, require_session
from .conversations import (
    ConversationService,
    decode_cursor,
    dump,
    emit,
    encode_cursor,
    get_run,
    not_found,
    owned,
    public_run,
    require_tool_run,
)
from .media_provider import check_generation, provider_for
from .problems import ProblemError

TERMINAL = ("completed", "failed", "canceled")
ENGINES = {
    "image": "agnes-image-2.5-flash",
    "ai_video": "agnes-video-2.5-flash",
    "local_motion": "local-ffmpeg",
}


def task_row(connection, tid):
    return connection.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()


def task_owned(connection, tid, owner):
    row = task_row(connection, tid)
    if row is None or row["owner_id"] != owner:
        not_found()
    return row


def can_recover(row):
    return row["status"] in (
        "running",
        "saving",
        "submission_unknown",
        "needs_reconciliation",
    ) and bool(row["provider_task_id"] or row["result_key"])


def public_task(row):
    keys = (
        "id",
        "conversation_id",
        "run_id",
        "kind",
        "status",
        "execution_engine",
        "created_at",
        "updated_at",
    )
    result = {key: row[key] for key in keys}
    for key in ("requested_parameters", "output_version_ids", "error"):
        result[key] = json.loads(row[key]) if row[key] is not None else None
    result.update(can_cancel=row["status"] == "queued", can_recover=can_recover(row))
    return result


def emit_task(connection, tid):
    row = task_row(connection, tid)
    emit(connection, row["conversation_id"], "task.updated", public_task(row))


def task_error(code, *, retryable=False):
    # 不接受供应商原始 message，也不存原始异常或其字符串。
    return dump(
        {"code": code, "message": "任务暂未完成，请按当前状态处理。", "retryable": retryable}
    )


@dataclass(frozen=True)
class ToolAuthority:
    """仅由阶段 05 受控网关绑定；不属于公开请求字段或模型可设置内容。"""

    owner_id: str
    conversation_id: str
    run_id: str
    manager_token: str
    action_id: str
    direct_text_video: bool = False
    max_tasks: int | None = None


class TaskService:
    def __init__(self, database, provider=None):
        self.database = database
        self.settings = database.settings
        self.provider = provider_for(database, provider)
        self.conversations = ConversationService(database)
        self.media = TaskMediaService(database)

    def references(self, connection, owner, data):
        cid = data["conversation_id"]
        inputs = list(data.get("reference_version_ids", []))
        if data["kind"] == "local_motion":
            inputs.append(data["image_version_id"])
        if data.get("reference_confirmation_id"):
            confirmed = confirmation_owned(
                connection, data["reference_confirmation_id"], owner, cid
            )
            inputs.append(confirmed["version_id"])
        for vid in inputs:
            version_owned(connection, vid, owner, image=True)
        if data.get("target_artifact_id"):
            artifact = artifact_owned(connection, data["target_artifact_id"], owner, tombstone=True)
            version_owned(
                connection, data["base_version_id"], owner, aid=artifact["id"], tombstone=True
            )
        if data.get("regenerate_from_task_id"):
            task_owned(connection, data["regenerate_from_task_id"], owner)
        return tuple(inputs)

    def create(self, data, key, context):
        return self._create(data, key, context=context)

    def create_tool(self, data, authority):
        self.conversations.key(authority.action_id)
        key = self.conversations.request_digest([authority.run_id, authority.action_id])
        return self._create(data, key, authority=authority)

    def _create(self, data, key, *, context=None, authority=None):
        key = self.conversations.key(key)
        semantic = data.model_dump(mode="json", exclude_none=True)
        request_digest = self.conversations.request_digest(semantic)
        cid = semantic["conversation_id"]
        path = "/tasks" if authority is None else f"/internal/runs/{authority.run_id}/tasks"
        with self.database.transaction() as connection:
            if authority is None:
                user = require_session(connection, context)
                owner, run_id = user["id"], None
            else:
                owner, run_id = authority.owner_id, authority.run_id
                if cid != authority.conversation_id:
                    not_found()
                run = require_tool_run(
                    connection,
                    owner_id=owner,
                    conversation_id=cid,
                    run_id=run_id,
                    manager_token=authority.manager_token,
                )
                user = connection.execute("SELECT * FROM users WHERE id=?", (owner,)).fetchone()
            conv = owned(connection, cid, owner, tombstone=True)
            inputs = self.references(connection, owner, semantic)
            if authority is not None:
                seq = connection.execute(
                    "SELECT seq FROM messages WHERE id=?", (run["user_message_id"],)
                ).fetchone()[0]
                allowed = {
                    v["id"]
                    for v in ArtifactService(self.database).authorized_versions(
                        connection, owner, cid, through_seq=seq
                    )
                }
                if not set(
                    inputs
                    + ((semantic["base_version_id"],) if semantic.get("base_version_id") else ())
                ).issubset(allowed):
                    not_found()
                if semantic.get("mode") == "keyframe":
                    original = json.loads(run["input_json"])
                    if semantic["reference_confirmation_id"] != original.get(
                        "reference_confirmation_id"
                    ):
                        fail(409, "REFERENCE_CONFIRMATION_REQUIRED", "本轮须使用用户的确认图片")
                    confirmation_owned(
                        connection,
                        semantic["reference_confirmation_id"],
                        owner,
                        cid,
                        original.get("selected_version_id"),
                    )
                if semantic.get("mode") == "text" and not authority.direct_text_video:
                    fail(409, "REFERENCE_CONFIRMATION_REQUIRED", "默认视频路径需先由用户确认图片")
            previous = connection.execute(
                "SELECT * FROM task_idempotency WHERE owner_id=? AND path=? AND key=?",
                (owner, path, key),
            ).fetchone()
            if conv["deleted_at"]:
                fail(410, "RESOURCE_GONE", "对话已删除，不能重建任务")
            if previous:
                original = task_row(connection, previous["task_id"])
                aids = [original["target_artifact_id"]]
                aids += [
                    r[0]
                    for r in connection.execute(
                        "SELECT artifact_id FROM artifact_versions WHERE source_task_id=?",
                        (original["id"],),
                    )
                ]
                for aid in filter(None, aids):
                    if artifact_owned(connection, aid, owner, tombstone=True)["deleted_at"]:
                        fail(410, "RESOURCE_GONE", "作品已删除，不能重建任务")
                if previous["request_digest"] != request_digest:
                    fail(409, "IDEMPOTENCY_CONFLICT", "相同标识的请求内容不一致")
                return json.loads(previous["response_json"]), True
            if authority is not None and authority.max_tasks is not None:
                count = connection.execute(
                    "SELECT count(*) FROM tasks WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                if count >= authority.max_tasks:
                    fail(403, "FORBIDDEN", "已执行本轮明确授权的数量，请另起用户动作")
            require_generation_allowed(connection, owner, data.kind)
            self.conversations.storage_gate()
            check_generation(self.provider, data.kind)
            if data.kind == "image" and inputs and not self.provider.reference_editing_enabled:
                fail(503, "PROVIDER_UNAVAILABLE", "参考图编辑尚未开放")
            if semantic.get("target_artifact_id"):
                artifact = artifact_owned(connection, semantic["target_artifact_id"], owner)
                expected = "image" if data.kind == "image" else "video"
                if artifact["kind"] != expected:
                    fail(422, "VALIDATION_ERROR", "目标作品类型与任务不符")
            count = connection.execute(
                "SELECT count(*) FROM tasks WHERE status NOT IN ('completed','failed','canceled')"
            ).fetchone()[0]
            if count >= self.settings.task_queue_capacity:
                raise ProblemError(
                    429,
                    "QUEUE_BACKPRESSURE",
                    "队列繁忙",
                    "请稍后重试此动作。",
                    retryable=True,
                    retry_after=2,
                )
            tid, timestamp = str(uuid4()), now()
            self.media.reserve(
                connection,
                task_id=tid,
                owner=owner,
                cid=cid,
                input_version_ids=inputs,
                target_version_id=semantic.get("base_version_id"),
            )
            for vid in inputs:
                authorize_version(connection, cid, vid)
            connection.execute(
                "INSERT INTO tasks(id,owner_id,conversation_id,run_id,kind,status,"
                "requested_parameters,"
                "execution_engine,owner_auth_epoch,target_artifact_id,base_version_id,output_artifact_id,"
                "output_version_id,next_attempt_at,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    owner,
                    cid,
                    run_id,
                    data.kind,
                    dump(semantic),
                    ENGINES[data.kind],
                    user["auth_epoch"],
                    semantic.get("target_artifact_id"),
                    semantic.get("base_version_id"),
                    semantic.get("target_artifact_id") or str(uuid4()),
                    str(uuid4()),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            if run_id:
                ids = json.loads(get_run(connection, run_id)["task_ids"])
                ids.append(tid)
                connection.execute(
                    "UPDATE runs SET task_ids=?,updated_at=? WHERE id=?",
                    (dump(ids), timestamp, run_id),
                )
                emit(connection, cid, "run.updated", public_run(get_run(connection, run_id)))
            result = public_task(task_row(connection, tid))
            emit_task(connection, tid)
            connection.execute(
                "INSERT INTO task_idempotency VALUES (?,'POST',?,?,?,?,?,?)",
                (owner, path, key, request_digest, tid, dump(result), timestamp),
            )
            return result, False

    def get(self, tid, context):
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            return public_task(task_owned(connection, tid, owner))

    def page(self, context, cursor=None, limit=20, *, cid=None, status=None, admin=False):
        with self.database.snapshot() as connection:
            user = (
                require_admin(connection, context)
                if admin
                else require_session(connection, context)
            )
            owner = user["id"]
            if cid:
                owned(connection, cid, owner)
            scope = ["admin_tasks" if admin else "tasks", owner, cid, status]
            boundary = decode_cursor(cursor, scope)
            where, params = ("1=1", []) if admin else ("owner_id=?", [owner])
            if cid:
                where += " AND conversation_id=?"
                params.append(cid)
            if status:
                where += " AND status=?"
                params.append(status)
            if boundary:
                where += " AND (created_at,id)<(?,?)"
                params.extend(boundary)
            rows = connection.execute(
                f"SELECT * FROM tasks WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            if admin:
                items = [
                    {
                        "id": r["id"],
                        "user_id": r["owner_id"],
                        "kind": r["kind"],
                        "status": r["status"],
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                        "error_code": json.loads(r["error"])["code"] if r["error"] else None,
                    }
                    for r in rows
                ]
            else:
                items = [public_task(r) for r in rows]
            return {
                "items": items,
                "next_cursor": encode_cursor(scope, [rows[-1]["created_at"], rows[-1]["id"]])
                if more
                else None,
            }

    def cancel(self, tid, context):
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            row = task_owned(connection, tid, owner)
            if row["status"] == "canceled":
                return public_task(row)
            if row["status"] != "queued":
                fail(409, "TASK_NOT_CANCELABLE", "任务已开始提交，不能承诺撤销")
            connection.execute(
                "UPDATE tasks SET status='canceled',error=NULL,updated_at=? WHERE id=?",
                (now(), tid),
            )
            self.media.close(connection, tid)
            emit_task(connection, tid)
            return public_task(task_row(connection, tid))

    def recover(self, tid, context):
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            row = task_owned(connection, tid, owner)
            if not can_recover(row):
                fail(409, "RECONCILIATION_REQUIRED", "没有可续查的任务或结果线索，不能再次生成")
            # 保留正在工作的租约，不允许 recover 抢走保存者或触发重复 POST。
            status = "saving" if row["result_key"] else "running"
            connection.execute(
                "UPDATE tasks SET status=?,next_attempt_at=?,updated_at=? WHERE id=?",
                (status, now(), now(), tid),
            )
            emit_task(connection, tid)
            return public_task(task_row(connection, tid))
