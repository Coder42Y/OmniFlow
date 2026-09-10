"""对话事实服务：鉴权、幂等、资源与事件在短事务中原子提交。"""

import base64
import hashlib
import json
import re
import shutil
from datetime import datetime
from uuid import UUID, uuid4

from . import conversation_models as m
from .auth_security import fail, now, require_generation_allowed, require_session
from .problems import ProblemError

ACTIVE = "('running','stopping','needs_reconciliation')"
NONTERMINAL = "('queued','running','stopping','needs_reconciliation')"


def dump(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def not_found():
    fail(404, "RESOURCE_NOT_FOUND", "资源不存在或当前用户不可访问")


def owned(connection, conversation_id, owner_id, *, tombstone=False):
    row = connection.execute(
        "SELECT * FROM conversations WHERE id=? AND owner_id=?", (conversation_id, owner_id)
    ).fetchone()
    if row is None or (row["deleted_at"] and not tombstone):
        not_found()
    return row


def require_tool_run(connection, *, owner_id, conversation_id, run_id, manager_token, media=True):
    """内部受控工具必须在保存动作的同一写事务调用；参数来自服务端绑定而非模型。

    此处不创建媒体任务，阶段 04/05 复用并叠加具体引用、意图、费用与种类开关校验。
    """
    conv = owned(connection, conversation_id, owner_id)
    row = get_run(connection, run_id)
    if row is None or row["conversation_id"] != conversation_id:
        not_found()
    if (
        row["status"] != "running"
        or conv["manager_token"] != manager_token
        or not conv["lease_until"]
        or conv["lease_until"] <= now()
    ):
        fail(409, "RUN_ALREADY_TERMINAL", "轮次不再接受新增动作")
    user = connection.execute("SELECT * FROM users WHERE id=?", (owner_id,)).fetchone()
    if user["status"] != "active" or user["auth_epoch"] != row["owner_auth_epoch"]:
        fail(403, "ACCOUNT_DISABLED", "账号不可执行新增生成")
    if media and json.loads(row["input_json"])["generation_permission"] == "discuss_only":
        fail(403, "FORBIDDEN", "本轮仅讨论，不允许创建媒体")
    return row


def public_message(row):
    data = {key: row[key] for key in m.Message.model_fields}
    for name in ("attachment_version_ids", "artifact_version_ids"):
        data[name] = json.loads(data[name])
    return data


def public_run(row):
    data = {key: row[key] for key in m.Run.model_fields}
    data["task_ids"] = json.loads(data["task_ids"])
    data["error"] = json.loads(data["error"]) if data["error"] else None
    return data


def get_run(connection, run_id):
    return connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def get_message(connection, message_id):
    return connection.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()


def public_conversation(connection, row):
    active = connection.execute(
        f"SELECT id FROM runs WHERE conversation_id=? AND status IN {ACTIVE}", (row["id"],)
    ).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_event_id": str(row["last_event_id"]),
        "active_run_id": active["id"] if active else None,
    }


def emit(connection, conversation_id, kind, data):
    # 只接受应用字段，不容许把 CLI 原始事件/错误/工具日志放进派生事件表。
    data = m.EVENT_DATA[kind].model_validate(data).model_dump(mode="json")
    timestamp = now()
    seq = connection.execute(
        "UPDATE conversations SET last_event_id=last_event_id+1, updated_at=? "
        "WHERE id=? RETURNING last_event_id",
        (timestamp, conversation_id),
    ).fetchone()[0]
    if kind == "conversation.updated":
        data["last_event_id"] = str(seq)
        data["updated_at"] = timestamp
    payload = {
        "event_id": str(seq),
        "conversation_id": conversation_id,
        "type": kind,
        "occurred_at": timestamp,
        "data": data,
    }
    connection.execute(
        "INSERT INTO events VALUES (?,?,?,?)", (conversation_id, seq, dump(payload), timestamp)
    )
    return payload


def emit_conversation(connection, conversation_id):
    row = connection.execute(
        "SELECT * FROM conversations WHERE id=?", (conversation_id,)
    ).fetchone()
    emit(connection, conversation_id, "conversation.updated", public_conversation(connection, row))


def encode_cursor(scope, boundary):
    return base64.urlsafe_b64encode(dump([scope, boundary]).encode()).decode().rstrip("=")


def decode_cursor(cursor, scope, *, sequence=False):
    if cursor is None:
        return None
    try:
        if len(cursor) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
            raise ValueError
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(value, list) or len(value) != 2 or value[0] != scope:
            raise ValueError
        boundary = value[1]
        if sequence:
            if type(boundary) is not int or not 1 <= boundary < 2**63:
                raise ValueError
        else:
            if not isinstance(boundary, list) or len(boundary) != 2:
                raise ValueError
            stamp, identity = boundary
            if not isinstance(stamp, str) or not isinstance(identity, str):
                raise ValueError
            parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ")
            if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != stamp or str(UUID(identity)) != identity:
                raise ValueError
        return boundary
    except (ValueError, TypeError, UnicodeError, OverflowError):
        fail(400, "VALIDATION_ERROR", "分页游标无效")


