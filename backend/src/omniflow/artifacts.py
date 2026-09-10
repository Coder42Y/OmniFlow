"""作品事实服务；文件写入与数据库关联分步完成，不宣称跨系统 exactly-once。"""

import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from uuid import uuid4

from . import artifact_models as m
from .auth_security import digest, expiry, fail, now, require_session
from .conversations import (
    ConversationService,
    decode_cursor,
    dump,
    encode_cursor,
    not_found,
    owned,
)
from .media_storage import MediaInfo, MediaStorage, inspect_image


def artifact_owned(connection, aid, owner, *, tombstone=False):
    row = connection.execute(
        "SELECT * FROM artifacts WHERE id=? AND owner_id=?", (aid, owner)
    ).fetchone()
    if row is None or (row["deleted_at"] and not tombstone):
        not_found()
    return row


def version_owned(connection, vid, owner, *, aid=None, image=False, tombstone=False):
    row = connection.execute(
        "SELECT v.* FROM artifact_versions v JOIN artifacts a ON a.id=v.artifact_id "
        "WHERE v.id=? AND a.owner_id=?" + ("" if tombstone else " AND a.deleted_at IS NULL"),
        (vid, owner),
    ).fetchone()
    if row is None or (aid is not None and row["artifact_id"] != aid):
        not_found()
    if image and row["media_type"] not in ("image/png", "image/jpeg", "image/webp"):
        fail(422, "VALIDATION_ERROR", "所选版本不是图片")
    return row


def public_artifact(row):
    return {key: row[key] for key in m.Artifact.model_fields}


def public_version(row):
    result = {key: row[key] for key in m.ArtifactVersion.model_fields if key != "content_url"}
    result["content_url"] = f"/api/v1/artifacts/{row['artifact_id']}/versions/{row['id']}/content"
    return result


def authorize_version(connection, cid, vid):
    connection.execute(
        "INSERT OR IGNORE INTO conversation_artifact_versions VALUES (?,?)", (cid, vid)
    )


def confirmation_owned(connection, identity, owner, cid, vid=None):
    row = connection.execute(
        "SELECT * FROM reference_confirmations WHERE id=? AND owner_id=?",
        (identity, owner),
    ).fetchone()
    if row is None:
        not_found()
    version_owned(connection, row["version_id"], owner, image=True)
    if row["conversation_id"] != cid or (vid is not None and row["version_id"] != vid):
        fail(409, "REFERENCE_CONFIRMATION_REQUIRED", "请在当前对话确认所选图片版本")
    return row


def check_uses(connection, aid, *, target_only=False):
    return (
        connection.execute(
            "SELECT 1 FROM task_artifact_uses u JOIN task_media_scopes s ON s.task_id=u.task_id "
            "WHERE u.artifact_id=? AND s.closed_at IS NULL"
            + (" AND u.role='target'" if target_only else ""),
            (aid,),
        ).fetchone()
        is not None
    )


