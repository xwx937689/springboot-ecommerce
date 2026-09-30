"""Phase 5 - 规则基线用例生成（确定性，免 LLM）。

企业实践：AI 生成前先有确定性基线，保证覆盖下限、可在无 Key 的 CI 里跑；
AI 再在其上补充业务语义用例（边界/组合/越权等），两者合并去重。

v2 改进（依据 Phase 6 基线报告 artifacts/baseline-phase6-20260929/ANALYSIS.md）：
  - 请求体基于契约 components.schemas 真实字段构造（不再 name 兜底）
  - auth 用例携带合法 body（系统校验顺序：body 校验先于鉴权，否则测到 400 而非 401）
  - negative 用例 = 缺失必填字段 -> 400
"""
from __future__ import annotations

from parser.models import AuthType, Endpoint, ParameterLocation

from .models import (
    CaseCategory,
    CaseSource,
    ExpectedResult,
    RequestSpec,
    TestCase,
)

# 常见字段语义样例（命中业务含义，比类型兜底更真实）
_SAMPLE_VALUES: dict[str, object] = {
    "email": "buyer1@example.com",
    "password": "password123",
    "refreshtoken": "sample-refresh-token",
    "token": "sample-refresh-token",
    "oldpassword": "password123",
    "newpassword": "password123",
    "quantity": 1,
    "price": 10.0,
    "amount": 10.0,
    "page": 0,
    "size": 10,
    "role": "USER",
    "name": "sample-name",
    "title": "sample-title",
    "description": "sample-description",
    "city": "Istanbul",
    "country": "TR",
    "line1": "sample street 1",
    "isdefault": True,
    "enabled": True,
}


def _pad(value: str, spec: dict) -> str:
    """满足 minLength/maxLength 约束。"""
    min_len = spec.get("minLength")
    if isinstance(min_len, int) and min_len > len(value):
        value = (value + "-" + "x" * min_len)[: max(min_len, len(value))]
    max_len = spec.get("maxLength")
    if isinstance(max_len, int) and len(value) > max_len:
        value = value[:max_len]
    return value


def _sample_prop(name: str, spec: dict, schemas: dict | None = None, depth: int = 0) -> object:
    """按字段名语义 + 类型 + 约束构造合法样例值。"""
    spec = spec or {}
    schemas = schemas or {}
    if spec.get("enum"):
        return spec["enum"][0]
    # 嵌套对象引用（如 card -> CardDetails）：递归构造
    if spec.get("$ref") or spec.get("type") == "object":
        resolved = _resolve_ref(spec, schemas) if spec.get("$ref") else spec
        if depth < 3 and isinstance(resolved, dict) and resolved.get("properties"):
            return _object_from_schema(resolved, schemas, depth)
        return {}

    key = name.lower().replace("_", "").replace("-", "")
    t = (spec.get("type") or "string").lower()

    if t == "array":
        return [_sample_prop(name, spec.get("items") or {}, schemas, depth)]
    if t == "boolean":
        return bool(_SAMPLE_VALUES.get(key, True))
    if t in ("integer", "number"):
        if key in _SAMPLE_VALUES:
            return _SAMPLE_VALUES[key]
        return 1 if t == "integer" else 10.0

    # string
    if key in _SAMPLE_VALUES and isinstance(_SAMPLE_VALUES[key], str):
        return _pad(str(_SAMPLE_VALUES[key]), spec)
    value = _SAMPLE_VALUES.get(key, f"sample-{name}")
    return _pad(str(value), spec)


def _object_from_schema(schema: dict, schemas: dict, depth: int = 0) -> dict:
    out: dict = {}
    for name, prop in (schema.get("properties") or {}).items():
        out[name] = _sample_prop(name, prop if isinstance(prop, dict) else {}, schemas, depth + 1)
    return out


def _resolve_ref(schema: dict | None, schemas: dict) -> dict | None:
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if ref:
        return schemas.get(ref.rsplit("/", 1)[-1]) or {}
    return schema


def build_body(schema_ref: str | None, schemas: dict, *, omit_required: bool = False) -> dict | None:
    """按 schema 构造请求体。

    omit_required=True 构造"校验必失败"的 body：缺失部分必填字段；
    若字段全为必填（缺失后会变成空对象），则将全部字段置 None（触发 @NotNull -> 400）。
    """
    schema = _resolve_ref({"$ref": schema_ref} if schema_ref else None, schemas)
    if not isinstance(schema, dict):
        return None
    props: dict = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if not props:
        return None

    if omit_required:
        optional = {n: s for n, s in props.items() if n not in required}
        if optional:
            return {
                n: _sample_prop(n, s if isinstance(s, dict) else {}, schemas)
                for n, s in optional.items()
            }
        # 全必填 schema：必填字段全部置 None，触发 @NotNull 校验失败
        return {n: None for n in props}

    return {
        n: _sample_prop(n, s if isinstance(s, dict) else {}, schemas)
        for n, s in props.items()
    }