class ConversationService:
    def __init__(self, database):
        self.database = database
        self.settings = database.settings

    def storage_gate(self):
        if shutil.disk_usage(self.settings.data_dir).free < self.settings.min_free_disk_bytes:
            fail(507, "STORAGE_UNAVAILABLE", "存储空间不足，已暂停新增")

    @staticmethod
    def key(key):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
            fail(422, "VALIDATION_ERROR", "幂等键无效")
        return key

    @staticmethod
    def request_digest(data):
        return hashlib.sha256(dump(data).encode()).hexdigest()

    def replay(self, connection, owner_id, path, key, digest):
        row = connection.execute(
            "SELECT * FROM idempotency_records WHERE owner_id=? AND method='POST' "
            "AND path=? AND key=?",
            (owner_id, path, key),
        ).fetchone()
        if row is None:
            return None
        conv = owned(connection, row["conversation_id"], owner_id, tombstone=True)
        if conv["deleted_at"]:
            fail(410, "RESOURCE_GONE", "资源已删除，不能重建")
        if row["request_digest"] != digest:
            fail(409, "IDEMPOTENCY_CONFLICT", "相同标识的请求内容不一致")
        return json.loads(row["response_json"])

    @staticmethod
    def record(connection, owner, path, key, digest, cid, response, status):
        connection.execute(
            "INSERT INTO idempotency_records VALUES (?,'POST',?,?,?,?,?,?,?)",
            (owner, path, key, digest, cid, dump(response), status, now()),
        )

    def create(self, data, key, context):
        key = self.key(key)
        digest = self.request_digest(data.model_dump(mode="json"))
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            replay = self.replay(connection, owner, "/conversations", key, digest)
            if replay is not None:
                return replay, True
            self.storage_gate()
            cid, timestamp = str(uuid4()), now()
            connection.execute(
                "INSERT INTO conversations(id,owner_id,title,created_at,updated_at) "
                "VALUES (?,?,?,?,?)",
                (cid, owner, data.title, timestamp, timestamp),
            )
            result = public_conversation(connection, owned(connection, cid, owner))
            self.record(connection, owner, "/conversations", key, digest, cid, result, 201)
            return result, False

    def conversation(self, cid, context):
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            return public_conversation(connection, owned(connection, cid, owner))

    def rename(self, cid, data, context):
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            owned(connection, cid, owner)
            connection.execute("UPDATE conversations SET title=? WHERE id=?", (data.title, cid))
            emit_conversation(connection, cid)
            return public_conversation(connection, owned(connection, cid, owner))

    def delete(self, cid, context):
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            row = owned(connection, cid, owner, tombstone=True)
            if row["deleted_at"]:
                return
            if connection.execute(
                f"SELECT 1 FROM runs WHERE conversation_id=? AND status IN {NONTERMINAL}", (cid,)
            ).fetchone():
                fail(409, "RESOURCE_IN_USE", "对话仍有未结束轮次")
            if connection.execute(
                "SELECT 1 FROM task_media_scopes WHERE conversation_id=? AND closed_at IS NULL",
                (cid,),
            ).fetchone():
                fail(409, "RESOURCE_IN_USE", "对话仍有未结束媒体任务占用")
            # 阶段 04 所有任务必须在同一创建/终态事务登记/释放媒体范围，包括无输入任务。
            connection.execute(
                "UPDATE conversations SET deleted_at=?,updated_at=? WHERE id=?", (now(), now(), cid)
            )
            # 只留归属/幂等及关系墓碑，清掉网站正文；不删除独立作品或供应商记录。
            connection.execute("UPDATE conversations SET title='已删除对话' WHERE id=?", (cid,))
            connection.execute(
                "UPDATE messages SET content='',attachment_version_ids='[]',"
                "selected_version_id=NULL,artifact_version_ids='[]' WHERE conversation_id=?",
                (cid,),
            )
            connection.execute(
                "UPDATE runs SET input_json='{}',error=NULL WHERE conversation_id=?", (cid,)
            )
            connection.execute(
                "UPDATE idempotency_records SET response_json='{}' WHERE conversation_id=?", (cid,)
            )
            connection.execute("DELETE FROM events WHERE conversation_id=?", (cid,))

    def validate_references(self, connection, owner, cid, data):
        from .artifacts import confirmation_owned, version_owned

        for vid in data.attachment_version_ids:
            version_owned(connection, str(vid), owner, image=True)
        if data.selected_version_id:
            version_owned(connection, str(data.selected_version_id), owner)
        if data.reference_confirmation_id:
            confirmation_owned(
                connection,
                str(data.reference_confirmation_id),
                owner,
                cid,
                str(data.selected_version_id) if data.selected_version_id else None,
            )

    def enqueue(self, cid, data, key, context):
        key = self.key(key)
        semantic = data.model_dump(mode="json", exclude_none=True)
        digest = self.request_digest(semantic)
        path = f"/conversations/{cid}/messages"
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            conv = owned(connection, cid, owner, tombstone=True)
            self.validate_references(connection, owner, cid, data)
            replay = self.replay(connection, owner, path, key, digest)
            if replay is not None:
                return replay, True
            if conv["deleted_at"]:
                fail(410, "RESOURCE_GONE", "资源已删除，不能重建")
            previous = connection.execute(
                "SELECT r.* FROM runs r JOIN messages m ON m.id=r.user_message_id "
                "WHERE m.conversation_id=? AND m.client_message_id=?",
                (cid, str(data.client_message_id)),
            ).fetchone()
            if previous:
                if previous["input_digest"] != digest:
                    fail(409, "IDEMPOTENCY_CONFLICT", "相同标识的请求内容不一致")
                # 即使换 HTTP 键也复用首次受理响应，且登记新键防止日后改内容。
                original = connection.execute(
                    "SELECT response_json FROM idempotency_records WHERE owner_id=? AND path=? "
                    "AND request_digest=? ORDER BY created_at LIMIT 1",
                    (owner, path, digest),
                ).fetchone()
                result = json.loads(original[0])
                self.record(connection, owner, path, key, digest, cid, result, 202)
                return result, True
            require_generation_allowed(connection, owner, "text")
            self.storage_gate()
            count = connection.execute(
                f"SELECT count(*) FROM runs WHERE status IN {NONTERMINAL}"
            ).fetchone()[0]
            if count >= self.settings.run_queue_capacity:
                raise ProblemError(
                    429,
                    "QUEUE_BACKPRESSURE",
                    "队列繁忙",
                    "请稍后重试此动作。",
                    retryable=True,
                    retry_after=2,
                )
            from .artifacts import authorize_version, confirmation_owned

            explicit = set(semantic["attachment_version_ids"])
            if data.selected_version_id:
                explicit.add(str(data.selected_version_id))
            if data.reference_confirmation_id:
                confirmed = confirmation_owned(
                    connection,
                    str(data.reference_confirmation_id),
                    owner,
                    cid,
                )
                explicit.add(confirmed["version_id"])
            for vid in explicit:
                authorize_version(connection, cid, vid)
            mid, rid, timestamp = str(uuid4()), str(uuid4()), now()
            connection.execute(
                "INSERT INTO runs(id,conversation_id,user_message_id,status,"
                "input_json,input_digest,"
                "owner_auth_epoch,created_at,updated_at) VALUES (?,?,?,'queued',?,?,?,?,?)",
                (
                    rid,
                    cid,
                    mid,
                    dump(semantic),
                    digest,
                    require_session(connection, context)["auth_epoch"],
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "INSERT INTO messages(id,conversation_id,seq,role,content,status,run_id,"
                "client_message_id,attachment_version_ids,selected_version_id,"
                "created_at,updated_at) "
                "VALUES (?,?,?,'user',?,'completed',?,?,?,?,?,?)",
                (
                    mid,
                    cid,
                    conv["next_message_seq"],
                    data.content,
                    rid,
                    str(data.client_message_id),
                    dump(semantic["attachment_version_ids"]),
                    semantic.get("selected_version_id"),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE conversations SET next_message_seq=next_message_seq+2 WHERE id=?", (cid,)
            )
            result = {
                "message": public_message(get_message(connection, mid)),
                "run": public_run(get_run(connection, rid)),
            }
            emit(connection, cid, "message.created", result["message"])
            emit(connection, cid, "run.updated", result["run"])
            self.record(connection, owner, path, key, digest, cid, result, 202)
            return result, False

    def run(self, rid, context):
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            row = get_run(connection, rid)
            if row is None:
                not_found()
            owned(connection, row["conversation_id"], owner)
            return public_run(row)

    def cancel(self, rid, context):
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            row = get_run(connection, rid)
            if row is None:
                not_found()
            owned(connection, row["conversation_id"], owner)
            if row["status"] in ("canceled", "stopping"):
                return public_run(row)
            if row["status"] not in ("queued", "running"):
                fail(409, "RUN_ALREADY_TERMINAL", "轮次已结束或需要核对，不能再次停止")
            status = "canceled" if row["status"] == "queued" else "stopping"
            connection.execute(
                "UPDATE runs SET status=?,updated_at=? WHERE id=?", (status, now(), rid)
            )
            if status == "canceled" and row["assistant_message_id"]:
                # 发送前暂停的轮次可能已有空助手消息，取消后不能仍显示排队。
                connection.execute(
                    "UPDATE messages SET status='interrupted',updated_at=? WHERE id=?",
                    (now(), row["assistant_message_id"]),
                )
                emit(
                    connection,
                    row["conversation_id"],
                    "message.updated",
                    public_message(get_message(connection, row["assistant_message_id"])),
                )
            result = public_run(get_run(connection, rid))
            emit(connection, row["conversation_id"], "run.updated", result)
            return result

    @staticmethod
    def message_page(connection, cid, owner, cursor, limit):
        scope = ["messages", owner, cid]
        boundary = decode_cursor(cursor, scope, sequence=True)
        rows = connection.execute(
            "SELECT * FROM messages WHERE conversation_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
            (cid, boundary if boundary is not None else 2**63 - 1, limit + 1),
        ).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {
            "items": [public_message(r) for r in reversed(rows)],
            "next_cursor": encode_cursor(scope, rows[-1]["seq"]) if more else None,
        }

    def page(self, kind, context, cursor=None, limit=20, cid=None):
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            if cid is not None:
                owned(connection, cid, owner)
            if kind == "messages":
                return self.message_page(connection, cid, owner, cursor, limit)
            if kind not in ("conversations", "runs"):
                raise ValueError("未知分页资源")
            scope = [kind, owner, cid]
            boundary = decode_cursor(cursor, scope)
            params = [owner] if kind == "conversations" else [cid]
            where = (
                "owner_id=? AND deleted_at IS NULL"
                if kind == "conversations"
                else "conversation_id=?"
            )
            if boundary:
                where += " AND (created_at,id)<(?,?)"
                params.extend(boundary)
            rows = connection.execute(
                f"SELECT * FROM {kind} WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            items = [
                public_conversation(connection, r) if kind == "conversations" else public_run(r)
                for r in rows
            ]
            return {
                "items": items,
                "next_cursor": encode_cursor(scope, [rows[-1]["created_at"], rows[-1]["id"]])
                if more
                else None,
            }

    def snapshot(self, cid, context):
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            conv = owned(connection, cid, owner)
            messages = self.message_page(connection, cid, owner, None, 50)
            runs = connection.execute(
                f"SELECT * FROM runs WHERE conversation_id=? AND status IN {NONTERMINAL} "
                "ORDER BY created_at,id",
                (cid,),
            ).fetchall()
            from .artifacts import ArtifactService
            from .tasks import public_task

            tasks = connection.execute(
                "SELECT * FROM tasks WHERE conversation_id=? "
                "AND status NOT IN ('completed','failed','canceled') ORDER BY created_at,id",
                (cid,),
            ).fetchall()
            necessary = set()
            for message in messages["items"]:
                necessary.update(message["attachment_version_ids"])
                necessary.update(message["artifact_version_ids"])
                if message["selected_version_id"]:
                    necessary.add(message["selected_version_id"])
            return {
                "conversation": public_conversation(connection, conv),
                "messages": messages["items"],
                "messages_next_cursor": messages["next_cursor"],
                "runs": [public_run(r) for r in runs],
                "tasks": [public_task(task) for task in tasks],
                "artifact_versions": ArtifactService.available_versions(
                    connection, owner, necessary
                ),
                "last_event_id": str(conv["last_event_id"]),
            }

    @staticmethod
    def event_cursor(conv, raw):
        if raw is None:
            return conv["last_event_id"]
        if not re.fullmatch(r"0|[1-9][0-9]{0,18}", raw) or int(raw) > conv["last_event_id"]:
            fail(400, "INVALID_EVENT_CURSOR", "事件游标无效")
        cursor = int(raw)
        if cursor < conv["event_floor"]:
            fail(410, "EVENT_CURSOR_EXPIRED", "事件游标已过期，请重新获取快照")
        return cursor

    def prepare_events(self, cid, raw, context):
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            return self.event_cursor(owned(connection, cid, owner), raw)

    def next_event(self, cid, cursor, context):
        # 每条事件交付前复核登录和归属，避免一大批历史缓冲绕过注销。
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            conv = owned(connection, cid, owner)
            self.event_cursor(conv, str(cursor))
            row = connection.execute(
                "SELECT payload FROM events WHERE conversation_id=? AND seq>? ORDER BY seq LIMIT 1",
                (cid, cursor),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def prune_events(self, cid):
        """仅内部维护调用；删派生日志而非消息，不由 GET 触发写入。"""
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
            if row is None:
                return
            floor = max(
                row["event_floor"], row["last_event_id"] - self.settings.event_retention_count
            )
            connection.execute("UPDATE conversations SET event_floor=? WHERE id=?", (floor, cid))
            connection.execute(
                "DELETE FROM events WHERE conversation_id=? AND seq<=?", (cid, floor)
            )