class ArtifactService:
    def __init__(self, database):
        self.database = database
        self.settings = database.settings
        self.storage = MediaStorage(self.settings)
        self.conversations = ConversationService(database)

    @staticmethod
    def check_conversation(connection, cid, owner, *, replay=False):
        if cid:
            row = owned(connection, cid, owner, tombstone=replay)
            if row["deleted_at"]:
                fail(410, "RESOURCE_GONE", "对话已删除，不能重放此动作")

    def replay(self, connection, owner, path, key, request_digest):
        row = connection.execute(
            "SELECT * FROM artifact_idempotency WHERE owner_id=? AND method='POST' "
            "AND path=? AND key=?",
            (owner, path, key),
        ).fetchone()
        if row is None:
            return None
        artifact = artifact_owned(connection, row["artifact_id"], owner, tombstone=True)
        if artifact["deleted_at"]:
            fail(410, "RESOURCE_GONE", "作品已删除，不能重建")
        self.check_conversation(connection, row["conversation_id"], owner, replay=True)
        if row["request_digest"] != request_digest:
            fail(409, "IDEMPOTENCY_CONFLICT", "相同标识的请求内容不一致")
        return json.loads(row["response_json"])

    @staticmethod
    def record(connection, owner, path, key, request_digest, aid, cid, result):
        connection.execute(
            "INSERT INTO artifact_idempotency VALUES (?,'POST',?,?,?,?,?,?,201,?)",
            (owner, path, key, request_digest, aid, cid, dump(result), now()),
        )

    @staticmethod
    def append_version(connection, aid, vid, info, *, parent=None, task_id=None, engine=None):
        """内部任务保存也复用；调用方先落盘，再在同一任务终态事务中调用。

        source_task_id 唯一保证下载恢复不能追加第二版本；不接受客户端文件路径。
        """
        artifact = connection.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone()
        if artifact is None or artifact["deleted_at"]:
            not_found()
        kind = info.media_type.split("/")[0]
        if kind != artifact["kind"]:
            fail(422, "VALIDATION_ERROR", "输出类型与作品不符")
        if (
            parent is None
            and connection.execute(
                "SELECT 1 FROM artifact_versions WHERE artifact_id=?",
                (aid,),
            ).fetchone()
        ):
            fail(409, "VERSION_CONFLICT", "追加版本必须绑定当前父版本")
        number = artifact["version_count"] if parent is None else artifact["version_count"] + 1
        if parent is not None and artifact["current_version_id"] != parent:
            fail(409, "VERSION_CONFLICT", "作品已有更新版本")
        timestamp = now()
        connection.execute(
            "INSERT INTO artifact_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                vid,
                aid,
                number,
                parent,
                task_id,
                engine,
                info.media_type,
                info.byte_size,
                info.sha256,
                info.width,
                info.height,
                info.duration_seconds,
                info.fps,
                timestamp,
            ),
        )
        connection.execute(
            "UPDATE artifacts SET current_version_id=?,version_count=?,updated_at=? WHERE id=?",
            (vid, number, timestamp, aid),
        )

    def _save(self, content, info, semantic, path, key, context, *, aid=None):
        key = self.conversations.key(key)
        request_digest = self.conversations.request_digest(semantic)
        cid = semantic.get("conversation_id")

        def validate(connection):
            owner = require_session(connection, context)["id"]
            # 先校验当前请求的资源归属，不能拿旧键替代权限。
            self.check_conversation(connection, cid, owner, replay=True)
            if aid:
                artifact_owned(connection, aid, owner, tombstone=True)
                version_owned(
                    connection, semantic["base_version_id"], owner, aid=aid, tombstone=True
                )
            return owner

        # 无需新文件的重放先返回；大文件解码/写临时区均在写锁外。
        with self.database.snapshot() as connection:
            owner = validate(connection)
            replay = self.replay(connection, owner, path, key, request_digest)
            if replay is not None:
                return replay, True
        self.conversations.storage_gate()
        with self.storage.stage(content) as stage, self.database.transaction() as connection:
            owner = validate(connection)
            replay = self.replay(connection, owner, path, key, request_digest)
            if replay is not None:
                return replay, True
            self.conversations.storage_gate()
            parent = semantic.get("base_version_id")
            timestamp, vid = now(), str(uuid4())
            identity = aid or str(uuid4())
            if aid:
                artifact = artifact_owned(connection, aid, owner)
                if artifact["kind"] != "text":
                    fail(422, "VALIDATION_ERROR", "此接口仅保存文案版本")
                if artifact["current_version_id"] != parent:
                    fail(409, "VERSION_CONFLICT", "作品已有更新版本")
                if check_uses(connection, aid, target_only=True):
                    fail(409, "ARTIFACT_BUSY", "作品已有未结束修改任务")
            else:
                connection.execute(
                    "INSERT INTO artifacts(id,owner_id,kind,title,current_version_id,"
                    "version_count,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?)",
                    (
                        identity,
                        owner,
                        "text" if info.media_type == "text/plain" else "image",
                        semantic.get("title", "上传图片"),
                        vid,
                        timestamp,
                        timestamp,
                    ),
                )
            self.storage.publish(stage, vid)
            # 若此后进程崩溃/事务回滚，文件留在私有区由显式维护核对清理，不复用坏关联。
            self.append_version(connection, identity, vid, info, parent=parent)
            version = public_version(version_owned(connection, vid, owner))
            result = {
                "artifact": public_artifact(artifact_owned(connection, identity, owner)),
                "version": version,
            }
            if cid:
                authorize_version(connection, cid, vid)
                from .conversations import emit

                emit(connection, cid, "artifact.ready", version)
            self.record(connection, owner, path, key, request_digest, identity, cid, result)
            return result, False

    def upload(self, content, declared_type, cid, key, context):
        # 解析/解码之前再复核可选对话，客户端 MIME 不进入文件名或响应头。
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            self.check_conversation(connection, cid, owner, replay=True)
        info = inspect_image(content, declared_type, self.settings)
        semantic = {"sha256": info.sha256, "conversation_id": cid}
        return self._save(content, info, semantic, "/uploads", key, context)

    def save_text(self, data, key, context, aid=None):
        content = data.content.encode("utf-8")
        info = MediaInfo("text/plain", len(content), hashlib.sha256(content).hexdigest())
        return self._save(
            content,
            info,
            data.model_dump(mode="json", exclude_none=True),
            f"/artifacts/{aid}/text-versions" if aid else "/artifacts",
            key,
            context,
            aid=aid,
        )

    def get(self, aid, context, vid=None):
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            artifact = artifact_owned(connection, aid, owner)
            if vid:
                return public_version(version_owned(connection, vid, owner, aid=aid))
            return public_artifact(artifact)

    def page(self, context, cursor=None, limit=20, *, cid=None, kind=None, aid=None):
        with self.database.snapshot() as connection:
            owner = require_session(connection, context)["id"]
            self.check_conversation(connection, cid, owner)
            if aid:
                artifact_owned(connection, aid, owner)
                scope = ["versions", owner, aid]
                boundary = decode_cursor(cursor, scope, sequence=True)
                rows = connection.execute(
                    "SELECT * FROM artifact_versions WHERE artifact_id=? AND version_number<? "
                    "ORDER BY version_number DESC LIMIT ?",
                    (aid, boundary or 2**63 - 1, limit + 1),
                ).fetchall()
                more, rows = len(rows) > limit, rows[:limit]
                return {
                    "items": [public_version(r) for r in rows],
                    "next_cursor": encode_cursor(scope, rows[-1]["version_number"])
                    if more
                    else None,
                }
            scope = ["artifacts", owner, cid, kind]
            boundary = decode_cursor(cursor, scope)
            where, params = "a.owner_id=? AND a.deleted_at IS NULL", [owner]
            if kind:
                where += " AND a.kind=?"
                params.append(kind)
            if cid:
                where += (
                    " AND EXISTS (SELECT 1 FROM conversation_artifact_versions cv "
                    "JOIN artifact_versions v ON v.id=cv.version_id "
                    "WHERE cv.conversation_id=? AND v.artifact_id=a.id)"
                )
                params.append(cid)
            if boundary:
                where += " AND (a.created_at,a.id)<(?,?)"
                params.extend(boundary)
            rows = connection.execute(
                f"SELECT a.* FROM artifacts a WHERE {where} "
                "ORDER BY a.created_at DESC,a.id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            more, rows = len(rows) > limit, rows[:limit]
            return {
                "items": [public_artifact(r) for r in rows],
                "next_cursor": encode_cursor(scope, [rows[-1]["created_at"], rows[-1]["id"]])
                if more
                else None,
            }

    def confirm(self, cid, data, key, context):
        key = self.conversations.key(key)
        semantic = data.model_dump(mode="json")
        request_digest = self.conversations.request_digest(semantic)
        path = f"/conversations/{cid}/reference-confirmations"
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            self.check_conversation(connection, cid, owner, replay=True)
            row = version_owned(connection, str(data.version_id), owner, tombstone=True)
            replay = self.replay(connection, owner, path, key, request_digest)
            if replay is not None:
                return replay, True
            version_owned(connection, row["id"], owner, image=True)
            result = {"id": str(uuid4()), "conversation_id": cid, **semantic, "created_at": now()}
            connection.execute(
                "INSERT INTO reference_confirmations VALUES (?,?,?,?,?,?)",
                (result["id"], owner, cid, row["id"], data.purpose, result["created_at"]),
            )
            authorize_version(connection, cid, row["id"])
            self.record(
                connection, owner, path, key, request_digest, row["artifact_id"], cid, result
            )
            return result, False

    @staticmethod
    def receipt(row):
        return {
            "artifact_id": row["id"],
            "status": "access_revoked",
            "purge_target_at": row["purge_target_at"],
        }

    def delete(self, aid, context):
        with self.database.transaction() as connection:
            owner = require_session(connection, context)["id"]
            artifact = artifact_owned(connection, aid, owner, tombstone=True)
            if artifact["deleted_at"]:
                return self.receipt(artifact)
            if check_uses(connection, aid):
                fail(409, "RESOURCE_IN_USE", "作品仍被未结束任务使用")
            connection.execute(
                "UPDATE artifacts SET deleted_at=?,purge_target_at=?,updated_at=? WHERE id=?",
                (now(), expiry(self.settings.artifact_cleanup_seconds), now(), aid),
            )
            connection.execute(
                "UPDATE media_grants SET revoked_at=? WHERE version_id IN "
                "(SELECT id FROM artifact_versions WHERE artifact_id=?)",
                (now(), aid),
            )
            connection.execute(
                "UPDATE artifact_idempotency SET response_json='{}' WHERE artifact_id=?", (aid,)
            )
            return self.receipt(artifact_owned(connection, aid, owner, tombstone=True))

    def authorized_versions(self, connection, owner, cid, *, through_seq=None):
        """内部工具可见范围：本对话版本和截至本轮的显式引用，而非用户全库。

        后排消息授权不能经全局关联表提前进入前一轮；有 through_seq 时只读取
        不晚于本轮消息的显式引用及确认，不自动遍历作品库。
        """
        owned(connection, cid, owner)
        # 真正工具上下文由阶段 05 使用显式 per-run 版本集；此列表仅用于非执行时查询。
        if through_seq is not None:
            ids = set()
            for row in connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND seq<=?", (cid, through_seq)
            ):
                ids.update(json.loads(row["attachment_version_ids"]))
                ids.update(json.loads(row["artifact_version_ids"]))
                if row["selected_version_id"]:
                    ids.add(row["selected_version_id"])
            for row in connection.execute(
                "SELECT r.input_json FROM runs r JOIN messages msg ON msg.id=r.user_message_id "
                "WHERE msg.conversation_id=? AND msg.seq<=?",
                (cid, through_seq),
            ):
                confirmation_id = json.loads(row[0]).get("reference_confirmation_id")
                if confirmation_id:
                    confirmed = connection.execute(
                        "SELECT version_id FROM reference_confirmations "
                        "WHERE id=? AND owner_id=? AND conversation_id=?",
                        (confirmation_id, owner, cid),
                    ).fetchone()
                    if confirmed:
                        ids.add(confirmed[0])
        else:
            ids = {
                r[0]
                for r in connection.execute(
                    "SELECT version_id FROM conversation_artifact_versions WHERE conversation_id=?",
                    (cid,),
                )
            }
        return self.available_versions(connection, owner, ids)

    @staticmethod
    def available_versions(connection, owner, ids):
        result = []
        for vid in sorted(ids):
            row = connection.execute(
                "SELECT v.* FROM artifact_versions v JOIN artifacts a ON a.id=v.artifact_id "
                "WHERE v.id=? AND a.owner_id=? AND a.deleted_at IS NULL",
                (vid, owner),
            ).fetchone()
            if row:
                result.append(public_version(row))
        return result

    def cleanup(self):
        """显式本地维护；不因低磁盘删除可用作品，失败可用同一命令继续。

        持短写锁核对单个文件与关联，活跃上传通过 flock 保留。不安全擦除磁盘空闲页。
        """
        self.storage.check_root()
        purged = 0
        with self.database.connect(readonly=True) as connection:
            ids = [
                r[0]
                for r in connection.execute(
                    "SELECT id FROM artifacts WHERE deleted_at IS NOT NULL AND purged_at IS NULL "
                    "AND purge_target_at<=?",
                    (now(),),
                )
            ]
        for aid in ids:
            with self.database.transaction() as connection:
                due = connection.execute(
                    "SELECT 1 FROM artifacts WHERE id=? AND deleted_at IS NOT NULL "
                    "AND purged_at IS NULL AND purge_target_at<=?",
                    (aid, now()),
                ).fetchone()
                if due is None or check_uses(connection, aid):
                    continue
                for row in connection.execute(
                    "SELECT id FROM artifact_versions WHERE artifact_id=?", (aid,)
                ):
                    self.storage.path(row[0]).unlink(missing_ok=True)
                self.storage.sync_directory()
                connection.execute("UPDATE artifacts SET purged_at=? WHERE id=?", (now(), aid))
                purged += 1
        # 只清理服务器自建的文件名；不递归、不跟随符号链接，忽略宿主其他文件。
        for path in self.storage.root.iterdir():
            if not (
                re.fullmatch(r"[a-f0-9-]{36}\.blob", path.name)
                or re.fullmatch(r"stage-[A-Za-z0-9_-]+\.part", path.name)
            ):
                continue
            with self.database.transaction() as connection:
                if connection.execute(
                    "SELECT 1 FROM artifact_versions WHERE id=?", (path.stem,)
                ).fetchone():
                    continue
                # 稳定输出文件可能已发布但终态事务尚未提交；不能当孤立上传清理。
                if connection.execute(
                    "SELECT 1 FROM tasks WHERE output_version_id=? "
                    "AND status NOT IN ('completed','failed','canceled')",
                    (path.stem,),
                ).fetchone():
                    continue
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                except (FileNotFoundError, OSError):
                    continue
                try:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    if os.fstat(fd).st_mtime > time.time() - 3600:
                        continue
                    path.unlink(missing_ok=True)
                finally:
                    os.close(fd)
        return purged

    def capabilities(self, context, provider=None):
        with self.database.snapshot() as connection:
            require_session(connection, context)
            policy = connection.execute(
                "SELECT * FROM generation_policy WHERE singleton=1"
            ).fetchone()

        def unavailable(kind):
            from .media_provider import check_generation, safe_code
            from .problems import ProblemError

            if not policy[f"{kind}_enabled"]:
                return {"available": False, "reason": "GENERATION_PAUSED"}
            if provider is not None and (kind != "text" or self.settings.provider_mode == "real"):
                try:
                    self.conversations.storage_gate()
                    check_generation(provider, kind)
                except ProblemError as exc:
                    return {"available": False, "reason": safe_code(exc)}
                return {"available": True, "reason": None}
            return {"available": False, "reason": "PROVIDER_UNAVAILABLE"}

        return {
            "text": {"model": self.settings.text_model, "availability": unavailable("text")},
            "image": {
                "model": self.settings.image_model,
                "ratios": ["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "21:9"],
                "size_tiers": ["1K", "2K", "3K", "4K"],
                "reference_editing_enabled": bool(provider and provider.reference_editing_enabled),
                "availability": unavailable("image"),
            },
            "ai_video": {
                "model": self.settings.video_model,
                "modes": ["keyframe", "text"],
                "ratios": ["9:16", "16:9"],
                "min_seconds": 4,
                "max_seconds": 12,
                "size_tiers": ["720P"],
                "availability": unavailable("ai_video"),
            },
            "local_motion": unavailable("local_motion"),
            "business_quotas_enabled": False,
            "limits": {
                "max_message_chars": 16000,
                "max_upload_bytes": self.settings.max_upload_bytes,
                "max_image_pixels": self.settings.max_image_pixels,
                "max_message_attachments": 8,
                "max_image_references": 5,
                "max_page_size": 100,
            },
        }


class TaskMediaService:
    """只供可信任务服务/worker 调用；不签发网站用户或模型传入的任意任务/URL。

    阶段 04 必须在任务创建/终态的同一事务登记/关闭，在提交前激活。
    此处无调度、生成或供应商调用；暂未实现的任务端点不能签发真实授权。
    """

    def __init__(self, database):
        self.database = database
        self.settings = database.settings

    @staticmethod
    def reserve(connection, *, task_id, owner, cid, input_version_ids=(), target_version_id=None):
        owned(connection, cid, owner)
        user = connection.execute("SELECT * FROM users WHERE id=?", (owner,)).fetchone()
        if user["status"] != "active":
            fail(403, "ACCOUNT_DISABLED", "账号不可执行新增生成")
        uses = []
        for vid in input_version_ids:
            row = version_owned(connection, vid, owner, image=True)
            uses.append((task_id, row["artifact_id"], vid, "input"))
        if target_version_id:
            row = version_owned(connection, target_version_id, owner)
            artifact = artifact_owned(connection, row["artifact_id"], owner)
            if artifact["current_version_id"] != target_version_id:
                fail(409, "VERSION_CONFLICT", "作品已有更新版本")
            if check_uses(connection, row["artifact_id"], target_only=True):
                fail(409, "ARTIFACT_BUSY", "作品已有未结束修改任务")
            uses.append((task_id, row["artifact_id"], target_version_id, "target"))
        connection.execute(
            "INSERT INTO task_media_scopes(task_id,owner_id,conversation_id,owner_auth_epoch,"
            "created_at) VALUES (?,?,?,?,?)",
            (task_id, owner, cid, user["auth_epoch"], now()),
        )
        connection.executemany("INSERT INTO task_artifact_uses VALUES (?,?,?,?)", uses)

    @staticmethod
    def scope(connection, task_id):
        row = connection.execute(
            "SELECT s.* FROM task_media_scopes s JOIN users u ON u.id=s.owner_id "
            "JOIN conversations c ON c.id=s.conversation_id "
            "WHERE s.task_id=? AND s.closed_at IS NULL AND u.status='active' "
            "AND u.auth_epoch=s.owner_auth_epoch AND c.deleted_at IS NULL",
            (task_id,),
        ).fetchone()
        if row is None:
            not_found()
        return row

    def activate(self, connection, task_id):
        row = self.scope(connection, task_id)
        if row["activated_at"] is None:
            connection.execute(
                "UPDATE task_media_scopes SET activated_at=?,grant_deadline=? WHERE task_id=?",
                (now(), expiry(self.settings.media_grant_window_seconds), task_id),
            )
        # 不允许重试 activate 延长原窗口，未知提交也不能偷偷另起授权生命周期。

    @staticmethod
    def close(connection, task_id):
        connection.execute(
            "UPDATE task_media_scopes SET closed_at=COALESCE(closed_at,?) WHERE task_id=?",
            (now(), task_id),
        )
        connection.execute("UPDATE media_grants SET revoked_at=? WHERE task_id=?", (now(), task_id))

    def issue(self, connection, task_id, vid):
        row = self.scope(connection, task_id)
        if not row["activated_at"] or row["grant_deadline"] <= now():
            not_found()
        version_owned(connection, vid, row["owner_id"], image=True)
        if (
            connection.execute(
                "SELECT 1 FROM task_artifact_uses "
                "WHERE task_id=? AND version_id=? AND role='input'",
                (task_id, vid),
            ).fetchone()
            is None
        ):
            not_found()
        identity, token = str(uuid4()), secrets.token_urlsafe(32)
        connection.execute(
            "INSERT INTO media_grants VALUES (?,?,?,?,'provider_reference',?,NULL,?)",
            (
                identity,
                task_id,
                vid,
                digest("media-grant", token),
                min(expiry(self.settings.media_grant_ttl_seconds), row["grant_deadline"]),
                now(),
            ),
        )
        # 返回独立秘密字段，仅适配器组装指定提供方地址；不进入响应/事件/幂等账本。
        return identity, token

    def grant_version(self, connection, identity, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            not_found()
        row = connection.execute(
            "SELECT * FROM media_grants WHERE id=? AND token_hash=? AND revoked_at IS NULL "
            "AND expires_at>?",
            (identity, digest("media-grant", token), now()),
        ).fetchone()
        if row is None:
            not_found()
        scope = self.scope(connection, row["task_id"])
        if not scope["activated_at"] or scope["grant_deadline"] <= now():
            not_found()
        if (
            connection.execute(
                "SELECT 1 FROM task_artifact_uses "
                "WHERE task_id=? AND version_id=? AND role='input'",
                (row["task_id"], row["version_id"]),
            ).fetchone()
            is None
        ):
            not_found()
        return version_owned(connection, row["version_id"], scope["owner_id"], image=True)
