"""实际 app.openapi 与独立设计全量比对；不通过拷贝设计到服务端伪造一致。"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "docs/api/openapi-v1.json").read_text()
)


def canonical(value, document):
    if isinstance(value, list):
        return sorted(
            (canonical(v, document) for v in value), key=lambda v: json.dumps(v, sort_keys=True)
        )
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        target = document
        for part in value["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return canonical({**target, **{k: v for k, v in value.items() if k != "$ref"}}, document)
    result = {
        k: (
            {name: canonical(item, document) for name, item in v.items()}
            if k in {"properties", "headers", "mapping"}
            else canonical(v, document)
        )
        for k, v in value.items()
        if k not in {"title", "description", "examples", "example", "default"}
    }
    if "const" in result:
        result["enum"] = [result.pop("const")]
    if "enum" in result:
        result.pop("type", None)  # enum 已限定值类型，消除生成器冗余差别。
    if result.get("required") == []:
        result.pop("required")
    return result


def expected(value):
    result = canonical(value, CONTRACT)

    def fixed_model(item):
        if isinstance(item, dict):
            if item.get("enum") == [
                "gemini-3.8-flash-high",
                "gemini-3.8-flash-low",
                "gemini-3.8-flash-medium",
            ]:
                item["enum"] = ["gemini-3.8-flash-low"]  # 唯一批准的契约收窄。
            for child in item.values():
                fixed_model(child)
        elif isinstance(item, list):
            for child in item:
                fixed_model(child)

    fixed_model(result)
    return result


@pytest.mark.parametrize("name", CONTRACT["components"]["schemas"])
def test_all_design_schemas_match_runtime(app, name):
    actual = app.openapi()
    runtime = canonical(actual["components"]["schemas"][name], actual)
    design = expected(CONTRACT["components"]["schemas"][name])
    if name == "Capabilities":
        # Spec 最新决定固定 low，不能为了 schema 相等重新放开 medium/high。
        assert runtime["properties"]["text"]["properties"]["model"] == {
            "enum": ["gemini-3.8-flash-low"]
        }
        design["properties"]["text"]["properties"]["model"] = {"enum": ["gemini-3.8-flash-low"]}
    assert runtime == design


OPERATIONS = [(p, m) for p, ops in CONTRACT["paths"].items() for m in ops]


@pytest.mark.parametrize("path,method", OPERATIONS)
def test_all_operations_requests_responses_and_security(app, path, method):
    actual = app.openapi()
    runtime = actual["paths"]["/api/v1" + path][method]
    design = CONTRACT["paths"][path][method]
    assert runtime["operationId"] == design["operationId"]
    assert runtime.get("security", []) == design.get("security", [])
    assert runtime.get("x-required-role") == design.get("x-required-role")
    assert canonical(runtime.get("parameters", []), actual) == expected(
        design.get("parameters", [])
    )
    assert canonical(runtime.get("requestBody"), actual) == expected(design.get("requestBody"))
    assert canonical(runtime.get("x-event-schemas"), actual) == expected(
        design.get("x-event-schemas")
    )
    # 运行时统一安全边界可能额外返回其他 Problem，不能缺设计响应或冒出别的成功状态。
    assert set(design["responses"]) <= set(runtime["responses"])
    assert {c for c in runtime["responses"] if c.startswith("2")} == {
        c for c in design["responses"] if c.startswith("2")
    }
    for code, response in design["responses"].items():
        want = expected(response)
        got = canonical(runtime["responses"][code], actual)
        assert got.get("content") == want.get("content"), (path, method, code)
        assert want.get("headers", {}).items() <= got.get("headers", {}).items()
    for code, response in runtime["responses"].items():
        if int(code) >= 400:
            if method == "head":
                assert "content" not in response
            else:
                assert set(response["content"]) == {"application/problem+json"}


def test_no_extra_business_routes_or_unresolved_references(app):
    actual = app.openapi()
    assert {
        (p.removeprefix("/api/v1"), m) for p, ops in actual["paths"].items() for m in ops
    } == set(OPERATIONS)
    canonical(actual, actual)  # 每个内部引用必须能解析。
    assert canonical(actual["components"]["securitySchemes"], actual) == expected(
        CONTRACT["components"]["securitySchemes"]
    )


@pytest.mark.parametrize(
    "name",
    [
        "MessageCreate",
        "ImageTaskCreate",
        "VideoTaskCreate",
        "LocalMotionTaskCreate",
        "TextArtifactCreate",
        "TextVersionCreate",
    ],
)
def test_runtime_schema_rejects_explicit_null_and_extra_fields(app, name):
    actual = app.openapi()
    model = actual["components"]["schemas"][name]
    validator = Draft202012Validator(canonical(model, actual), format_checker=FormatChecker())
    for sample in CONTRACT["components"]["schemas"][name].get("examples", []):
        validator.validate(sample)
        assert not validator.is_valid({**sample, "owner_id": "forbidden"})
        for field in model["properties"]:
            if field not in model.get("required", []):
                assert not validator.is_valid({**sample, field: None})