def _query_sample(p) -> object:
    """query 参数采样：数组类型转 CSV 字符串（Spring 标准绑定 ids=1,2）。"""
    if (p.type or "").lower() == "array":
        return "1,2"
    v = _sample_prop(p.name, {"type": p.type})
    if isinstance(v, list):
        return ",".join(str(x) for x in v)
    return v


def _render_path(endpoint: Endpoint, *, not_found: bool = False) -> dict:
    params: dict[str, object] = {}
    for p in endpoint.parameters:
        if p.location == ParameterLocation.PATH:
            v = 999999999 if not_found else _sample_prop(p.name, {"type": p.type})
            if isinstance(v, list):
                v = ",".join(str(x) for x in v)
            params[p.name] = v
    return params


def generate_rule_cases(endpoint: Endpoint, schemas: dict | None = None) -> list[TestCase]:
    schemas = schemas or {}
    cases: list[TestCase] = []
    op = endpoint.operation_id or "op"
    req_base = {
        "method": endpoint.method.value,
        "path_template": endpoint.path,
        "auth": "jwt" if endpoint.auth == AuthType.JWT else "none",
    }
    identity = {"service": endpoint.service, "endpoint_key": endpoint.key}

    schema_ref = endpoint.request_body.schema_ref if endpoint.request_body else None
    body_valid = build_body(schema_ref, schemas)
    body_invalid = build_body(schema_ref, schemas, omit_required=True)

    # 1) 正向
    cases.append(TestCase(
        **identity,
        id=f"{endpoint.service}__{op}__positive-01",
        name=f"{op} 正向请求",
        category=CaseCategory.POSITIVE,
        description=f"按 schema 构造合法数据调用 {endpoint.key}",
        request=RequestSpec(
            **req_base,
            path_params=_render_path(endpoint),
            query_params={
                p.name: _query_sample(p)
                for p in endpoint.parameters
                if p.location == ParameterLocation.QUERY
            },
            body=body_valid,
        ),
        expected=ExpectedResult(status_class="2xx"),
        tags=["baseline", endpoint.tag or ""],
    ))

    # 2) 鉴权：JWT 接口 + 合法 body + 不带凭证 -> 401
    if endpoint.auth == AuthType.JWT:
        cases.append(TestCase(
            **identity,
            id=f"{endpoint.service}__{op}__auth-01",
            name=f"{op} 无凭证应401",
            category=CaseCategory.AUTH,
            description="携带合法请求体但不携带凭证，验证鉴权拦截（该系统 body 校验先于鉴权，故必须给合法 body）",
            request=RequestSpec(**req_base, path_params=_render_path(endpoint), body=body_valid),
            expected=ExpectedResult(status_codes=[401]),
            tags=["baseline", "auth"],
        ))

    # 3) 负向：缺失必填字段 -> 400
    if endpoint.request_body is not None and body_invalid is not None:
        cases.append(TestCase(
            **identity,
            id=f"{endpoint.service}__{op}__negative-01",
            name=f"{op} 缺失必填字段应400",
            category=CaseCategory.NEGATIVE,
            description="缺失全部必填字段，验证参数校验逻辑",
            request=RequestSpec(**req_base, path_params=_render_path(endpoint), body=body_invalid),
            expected=ExpectedResult(status_class="4xx"),
            tags=["baseline", "negative"],
        ))

    # 4) not_found：路径参数给不存在值（合法 body）
    if any(p.location == ParameterLocation.PATH for p in endpoint.parameters):
        cases.append(TestCase(
            **identity,
            id=f"{endpoint.service}__{op}__notfound-01",
            name=f"{op} 不存在资源",
            category=CaseCategory.NOT_FOUND,
            description="路径参数使用极大不存在 ID，校验 404/业务包装响应",
            request=RequestSpec(
                **req_base,
                path_params=_render_path(endpoint, not_found=True),
                body=body_valid,
            ),
            expected=ExpectedResult(status_codes=[404, 200, 400]),
            tags=["baseline", "not-found"],
        ))

    return cases
