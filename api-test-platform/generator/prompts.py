"""Phase 5 - 提示词构造。

原则：
  1) 给 LLM 的上下文必须是 Phase 4 的统一模型（Endpoint），不暴露原始 OpenAPI 细节；
  2) 输出强制 JSON 数组，schema 与 generator.models.TestCase 对齐，便于程序校验；
  3) 明确业务背景（电商微服务），并声明"只输出 JSON"以抑制废话。
"""
from __future__ import annotations

import json

from parser.models import Endpoint

SYSTEM_PROMPT = (
    "你是资深接口自动化测试工程师，正在为一套 Spring Boot 微服务电商系统"
    "（用户/商品/购物车/库存/支付/订单/通知/推荐/卖家 服务，JWT 鉴权，统一 ApiResponse 包装）设计 API 测试用例。"
    "你的输出必须是一个纯 JSON 数组，不要任何解释、markdown 代码块或多余文本。"
)

# 与 generator.models.TestCase 对齐的输出 schema（LLM 只填红框字段）
CASE_JSON_SHAPE = {
    "name": "用例短名（中文，动词开头）",
    "category": "positive|negative|boundary|auth|not_found|conflict",
    "description": "一句话说明验证点",
    "request": {
        "method": "与输入一致",
        "path_template": "与输入一致（保留 {param} 占位）",
        "path_params": {"参数名": "值"},
        "query_params": {"参数名": "值"},
        "headers": {"Header-Name": "value"},
        "body": {"字段": "值"},
        "auth": "none|jwt",
    },
    "expected": {
        "status_codes": [200],
        "status_class": "2xx|4xx|5xx（与 status_codes 二选一）",
        "json_path_contains": ["data.xxx"],
    },
    "tags": ["ai"],
}


# 统一响应包装（Phase 2 架构分析结论，来自 com.backendguru.common）
RESPONSE_WRAPPER = {
    "success": "boolean",
    "data": "业务数据（成功时）",
    "error": {"code": "string", "message": "string", "status": "int",
              "path": "string", "traceId": "string",
              "timestamp": "string", "details": "object"},
    "timestamp": "string(ISO)",
}

# 系统校验顺序实测结论（Phase 6 基线报告）：body 校验先于鉴权
VALIDATION_ORDER_NOTE = "该系统请求体校验先于鉴权：无凭证 + 非法 body 会返回 400 而非 401。auth 类用例必须携带合法 body。"


def build_user_prompt(endpoint: Endpoint, schemas: dict | None = None) -> str:
    schemas = schemas or {}
    schema_ref = endpoint.request_body.schema_ref if endpoint.request_body else None
    req_schema = schemas.get(schema_ref.rsplit("/", 1)[-1]) if schema_ref else None

    spec = {
        "service": endpoint.service,
        "operation_id": endpoint.operation_id,
        "tag": endpoint.tag,
        "method": endpoint.method.value,
        "path": endpoint.path,
        "auth": endpoint.auth.value,
        "parameters": [p.model_dump() for p in endpoint.parameters],
        "request_body": endpoint.request_body.model_dump() if endpoint.request_body else None,
        "request_body_schema": req_schema,   # 真实字段定义，body 字段名必须从这里取
        "responses": [r.model_dump() for r in endpoint.responses],
    }
    return (
        "为下面这个接口设计 3~6 个高价值测试用例（不要凑数，优先：业务边界、必填校验、"
        "鉴权/越权、资源不存在、幂等/重复提交冲突；正向用例 1 个即可）。\n\n"
        f"接口定义:\n{json.dumps(spec, ensure_ascii=False)}\n\n"
        f"统一响应包装（所有接口返回它）:\n{json.dumps(RESPONSE_WRAPPER, ensure_ascii=False)}\n\n"
        "业务前提：\n"
        "- 这是按 ID 查询/操作的接口时，数据库是空的，除非用例自己先创建数据；"
        "因此 GET/PUT/DELETE/{id} 类正向用例应归入 not_found（期望 404），除非路径 ID 是真实创建流程的一部分；\n"
        f"- {VALIDATION_ORDER_NOTE}\n"
        "- json_path_contains 只能引用统一响应包装里的字段路径（如 data.xxx、error.code）。\n\n"
        "输出 JSON 数组，每个元素结构如下（字段名必须完全一致）：\n"
        f"{json.dumps(CASE_JSON_SHAPE, ensure_ascii=False)}\n\n"
        "约束：\n"
        "- path_template/method 保持与输入一致，不改路径；\n"
        "- path_params/query_params 只使用输入里出现的参数名；\n"
        "- body 字段名/类型必须严格来自 request_body_schema 的 properties，禁止编造字段；\n"
        "- auth 接口的用例默认 auth=jwt（执行器会自动注入真实 token），仅鉴权类用例显式变体；\n"
        "- 期望状态码惯例：校验失败 400，未认证 401，不存在 404，成功 200。"
    )
