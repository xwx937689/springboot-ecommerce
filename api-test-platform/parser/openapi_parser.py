"""Phase 4 - OpenAPI JSON -> 统一接口模型。

真实可运行：读取 N 份 /v3/api-docs JSON，解析为 ServiceContract / Endpoint。
鉴权推断规则（基于本电商项目实际）：
  - 路径在网关白名单（/v3/api-docs, /swagger-ui, /actuator/health,
    /api/auth/login, /api/auth/refresh, /api/products, /api/products/**,
    /api/recommendations/** 等公开端点）-> AuthType.NONE
  - 其余 -> AuthType.JWT
白名单可外部覆盖（config）。
"""
from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path

from .models import (
    AuthType,
    Endpoint,
    HttpMethod,
    Parameter,
    ParameterLocation,
    RequestBody,
    ResponseSpec,
    ServiceContract,
)

# 网关白名单（与 GatewayJwtAuthenticationFilter 保持一致，可扩展）
PUBLIC_PATH_PREFIXES = (
    "/v3/api-docs",
    "/swagger-ui",
    "/actuator",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/products",
    "/api/recommendations",
    "/api/sellers",
)

# 服务级安全豁免（Phase 6 基线实测）：以下服务未引入 spring-security，
# 自身不做鉴权（仅靠网关兜底），直连端口调用时任何路径都是开放的。
# 证据：pom 无 security 依赖；api-docs 无凭证可访问（200/500 而非 401）。
OPEN_SERVICES = {
    "payment-service",
    "inventory-service",
    "recommendation-service",
    "notification-service",
}


def _is_public(path: str) -> bool:
    return any(path.startswith(p) or path == p for p in PUBLIC_PATH_PREFIXES)


def _infer_auth(path: str, service: str = "") -> AuthType:
    if service in OPEN_SERVICES:
        return AuthType.NONE
    return AuthType.NONE if _is_public(path) else AuthType.JWT


def _method_from_str(s: str) -> HttpMethod:
    return HttpMethod[s.upper()]


def _param_location(s: str) -> ParameterLocation:
    try:
        # 按枚举"值"查找（query/path/header/cookie）；Enum[...] 是按"名"查找且区分大小写
        return ParameterLocation(s.lower())
    except ValueError:
        return ParameterLocation.QUERY


def parse_file(path: Path, service_hint: str | None = None) -> ServiceContract:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    info = raw.get("info", {})
    service = service_hint or info.get("title", Path(path).stem)
    base_path = raw.get("servers", [{}])[0].get("url", "").rstrip("/")

    endpoints: list[Endpoint] = []
    paths = raw.get("paths", {})
    for path, methods in paths.items():
        for http_method, op in methods.items():
            if http_method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            op = op or {}
            params: list[Parameter] = []
            for p in op.get("parameters", []):
                params.append(
                    Parameter(
                        name=p.get("name", ""),
                        location=_param_location(p.get("in", "query")),
                        required=bool(p.get("required", False)),
                        type=(p.get("schema", {}) or {}).get("type", "string"),
                        description=p.get("description"),
                    )
                )
            rb = None
            rb_raw = op.get("requestBody")
            if rb_raw:
                content = rb_raw.get("content", {})
                ct = next(iter(content), "application/json")
                schema_ref = None
                example = None
                if content.get(ct, {}).get("schema"):
                    schema_ref = content[ct]["schema"].get("$ref")
                    example = content[ct]["schema"].get("example")
                rb = RequestBody(
                    required=bool(rb_raw.get("required", False)),
                    content_type=ct,
                    schema_ref=schema_ref,
                    example=example,
                )
            responses = [
                ResponseSpec(
                    status_code=int(str(code).replace("XX", "00")),
                    description=r.get("description"),
                    schema_ref=(r.get("content", {})
                                .get("application/json", {})
                                .get("schema", {}).get("$ref")),
                )
                for code, r in (op.get("responses", {}) or {}).items()
            ]
            tags = op.get("tags", ["default"])
            endpoints.append(
                Endpoint(
                    service=service,
                    tag=tags[0],
                    operation_id=op.get("operationId"),
                    method=_method_from_str(http_method),
                    path=path,
                    summary=op.get("summary") or op.get("description"),
                    auth=_infer_auth(path, service),
                    parameters=params,
                    request_body=rb,
                    responses=responses,
                )
            )

    return ServiceContract(
        service=service,
        title=info.get("title", service),
        version=info.get("version", "0.0.0"),
        base_path=base_path,
        endpoints=endpoints,
        schemas=raw.get("components", {}).get("schemas", {}) or {},
    )


def parse_dir(contracts_dir: Path, mapping: dict[str, str] | None = None) -> list[ServiceContract]:
    """解析目录下所有 *-service.json。mapping: 文件名 -> 服务名。

    无效契约（空文件/非 JSON/非 OpenAPI 结构，如无 Web 端点的服务）跳过并告警，
    不阻断整体解析——真实平台必须容忍部分被测系统契约缺失。
    """
    import logging

    logger = logging.getLogger(__name__)
    mapping = mapping or {}
    contracts: list[ServiceContract] = []
    for f in sorted(contracts_dir.glob("*.json")):
        hint = mapping.get(f.stem)
        try:
            contracts.append(parse_file(f, hint))
        except (JSONDecodeError, ValueError, KeyError) as exc:
            size = f.stat().st_size if f.exists() else 0
            logger.warning("跳过无效契约 %s (size=%sB): %s", f.name, size, exc)
    return contracts
