"""补齐运行时校验器及 SSE 的机器可读声明；不读取或覆盖设计契约。"""

from copy import deepcopy

from .conversation_models import EVENT_DATA, MessageDelta


def complete_schema(schema):
    models = schema["components"]["schemas"]
    # 以下约束已经由请求模型的 model_validator 执行，Pydantic 不会自动导出。
    for name in (
        "MessageCreate",
        "ImageTaskCreate",
        "VideoTaskCreate",
        "LocalMotionTaskCreate",
        "TextArtifactCreate",
        "TextVersionCreate",
    ):
        model = models[name]
        for field in model["properties"].values():
            branches = field.get("anyOf", [])
            if any(branch == {"type": "null"} for branch in branches):
                non_null = [branch for branch in branches if branch != {"type": "null"}]
                if len(non_null) == 1:
                    field.pop("anyOf")
                    field.pop("default", None)
                    field.update(non_null[0])
        if name.endswith("TaskCreate"):
            model["dependentRequired"] = {
                "target_artifact_id": ["base_version_id"],
                "base_version_id": ["target_artifact_id"],
            }
    models["MessageCreate"]["anyOf"] = [
        {"properties": {"content": {"minLength": 1}}, "required": ["content"]},
        {
            "properties": {"attachment_version_ids": {"minItems": 1}},
            "required": ["attachment_version_ids"],
        },
    ]
    models["VideoTaskCreate"]["allOf"] = [
        {
            "if": {"properties": {"mode": {"const": "keyframe"}}, "required": ["mode"]},
            "then": {"required": ["reference_confirmation_id"]},
            "else": {"not": {"required": ["reference_confirmation_id"]}},
        }
    ]
    # Problem 始终显式序列化这两个默认值，并排除 errors=None。
    problem = models["Problem"]
    problem["required"] = list(dict.fromkeys([*problem["required"], "type", "retryable"]))
    problem["properties"]["type"]["format"] = "uri-reference"
    problem["properties"]["errors"] = {
        "type": "array",
        "items": {"$ref": "#/components/schemas/FieldError"},
    }
    models["ArtifactVersion"]["properties"]["content_url"]["format"] = "uri-reference"
    models["TaskCreate"] = deepcopy(models["Task"]["properties"]["requested_parameters"])
    models["MessageDelta"] = MessageDelta.model_json_schema()
    event_refs = {}
    for event, data in EVENT_DATA.items():
        name = "Event" + "".join(part.capitalize() for part in event.split("."))
        models[name] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "event_id": {"type": "string", "pattern": r"^(0|[1-9][0-9]*)$"},
                "conversation_id": {"type": "string", "format": "uuid"},
                "type": {"const": event},
                "occurred_at": {"type": "string", "format": "date-time"},
                "data": {"$ref": f"#/components/schemas/{data.__name__}"},
            },
            "required": ["event_id", "conversation_id", "type", "occurred_at", "data"],
        }
        event_refs[event] = {"$ref": f"#/components/schemas/{name}"}
    models["ConversationEvent"] = {"oneOf": list(event_refs.values())}
    models["StreamControl"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "code": {
                "type": "string",
                "enum": [
                    "AUTH_REQUIRED",
                    "EVENT_CURSOR_EXPIRED",
                    "RESOURCE_NOT_FOUND",
                    "SERVICE_RESTARTING",
                ],
            },
            "message": {"type": "string"},
        },
        "required": ["code", "message"],
    }
    event_refs["control"] = {"$ref": "#/components/schemas/StreamControl"}
    stream = schema["paths"]["/api/v1/conversations/{conversation_id}/events"]["get"]
    stream["x-event-schemas"] = event_refs
    stream["responses"]["200"].setdefault("headers", {})["X-Accel-Buffering"] = {
        "schema": {"enum": ["no"]}
    }
    for path, operations in schema["paths"].items():
        for operation in operations.values():
            if path.startswith("/api/v1/admin/"):
                operation["x-required-role"] = "admin"
            for response in operation.get("responses", {}).values():
                response.setdefault("headers", {})["X-Content-Type-Options"] = {
                    "schema": {"enum": ["nosniff"]}
                }
            # query/header 的可选表示省略，不存在 JSON null 的传输形式。
            for parameter in operation.get("parameters", []):
                field = parameter["schema"]
                if parameter["name"] in ("Last-Event-ID", "after_event_id"):
                    field["pattern"] = r"^(0|[1-9][0-9]*)$"  # 服务层返回稳定 400 而非 422。
                branches = field.get("anyOf", [])
                non_null = [branch for branch in branches if branch != {"type": "null"}]
                if len(branches) == 2 and len(non_null) == 1:
                    field.pop("anyOf")
                    field.pop("default", None)
                    field.update(non_null[0])
    return schema
