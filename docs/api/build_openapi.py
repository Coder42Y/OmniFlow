#!/usr/bin/env python3
"""生成设计阶段的 OpenAPI 契约；不启动 FastAPI、不调用任何提供方。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
S = {}
PATHS = {}
EXAMPLES = {}
RESPONSES = {}

def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}

def obj(properties, required=(), **kwargs):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False, **kwargs}

def arr(items, **kwargs):
    return {"type": "array", "items": items, **kwargs}

def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}

def string(**kwargs):
    return {"type": "string", **kwargs}

def enum(*values):
    return string(enum=list(values))

UUID = string(format="uuid")
DATE = string(format="date-time")
TEXT = string()
TOKEN = string(minLength=32, maxLength=256, pattern="^[A-Za-z0-9_-]+$", writeOnly=True)
BOOL = {"type": "boolean"}
INT = {"type": "integer", "minimum": 0}
POS = {"type": "integer", "minimum": 1}
CURSOR = string(minLength=1, maxLength=512)
EID = string(pattern="^(0|[1-9][0-9]*)$")
IMAGE_RATIO = enum("1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2", "21:9")
VIDEO_RATIO = enum("9:16", "16:9")
KIND = enum("image", "video", "text")
TASK_KIND = enum("image", "ai_video", "local_motion")
TASK_STATUS = enum("queued", "submitting", "running", "saving", "completed", "failed",
                   "canceled", "submission_unknown", "needs_reconciliation")
RUN_STATUS = enum("queued", "running", "stopping", "completed", "failed", "canceled",
                  "interrupted", "needs_reconciliation")

S["Problem"] = obj({
    "type": string(format="uri-reference"), "title": string(),
    "status": {"type": "integer", "minimum": 400, "maximum": 599},
    "code": string(pattern="^[A-Z][A-Z0-9_]*$"), "detail": string(),
    "request_id": UUID, "retryable": BOOL,
    "errors": arr(obj({"field": string(), "code": string(), "message": string()},
                      ["field", "code", "message"])),
}, ["type", "title", "status", "code", "detail", "request_id", "retryable"])
S["User"] = obj({"id": UUID, "username": string(), "role": enum("user", "admin"),
                 "status": enum("active", "disabled"), "created_at": DATE},
                ["id", "username", "role", "status", "created_at"])
S["AuthPolicy"] = obj({"username_pattern": string(), "username_normalization": string(),
    "password_min_length": POS, "password_max_length": POS,
    "session_ttl_seconds": POS, "invitation_ttl_default_seconds": POS,
    "password_reset_ttl_default_seconds": POS},
    ["username_pattern", "username_normalization", "password_min_length", "password_max_length",
     "session_ttl_seconds", "invitation_ttl_default_seconds", "password_reset_ttl_default_seconds"])
S["CsrfToken"] = obj({"csrf_token": string(minLength=32), "expires_at": DATE}, ["csrf_token", "expires_at"])
S["LoginInput"] = obj({"username": string(minLength=1, maxLength=128),
    "password": string(minLength=1, maxLength=128, writeOnly=True)}, ["username", "password"])
S["RegisterInput"] = obj({"invite_token": TOKEN,
    "username": string(minLength=1, maxLength=128),
    "password": string(minLength=1, maxLength=128, writeOnly=True)},
    ["invite_token", "username", "password"])
S["TokenInput"] = obj({"token": TOKEN}, ["token"])
S["TokenValidity"] = obj({"valid": BOOL, "expires_at": DATE}, ["valid", "expires_at"])
S["InviteTokenValidity"] = obj({"valid": BOOL, "expires_at": nullable(DATE)}, ["valid", "expires_at"])
S["PasswordResetInput"] = obj({"reset_token": TOKEN,
    "new_password": string(minLength=1, maxLength=128, writeOnly=True)}, ["reset_token", "new_password"])
S["LoginResult"] = obj({"user": ref("User"), "session_expires_at": DATE,
    "csrf_token": string(minLength=32)}, ["user", "session_expires_at", "csrf_token"])
S["Availability"] = obj({"available": BOOL, "reason": nullable(string())}, ["available", "reason"])
S["Capabilities"] = obj({
    "text": obj({"model": enum("gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high"),
                  "availability": ref("Availability")}, ["model", "availability"]),
    "image": obj({"model": {"const": "agnes-image-2.5-flash"},
                  "ratios": arr(IMAGE_RATIO), "size_tiers": arr(enum("1K", "2K", "3K", "4K")),
                  "reference_editing_enabled": BOOL, "availability": ref("Availability")},
                 ["model", "ratios", "size_tiers", "reference_editing_enabled", "availability"]),
    "ai_video": obj({"model": {"const": "agnes-video-2.5-flash"}, "modes": arr(enum("keyframe", "text")),
                     "ratios": arr(VIDEO_RATIO), "min_seconds": POS, "max_seconds": POS,
                     "size_tiers": arr(enum("720P")), "availability": ref("Availability")},
                    ["model", "modes", "ratios", "min_seconds", "max_seconds", "size_tiers", "availability"]),
    "local_motion": ref("Availability"),
    "business_quotas_enabled": {"const": False},
    "limits": obj({"max_message_chars": POS, "max_upload_bytes": POS, "max_image_pixels": POS,
                   "max_message_attachments": POS, "max_image_references": POS, "max_page_size": POS},
                  ["max_message_chars", "max_upload_bytes", "max_image_pixels", "max_message_attachments",
                   "max_image_references", "max_page_size"]),
}, ["text", "image", "ai_video", "local_motion", "business_quotas_enabled", "limits"])
S["ConversationCreate"] = obj({"title": string(minLength=1, maxLength=120)})
S["ConversationPatch"] = obj({"title": string(minLength=1, maxLength=120)}, ["title"])
S["Conversation"] = obj({"id": UUID, "title": string(), "created_at": DATE, "updated_at": DATE,
    "last_event_id": EID, "active_run_id": nullable(UUID)},
    ["id", "title", "created_at", "updated_at", "last_event_id", "active_run_id"])
S["MessageCreate"] = obj({"client_message_id": UUID,
    "content": string(maxLength=16000),
    "attachment_version_ids": arr(UUID, maxItems=8, uniqueItems=True),
    "selected_version_id": UUID, "reference_confirmation_id": UUID,
    "generation_permission": enum("requested_only", "discuss_only")},
    ["client_message_id", "content"], anyOf=[
        {"properties": {"content": {"minLength": 1}}, "required": ["content"]},
        {"properties": {"attachment_version_ids": {"minItems": 1}}, "required": ["attachment_version_ids"]},
    ])
S["Message"] = obj({"id": UUID, "conversation_id": UUID, "seq": POS,
    "role": enum("user", "assistant"), "content": TEXT,
    "status": enum("queued", "streaming", "completed", "interrupted", "failed"),
    "run_id": nullable(UUID), "client_message_id": nullable(UUID),
    "attachment_version_ids": arr(UUID), "selected_version_id": nullable(UUID),
    "artifact_version_ids": arr(UUID, uniqueItems=True), "created_at": DATE, "updated_at": DATE},
    ["id", "conversation_id", "seq", "role", "content", "status", "run_id", "client_message_id",
     "attachment_version_ids", "selected_version_id", "artifact_version_ids", "created_at", "updated_at"])
S["ResourceError"] = obj({"code": string(), "message": string(), "retryable": BOOL},
                        ["code", "message", "retryable"])
S["Run"] = obj({"id": UUID, "conversation_id": UUID, "user_message_id": UUID,
    "assistant_message_id": nullable(UUID), "status": RUN_STATUS, "task_ids": arr(UUID),
    "error": nullable(ref("ResourceError")), "created_at": DATE, "updated_at": DATE},
    ["id", "conversation_id", "user_message_id", "assistant_message_id", "status", "task_ids",
     "error", "created_at", "updated_at"])
S["MessageAccepted"] = obj({"message": ref("Message"), "run": ref("Run")}, ["message", "run"])
S["ReferenceConfirmationCreate"] = obj({"version_id": UUID, "purpose": {"const": "video_first_frame"}},
                                       ["version_id", "purpose"])
S["ReferenceConfirmation"] = obj({"id": UUID, "conversation_id": UUID, "version_id": UUID,
    "purpose": {"const": "video_first_frame"}, "created_at": DATE},
    ["id", "conversation_id", "version_id", "purpose", "created_at"])

base_task = {"conversation_id": UUID, "target_artifact_id": UUID, "base_version_id": UUID,
             "regenerate_from_task_id": UUID}
version_pair = {"target_artifact_id": ["base_version_id"], "base_version_id": ["target_artifact_id"]}
S["ImageTaskCreate"] = obj({**base_task, "kind": {"const": "image"},
    "prompt": string(minLength=1, maxLength=4000), "aspect_ratio": IMAGE_RATIO,
    "size_tier": enum("1K", "2K", "3K", "4K"),
    "reference_version_ids": arr(UUID, maxItems=5, uniqueItems=True)},
    ["conversation_id", "kind", "prompt", "aspect_ratio", "size_tier"], dependentRequired=version_pair)
S["VideoTaskCreate"] = obj({**base_task, "kind": {"const": "ai_video"},
    "prompt": string(minLength=1, maxLength=4000), "mode": enum("keyframe", "text"),
    "reference_confirmation_id": UUID,
    "seconds": {"type": "integer", "minimum": 4, "maximum": 12},
    "size_tier": {"const": "720P"}, "aspect_ratio": VIDEO_RATIO},
    ["conversation_id", "kind", "prompt", "mode", "seconds", "size_tier", "aspect_ratio"],
    dependentRequired=version_pair,
    allOf=[{"if": {"properties": {"mode": {"const": "keyframe"}}, "required": ["mode"]},
            "then": {"required": ["reference_confirmation_id"]},
            "else": {"not": {"required": ["reference_confirmation_id"]}}}])
S["LocalMotionTaskCreate"] = obj({**base_task, "kind": {"const": "local_motion"},
    "image_version_id": UUID, "motion_type": enum("dolly_in", "pan_left", "pan_right", "dynamic_float"),
    "seconds": {"type": "number", "minimum": 1, "maximum": 30}, "aspect_ratio": VIDEO_RATIO},
    ["conversation_id", "kind", "image_version_id", "motion_type", "seconds", "aspect_ratio"],
    dependentRequired=version_pair)
S["TaskCreate"] = {"oneOf": [ref("ImageTaskCreate"), ref("VideoTaskCreate"), ref("LocalMotionTaskCreate")],
    "discriminator": {"propertyName": "kind", "mapping": {
        "image": "#/components/schemas/ImageTaskCreate", "ai_video": "#/components/schemas/VideoTaskCreate",
        "local_motion": "#/components/schemas/LocalMotionTaskCreate"}}}
S["Task"] = obj({"id": UUID, "conversation_id": UUID, "run_id": nullable(UUID),
    "kind": TASK_KIND, "status": TASK_STATUS, "requested_parameters": ref("TaskCreate"),
    "execution_engine": enum("agnes-image-2.5-flash", "agnes-video-2.5-flash", "local-ffmpeg"),
    "output_version_ids": arr(UUID), "can_cancel": BOOL, "can_recover": BOOL,
    "error": nullable(ref("ResourceError")), "created_at": DATE, "updated_at": DATE},
    ["id", "conversation_id", "run_id", "kind", "status", "requested_parameters", "execution_engine",
     "output_version_ids", "can_cancel", "can_recover", "error", "created_at", "updated_at"])
S["Artifact"] = obj({"id": UUID, "kind": KIND, "title": string(maxLength=120),
    "current_version_id": UUID, "version_count": POS, "created_at": DATE, "updated_at": DATE},
    ["id", "kind", "title", "current_version_id", "version_count", "created_at", "updated_at"])
S["ArtifactVersion"] = obj({"id": UUID, "artifact_id": UUID, "version_number": POS,
    "parent_version_id": nullable(UUID), "source_task_id": nullable(UUID),
    "execution_engine": nullable(enum("agnes-image-2.5-flash", "agnes-video-2.5-flash", "local-ffmpeg")),
    "media_type": enum("image/png", "image/jpeg", "image/webp", "video/mp4", "text/plain"),
    "byte_size": INT, "sha256": string(pattern="^[a-f0-9]{64}$"),
    "width": nullable(POS), "height": nullable(POS),
    "duration_seconds": nullable({"type": "number", "minimum": 0}),
    "fps": nullable({"type": "number", "exclusiveMinimum": 0}),
    "created_at": DATE, "content_url": string(format="uri-reference")},
    ["id", "artifact_id", "version_number", "parent_version_id", "source_task_id", "execution_engine", "media_type", "byte_size",
     "sha256", "width", "height", "duration_seconds", "fps", "created_at", "content_url"])
S["ArtifactCreated"] = obj({"artifact": ref("Artifact"), "version": ref("ArtifactVersion")}, ["artifact", "version"])
S["TextArtifactCreate"] = obj({"kind": {"const": "text"}, "title": string(minLength=1, maxLength=120),
    "content": string(minLength=1, maxLength=50000), "conversation_id": UUID}, ["kind", "title", "content"])
S["TextVersionCreate"] = obj({"base_version_id": UUID, "content": string(minLength=1, maxLength=50000),
    "conversation_id": UUID}, ["base_version_id", "content"])
S["UploadInput"] = obj({"file": string(format="binary"), "conversation_id": UUID}, ["file"])
S["DeletionReceipt"] = obj({"artifact_id": UUID, "status": {"const": "access_revoked"},
    "purge_target_at": DATE}, ["artifact_id", "status", "purge_target_at"])
S["ConversationSnapshot"] = obj({"conversation": ref("Conversation"), "messages": arr(ref("Message")),
    "messages_next_cursor": nullable(CURSOR), "runs": arr(ref("Run")), "tasks": arr(ref("Task")),
    "artifact_versions": arr(ref("ArtifactVersion")), "last_event_id": EID},
    ["conversation", "messages", "messages_next_cursor", "runs", "tasks", "artifact_versions", "last_event_id"])
S["InviteCreate"] = obj({"expires_in_seconds": nullable(POS)})
S["Invite"] = obj({"id": UUID, "status": enum("active", "used", "revoked", "expired"),
    "created_at": DATE, "expires_at": nullable(DATE)}, ["id", "status", "created_at", "expires_at"])
S["InviteIssued"] = obj({"invite": ref("Invite"), "invite_url": string(format="uri")}, ["invite", "invite_url"])
S["UserStatusPatch"] = obj({"status": enum("active", "disabled")}, ["status"])
S["PasswordResetIssue"] = obj({"verification_method": enum("trusted_existing_contact", "in_person"),
    "note": string(maxLength=200)}, ["verification_method"])
S["PasswordResetIssued"] = obj({"id": UUID, "expires_at": DATE,
    "reset_url": string(format="uri")}, ["id", "expires_at", "reset_url"])
S["GenerationPolicy"] = obj({"text_enabled": BOOL, "image_enabled": BOOL,
    "ai_video_enabled": BOOL, "local_motion_enabled": BOOL, "updated_at": DATE},
    ["text_enabled", "image_enabled", "ai_video_enabled", "local_motion_enabled", "updated_at"])
S["GenerationPolicyPatch"] = obj({"text_enabled": BOOL, "image_enabled": BOOL,
    "ai_video_enabled": BOOL, "local_motion_enabled": BOOL}, minProperties=1)
S["OperationalTask"] = obj({"id": UUID, "user_id": UUID, "kind": TASK_KIND, "status": TASK_STATUS,
    "created_at": DATE, "updated_at": DATE, "error_code": nullable(string())},
    ["id", "user_id", "kind", "status", "created_at", "updated_at", "error_code"])
S["Health"] = obj({"status": enum("ok", "unavailable")}, ["status"])
for name in ["Conversation", "Message", "Run", "Task", "Artifact", "ArtifactVersion", "Invite", "User", "OperationalTask"]:
    S[name + "Page"] = obj({"items": arr(ref(name)), "next_cursor": nullable(CURSOR)}, ["items", "next_cursor"])

# SSE数据结构与REST资源共用定义，原始CLI事件不能直接作为浏览器事件返回。
S["MessageDelta"] = obj({"message_id": UUID, "run_id": UUID,
    "chunk_index": POS, "delta": string()}, ["message_id", "run_id", "chunk_index", "delta"])
event_data = {"message.created": "Message", "message.delta": "MessageDelta", "message.updated": "Message",
              "run.updated": "Run", "task.updated": "Task", "artifact.ready": "ArtifactVersion",
              "conversation.updated": "Conversation"}
S["ConversationEvent"] = {"oneOf": []}
for event, data in event_data.items():
    name = "Event" + "".join(x.title() for x in event.split('.'))
    S[name] = obj({"event_id": EID, "conversation_id": UUID, "type": {"const": event},
                  "occurred_at": DATE, "data": ref(data)},
                 ["event_id", "conversation_id", "type", "occurred_at", "data"])
    S["ConversationEvent"]["oneOf"].append(ref(name))
S["StreamControl"] = obj({"code": enum("AUTH_REQUIRED", "EVENT_CURSOR_EXPIRED", "RESOURCE_NOT_FOUND", "SERVICE_RESTARTING"),
    "message": string()}, ["code", "message"])

PARAMS = {
    "IdempotencyKey": {"name": "Idempotency-Key", "in": "header", "required": True,
        "description": "同一用户动作重试必须复用；同键不同语义请求409。不是身份凭据。",
        "schema": string(minLength=8, maxLength=128, pattern="^[A-Za-z0-9._:-]+$")},
    "PageCursor": {"name": "cursor", "in": "query", "required": False, "schema": CURSOR},
    "PageLimit": {"name": "limit", "in": "query", "required": False,
                  "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
    "ConversationFilter": {"name": "conversation_id", "in": "query", "required": False, "schema": UUID},
    "LastEventId": {"name": "Last-Event-ID", "in": "header", "required": False, "schema": EID},
    "AfterEventId": {"name": "after_event_id", "in": "query", "required": False, "schema": EID},
    "Range": {"name": "Range", "in": "header", "required": False,
              "description": "仅单区间bytes=a-b、bytes=a-或bytes=-n；多区间416。", "schema": string()},
    "Download": {"name": "download", "in": "query", "required": False,
                 "schema": {"type": "boolean", "default": False}},
}

COOKIE = [{"SessionCookie": []}]
WRITE = [{"SessionCookie": [], "CsrfHeader": []}]
PREAUTH = [{"CsrfContextCookie": [], "CsrfHeader": []}, {"SessionCookie": [], "CsrfHeader": []}]
COMMON_HEADERS = {"X-Request-ID": {"schema": UUID, "description": "服务器生成的追踪ID"},
                  "Cache-Control": {"schema": string(), "description": "受保护响应使用private, no-store"}}

def response(description, schema=None, media="application/json"):
    r = {"description": description, "headers": COMMON_HEADERS.copy()}
    if schema is not None:
        r["content"] = {media: {"schema": schema}}
    return r

def error_response(code, head=False):
    name = f'HTTP{code}' + ('Head' if head else '')
    if name not in RESPONSES:
        value = response(f"HTTP {code}；稳定错误码与处理方式见接口文档",
                         None if head else ref("Problem"), "application/problem+json")
        if code == 429:
            value['headers']['Retry-After'] = {'schema': string(pattern='^[0-9]+$'),
                'description': '安全限流或队列背压；不是用户日配额'}
        if code == 416:
            value['headers']['Content-Range'] = {'schema': string(), 'description': 'bytes */实际长度'}
        RESPONSES[name] = value
    return {'$ref': '#/components/responses/' + name}

def operation(path, method, operation_id, summary, tag, *, result=None, status=200, body=None,
              security=None, anonymous=False, preauth=False, idem=False, page=False,
              parameters=(), errors=(), description="", admin=False, media="application/json"):
    method = method.lower()
    sec = ([] if anonymous else PREAUTH if preauth else
           WRITE if method in ('post', 'patch', 'put', 'delete') else COOKIE) if security is None else security
    p = []
    for segment in path.split('/'):
        if segment.startswith('{'):
            p.append({"name": segment[1:-1], "in": "path", "required": True, "schema": UUID})
    if idem:
        p.append({"$ref": "#/components/parameters/IdempotencyKey"})
    if page:
        p.extend({"$ref": "#/components/parameters/" + x} for x in ['PageCursor', 'PageLimit'])
    p.extend({"$ref": "#/components/parameters/" + x} if isinstance(x, str) else x for x in parameters)
    result_schema = ref(result) if isinstance(result, str) else result
    responses = {str(status): response(summary, result_schema, media)}
    error_codes = set(errors) | {400, 429, 500, 503}
    if sec:
        error_codes.add(403)
    if any('SessionCookie' in x for x in sec) and not preauth:
        error_codes.add(401)
    if '{' in path:
        error_codes.add(404)
    if body is not None:
        error_codes.add(422)
    if idem:
        error_codes |= {409, 410}
    for code in sorted(error_codes):
        responses.setdefault(str(code), error_response(code, head=method == 'head'))
    if idem:
        responses[str(status)]['headers']['Idempotency-Replayed'] = {"schema": BOOL}
    d = {"operationId": operation_id, "summary": summary, "tags": [tag],
         "description": description or summary, "security": sec, "parameters": p, "responses": responses,
         "x-implementation-status": "design-only"}
    if admin:
        d['x-required-role'] = 'admin'
        d['description'] += ' 必须校验admin角色；Cookie鉴权本身不代表管理员授权。'
    if body is not None:
        body_schema = ref(body) if isinstance(body, str) else body
        d['requestBody'] = {"required": True, "content": {"application/json": {"schema": body_schema}}}
    if method == 'head':
        for r in responses.values():
            r.pop('content', None)
    PATHS.setdefault(path, {})[method] = d
    return d

operation('/health/live', 'get', 'health_live', '进程存活', '系统', result='Health', anonymous=True)
operation('/health/ready', 'get', 'health_ready', '数据库等必要组件就绪', '系统', result='Health', anonymous=True,
          description='不返回进程路径、数据库位置、凭据或完整供应商诊断。未就绪503 Problem。')
operation('/auth/policy', 'get', 'get_auth_policy', '查询注册与登录策略', '账号', result='AuthPolicy', anonymous=True)
csrf_op = operation('/auth/csrf', 'get', 'get_csrf', '取得绑定当前会话或匿名上下文的CSRF令牌', '账号', result='CsrfToken', anonymous=True)
csrf_op['responses']['200']['headers']['Set-Cookie'] = {"schema": string(), "description": "必要时设置HttpOnly匿名CSRF上下文Cookie；不作为登录凭据"}
operation('/auth/invitations/validate', 'post', 'validate_invite', '校验邀请而不消费', '账号', result='InviteTokenValidity', body='TokenInput', preauth=True, errors=[400])
for path, oid, label, body, code in [('/auth/register','register','消费邀请并注册登录','RegisterInput',201),
                                   ('/auth/login','login','登录并轮换会话','LoginInput',200)]:
    d = operation(path,'post',oid,label,'账号',result='LoginResult',body=body,status=code,preauth=True,errors=[401,409])
    d['responses'][str(code)]['headers']['Set-Cookie'] = {"schema": string(),
        "description": "__Host-omniflow_session；HttpOnly; Secure; SameSite=Lax; Path=/；无Domain属性"}
operation('/auth/me','get','get_me','读取当前用户','账号',result='User')
logout = operation('/auth/logout','post','logout','撤销当前登录并清理Cookie','账号',status=204)
logout['responses']['204']['headers']['Set-Cookie'] = {"schema": string(), "description": "清除登录Cookie；不清除agy授权"}
operation('/auth/password-resets/validate','post','validate_password_reset','校验重置链接而不消费','账号',body='TokenInput',result='TokenValidity',preauth=True)
operation('/auth/password-resets/complete','post','complete_password_reset','消费重置凭据、改密并撤销全部旧登录','账号',body='PasswordResetInput',status=204,preauth=True,
          description='成功后重新登录，不返回密码或自动登录；链接用途严格区分，错误统一TOKEN_INVALID。')
operation('/capabilities','get','get_capabilities','查询实际模型能力、可用性与安全上限','系统',result='Capabilities')
operation('/conversations','post','create_conversation','创建全新的独立创作对话','对话',body='ConversationCreate',result='Conversation',status=201,idem=True,
          description='不接受CLI ID。CLI可首次发消息才启动；绝不继承旧对话上下文。')
operation('/conversations','get','list_conversations','列出自己的对话','对话',result='ConversationPage',page=True)
operation('/conversations/{conversation_id}','get','get_conversation','读取自己的对话元数据','对话',result='Conversation')
operation('/conversations/{conversation_id}','patch','rename_conversation','修改对话标题','对话',body='ConversationPatch',result='Conversation')
operation('/conversations/{conversation_id}','delete','delete_conversation','删除对话但不连带删除作品','对话',status=204,errors=[409],
          description='有未结束run或非终态task时409 RESOURCE_IN_USE。删除网站记录不等于即时清除第三方留存。')
operation('/conversations/{conversation_id}/snapshot','get','get_conversation_snapshot','取得同一数据库快照下的最近消息与状态','对话',result='ConversationSnapshot',
          description='最近50条消息按seq升序，附更早消息游标；包含非终态run/task及所需作品版本，last_event_id与这些数据同一读事务产生。')
operation('/conversations/{conversation_id}/messages','get','list_messages','分页读取对话消息','对话',result='MessagePage',page=True,
          description='每页按seq升序显示；cursor指向更早一页。无cursor时返回最近limit条。')
operation('/conversations/{conversation_id}/messages','post','create_message','保存消息并排队一个主控轮次','对话',body='MessageCreate',result='MessageAccepted',status=202,idem=True,errors=[409],
          description='内容与附件不能同时为空；同一对话run串行，后发消息可排队。客户端不能上传assistant/system历史、用户ID、CLI ID或任意模型。')
operation('/conversations/{conversation_id}/runs','get','list_runs','查询对话轮次含待核对状态','对话',result='RunPage',page=True)
operation('/runs/{run_id}','get','get_run','查询自己的单个主控轮次','对话',result='Run')
operation('/runs/{run_id}/cancel','post','cancel_run','请求停止一个文字轮次而非取消媒体','对话',result='Run',status=202,errors=[409],
          description='queued可直接canceled；running先stopping。已经受理的媒体task继续，完成后的run返回409。重复停止请求返回当前状态。')
operation('/conversations/{conversation_id}/reference-confirmations','post','confirm_reference','明确确认图片版本用于本对话首帧视频','作品',
          body='ReferenceConfirmationCreate',result='ReferenceConfirmation',status=201,idem=True,errors=[409],
          description='必须是自有可用图片版本。此动作是用户确认事实，不向MCP开放，模型不能自造确认。')
sse = operation('/conversations/{conversation_id}/events','get','stream_events','订阅本对话持久事件流','事件',
    result=string(), media='text/event-stream', parameters=['LastEventId','AfterEventId'],errors=[410],
    description='只读订阅，不启动run或任务。Last-Event-ID优先于query游标；必须使用应用事件，不透传CLI日志。过期游标在开始流前返回410。')
sse['responses']['200']['headers'].update({'X-Accel-Buffering': {'schema': string(enum=['no'])}})
sse['x-event-schemas'] = {**{k: ref('Event'+''.join(x.title() for x in k.split('.'))) for k in event_data}, 'control': ref('StreamControl')}

upload = operation('/uploads','post','upload_image','上传图片并建立不可变版本','作品',body='UploadInput',result='ArtifactCreated',status=201,idem=True,errors=[413,415,422,507],
    description='multipart/form-data；只接收可解码JPEG/PNG/WebP，限额见capabilities。幂等摘要使用文件字节及语义字段，不含multipart边界。')
upload['requestBody']['content'] = {'multipart/form-data': {'schema': ref('UploadInput')}}
operation('/tasks','post','create_task','通过统一服务创建一个媒体任务','任务',body='TaskCreate',result='Task',status=202,idem=True,errors=[409,422,503,507],
          description='每task一份媒体。精修入口显式提交，Gemini经内部受控网关调用同一任务服务；参数和确认事实必须由后端校验。')
operation('/tasks','get','list_tasks','查询自己的媒体任务','任务',result='TaskPage',page=True,parameters=['ConversationFilter',
    {'name':'status','in':'query','required':False,'schema':TASK_STATUS}])
operation('/tasks/{task_id}','get','get_task','查询真实媒体状态','任务',result='Task')
operation('/tasks/{task_id}/cancel','post','cancel_task','仅取消尚未提交的媒体任务','任务',result='Task',errors=[409],
          description='只允许queued→canceled，与worker领取原子互斥；其他非可取消状态409 TASK_NOT_CANCELABLE，绝不伪称供应商已撤销。')
operation('/tasks/{task_id}/recover','post','recover_task','仅续查或重新下载同一供应商结果','任务',result='Task',status=202,errors=[409],
          description='不新建task、不再次生成。有可核对供应商任务ID/结果线索才可恢复；提交未知且无线索409 RECONCILIATION_REQUIRED。')
operation('/artifacts','get','list_artifacts','列出自己的作品，不向模型默认注入其他会话作品','作品',result='ArtifactPage',page=True,
          parameters=['ConversationFilter',{'name':'kind','in':'query','required':False,'schema':KIND}])
operation('/artifacts','post','create_text_artifact','将文案另存为作品','作品',body='TextArtifactCreate',result='ArtifactCreated',status=201,idem=True,errors=[507],
          description='不调用模型，content存为受保护text/plain版本。')
operation('/artifacts/{artifact_id}','get','get_artifact','读取作品及当前版本标识','作品',result='Artifact')
operation('/artifacts/{artifact_id}','delete','delete_artifact','撤销作品及所有版本访问并排队物理清理','作品',result='DeletionReceipt',status=202,errors=[409],
          description='任务占用时409 RESOURCE_IN_USE；已生成的派生视频不连带删除。不承诺撤回已下载或已送达提供方的副本。')
operation('/artifacts/{artifact_id}/versions','get','list_artifact_versions','分页读取作品历史版本','作品',result='ArtifactVersionPage',page=True)
operation('/artifacts/{artifact_id}/text-versions','post','create_text_version','保存文案修改为新版本','作品',body='TextVersionCreate',result='ArtifactCreated',status=201,idem=True,errors=[409,507],
          description='仅text作品；base_version_id必须仍为当前版本。图片和视频版本由统一媒体任务产生。')
operation('/artifacts/{artifact_id}/versions/{version_id}','get','get_artifact_version','读取实际作品文件元数据','作品',result='ArtifactVersion',
          description='同时验证owner及version属于路径中的artifact；返回实际尺寸/时长，不把请求参数当作成片规格。')
content_path = '/artifacts/{artifact_id}/versions/{version_id}/content'
content = operation(content_path,'get','get_artifact_content','受保护文件读取、播放及下载','作品',
    result=string(format='binary'),media='application/octet-stream',parameters=['Range','Download'],errors=[416])
content['responses']['200']['content'] = {x: {'schema': string(format='binary')} for x in
    ['image/png','image/jpeg','image/webp','video/mp4','text/plain']}
content['responses']['206'] = response('单区间部分内容')
content['responses']['206']['content'] = content['responses']['200']['content']
for code in ['200','206']:
    content['responses'][code]['headers'].update({
        'Accept-Ranges': {'schema': string(enum=['bytes'])}, 'Content-Length': {'schema': INT},
        'Content-Disposition': {'schema': string()}, 'X-Content-Type-Options': {'schema': string(enum=['nosniff'])}})
content['responses']['206']['headers']['Content-Range'] = {'schema':string()}
head = operation(content_path,'head','head_artifact_content','鉴权后读取文件头，不返回响应体','作品',parameters=['Download'])
head['responses']['200']['headers'].update({'Content-Length': {'schema':INT},'Content-Type': {'schema':string()},
                                           'Accept-Ranges': {'schema':string(enum=['bytes'])}})

grant_security = [{'MediaGrantToken': []}]
grant_path = '/media-grants/{grant_id}/content'
grant = operation(grant_path,'get','get_provider_reference','供应商按短期授权读取唯一参考图片','供应商取图',
    result=string(format='binary'),media='application/octet-stream',security=grant_security,errors=[404],
    description='不需要网站Cookie；只允许后端签发、绑定具体版本和任务的token。失效/撤销/错误凭据统一404，不接受任意路径，不公开整个素材目录。')
grant['responses']['200']['content'] = {x: {'schema':string(format='binary')} for x in ['image/png','image/jpeg','image/webp']}
operation(grant_path,'head','head_provider_reference','供应商验证参考图片文件头','供应商取图',
    security=grant_security,errors=[404],description='与GET使用同一短期授权，不返回响应体；token及完整URL必须从访问日志中脱敏。')

operation('/admin/invitations','get','admin_list_invites','列出邀请元数据，不返回历史凭据','管理',result='InvitePage',page=True,admin=True)
operation('/admin/invitations','post','admin_create_invite','签发一次性邀请，完整链接仅返回本次','管理',body='InviteCreate',result='InviteIssued',status=201,admin=True,
          description='不记录完整链接。失败不能自动重试发新邀请；通过列表核对或撤销后重新签发。')
operation('/admin/invitations/{invitation_id}/revoke','post','admin_revoke_invite','撤销未消费邀请','管理',result='Invite',admin=True,errors=[409])
operation('/admin/users','get','admin_list_users','查询账号元数据','管理',result='UserPage',page=True,admin=True)
operation('/admin/users/{user_id}','patch','admin_set_user_status','停用或启用账号','管理',body='UserStatusPatch',result='User',admin=True,errors=[409],
          description='不能停用唯一管理员。停用撤销登录与媒体grant、拒绝新增及未提交执行；已受理任务仅继续核对终态，不删除作品。')
operation('/admin/users/{user_id}/password-resets','post','admin_issue_password_reset','人工核验身份后签发重置链接','管理',body='PasswordResetIssue',result='PasswordResetIssued',status=201,admin=True,
          description='服务端验证管理员，不以请求中的核验方式代替真实人工核验。不能查看旧密码；URL仅本次返回，重发使该用户旧重置凭据失效。')
operation('/admin/tasks','get','admin_list_operational_tasks','查看不含私密内容的任务故障概况','管理',result='OperationalTaskPage',page=True,admin=True,
          parameters=[{'name':'status','in':'query','required':False,'schema':TASK_STATUS}])
operation('/admin/generation-policy','get','admin_get_generation_policy','读取本地暂停开关','管理',result='GenerationPolicy',admin=True)
operation('/admin/generation-policy','patch','admin_patch_generation_policy','暂停或恢复接收新请求','管理',body='GenerationPolicyPatch',result='GenerationPolicy',admin=True,
          description='关闭后拒绝新入队并暂停尚未提交的同类工作；已由供应商受理的继续收尾。恢复本地开关不覆盖供应商限额或费用保护，不允许设置key、模型、额度或收费回退。')

# 完整、虚构的示例供文档校验；不使用真实对话、账号或密钥。
C='11111111-1111-4111-8111-111111111111'
U='22222222-2222-4222-8222-222222222222'
M='33333333-3333-4333-8333-333333333333'
R='44444444-4444-4444-8444-444444444444'
T='55555555-5555-4555-8555-555555555555'
A='66666666-6666-4666-8666-666666666666'
V='77777777-7777-4777-8777-777777777777'
F='88888888-8888-4888-8888-888888888888'
NOW='2026-09-08T08:00:00Z'
EXAMPLES['ConversationCreate']={'title':'香水创作测试'}
EXAMPLES['Conversation']={'id':C,'title':'香水创作测试','created_at':NOW,'updated_at':NOW,'last_event_id':'0','active_run_id':None}
EXAMPLES['MessageCreate']={'client_message_id':M,'content':'请根据已确认图片制作视频，固定镜头，让丝带轻轻飘动。',
    'selected_version_id':V,'reference_confirmation_id':F,'generation_permission':'requested_only'}
EXAMPLES['Message']={'id':M,'conversation_id':C,'seq':1,'role':'user','content':EXAMPLES['MessageCreate']['content'],
    'status':'completed','run_id':R,'client_message_id':M,'attachment_version_ids':[],'selected_version_id':V,'artifact_version_ids':[], 'created_at':NOW,'updated_at':NOW}
EXAMPLES['Run']={'id':R,'conversation_id':C,'user_message_id':M,'assistant_message_id':None,'status':'queued',
    'task_ids':[],'error':None,'created_at':NOW,'updated_at':NOW}
EXAMPLES['MessageAccepted']={'message':EXAMPLES['Message'],'run':EXAMPLES['Run']}
EXAMPLES['ReferenceConfirmationCreate']={'version_id':V,'purpose':'video_first_frame'}
EXAMPLES['ReferenceConfirmation']={'id':F,'conversation_id':C,'version_id':V,'purpose':'video_first_frame','created_at':NOW}
EXAMPLES['ImageTaskCreate']={'conversation_id':C,'kind':'image','prompt':'冷白背景的虚构透明香水瓶，银色瓶盖与淡蓝丝带，无品牌文字。',
    'aspect_ratio':'9:16','size_tier':'1K','reference_version_ids':[]}
EXAMPLES['VideoTaskCreate']={'conversation_id':C,'kind':'ai_video','mode':'keyframe','reference_confirmation_id':F,
    'prompt':'保持参考图瓶体和文字不变，固定镜头，仅让丝带末端轻轻摆动。','seconds':4,'size_tier':'720P','aspect_ratio':'9:16'}
EXAMPLES['LocalMotionTaskCreate']={'conversation_id':C,'kind':'local_motion','image_version_id':V,
    'motion_type':'dolly_in','seconds':4,'aspect_ratio':'9:16'}
EXAMPLES['Task']={'id':T,'conversation_id':C,'run_id':None,'kind':'ai_video','status':'queued',
    'requested_parameters':EXAMPLES['VideoTaskCreate'],'execution_engine':'agnes-video-2.5-flash','output_version_ids':[],
    'can_cancel':True,'can_recover':False,'error':None,'created_at':NOW,'updated_at':NOW}
EXAMPLES['User']={'id':U,'username':'demo_creator','role':'user','status':'active','created_at':NOW}
EXAMPLES['Artifact']={'id':A,'kind':'image','title':'视觉确认稿','current_version_id':V,'version_count':1,'created_at':NOW,'updated_at':NOW}
EXAMPLES['ArtifactVersion']={'id':V,'artifact_id':A,'version_number':1,'parent_version_id':None,'source_task_id':None,'execution_engine':None,
    'media_type':'image/png','byte_size':972514,'sha256':'0'*64,'width':736,'height':1312,
    'duration_seconds':None,'fps':None,'created_at':NOW,'content_url':f'/api/v1/artifacts/{A}/versions/{V}/content'}
EXAMPLES['ArtifactCreated']={'artifact':EXAMPLES['Artifact'],'version':EXAMPLES['ArtifactVersion']}
EXAMPLES['ConversationSnapshot']={'conversation':EXAMPLES['Conversation'],'messages':[],'messages_next_cursor':None,
    'runs':[],'tasks':[],'artifact_versions':[],'last_event_id':'0'}
EXAMPLES['AuthPolicy']={'username_pattern':'^[a-z0-9_]{3,32}$','username_normalization':'trim_ascii_spaces_then_lowercase',
    'password_min_length':6,'password_max_length':128,'session_ttl_seconds':604800,
    'invitation_ttl_default_seconds':604800,'password_reset_ttl_default_seconds':1800}
EXAMPLES['Problem']={'type':'about:blank','title':'资源不可见','status':404,'code':'RESOURCE_NOT_FOUND',
    'detail':'资源不存在或当前用户不可访问。','request_id':U,'retryable':False}
EXAMPLES['EventTaskUpdated']={'event_id':'12','conversation_id':C,'type':'task.updated','occurred_at':NOW,'data':EXAMPLES['Task']}
EXAMPLES['TextArtifactCreate']={'kind':'text','title':'产品文案','content':'这是用于契约校验的虚构文案。','conversation_id':C}
EXAMPLES['TextVersionCreate']={'base_version_id':V,'content':'这是修改后的虚构文案。'}
for name, example in EXAMPLES.items():
    S[name]['examples']=[example]

DOC = {'openapi':'3.1.0', 'info': {'title':'OmniFlow FastAPI 接口设计', 'version':'1.0.0-draft',
    'description':'设计契约第一版，与Spec v1.0配套。尚未部署、不是从现有server.py或运行中的FastAPI生成；不得用文档成功代替真实接口验收。'},
    'jsonSchemaDialect':'https://json-schema.org/draft/2020-12/schema',
    'servers':[{'url':'/api/v1','description':'待实现的相对前缀；不表示现有域名已提供这些接口'}],
    'tags':[{'name':x} for x in ['系统','账号','对话','事件','任务','作品','供应商取图','管理']],
    'security':COOKIE, 'paths':PATHS,
    'components':{'schemas':S,'parameters':PARAMS,'responses':RESPONSES,'securitySchemes':{
        'SessionCookie':{'type':'apiKey','in':'cookie','name':'__Host-omniflow_session','description':'网站登录，不是Google/Agnes凭据'},
        'CsrfContextCookie':{'type':'apiKey','in':'cookie','name':'__Host-omniflow_csrf','description':'匿名表单上下文，不具有登录权限'},
        'CsrfHeader':{'type':'apiKey','in':'header','name':'X-CSRF-Token','description':'绑定Cookie上下文，所有状态修改请求必须验证'},
        'MediaGrantToken':{'type':'apiKey','in':'query','name':'token','description':'仅供应商取参考图片；后端生成短期高熵凭据，绑定grant_id、版本、任务和到期时间；不是网站或Google登录'},
    }}, 'x-spec-document':'../specs/创作工作台重构-Spec-v1.0.md',
    'x-no-business-quotas':True,'x-deployed':False}

if __name__ == '__main__':
    target=ROOT/'openapi-v1.json'
    target.write_text(json.dumps(DOC,ensure_ascii=False,indent=2)+'\n')
    lines = ['【FastAPI v1 路由索引】', '',
             '由 build_openapi.py 生成；以 /api/v1 为前缀，全部为待实现设计，不是现有线上接口。',
             '字段完整定义见 openapi-v1.json；行为、幂等和安全语义见 FastAPI接口文档-v1.md。', '']
    for tag in DOC['tags']:
        lines.extend(['▶ ' + tag['name'], ''])
        for path, methods in PATHS.items():
            for method, op in methods.items():
                if op['tags'][0] != tag['name']:
                    continue
                body_names = []
                for media, content in op.get('requestBody', {}).get('content', {}).items():
                    body_names.append(media + ' / ' + content['schema'].get('$ref', '内联schema').rsplit('/', 1)[-1])
                success = []
                for code, result in op['responses'].items():
                    if code.startswith('2'):
                        names = {v['schema'].get('$ref', '二进制或流').rsplit('/', 1)[-1]
                                 for v in result.get('content', {}).values()}
                        success.append(code + ' ' + ('/'.join(sorted(names)) if names else '无响应体'))
                is_admin = op.get('x-required-role') == 'admin'
                auth = '管理员' if is_admin else '供应商短期取图凭据' if op['security'] == grant_security else '匿名表单CSRF' if op['security'] == PREAUTH else '公开' if not op['security'] else '登录'
                if op['security'] == WRITE:
                    auth += '＋CSRF'
                idem = any(p.get('$ref', '').endswith('/IdempotencyKey') for p in op['parameters'])
                lines.extend([f"• {method.upper()} {path} — {op['summary']}",
                              f"  operationId：{op['operationId']}；鉴权：{auth}；幂等键：{'必须' if idem else '不要求'}。",
                              f"  请求：{'; '.join(body_names) if body_names else '无请求体'}；成功：{'; '.join(success)}。",
                              '  ' + op['description'], ''])
    (ROOT/'路由索引-v1.md').write_text('\n'.join(lines))
    print(f'已生成 {target.name} 与路由索引：{len(PATHS)} 个路径，{sum(len(x) for x in PATHS.values())} 个操作，{len(S)} 个schema；未启动服务。')
