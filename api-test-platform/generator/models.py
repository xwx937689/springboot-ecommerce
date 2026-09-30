"""Phase 5 - 测试用例数据模型。

TestCase 是 Phase 6 执行器的输入：必须自带可执行的全部信息
（方法/路径模板/参数/请求体/鉴权要求/期望结果），不依赖生成来源。
source 区分 rule（规则基线）与 ai（LLM 生成），便于对比 AI 增益。
"""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class CaseCategory(str, Enum):
    POSITIVE = "positive"        # 正向主流程
    NEGATIVE = "negative"        # 负向（非法参数/缺字段）
    BOUNDARY = "boundary"        # 边界值
    AUTH = "auth"                # 鉴权类（无 token / 过期 / 越权）
    NOT_FOUND = "not_found"      # 资源不存在
    CONFLICT = "conflict"        # 业务冲突（重复下单等）


class CaseSource(str, Enum):
    RULE = "rule"
    AI = "ai"


class ExpectedResult(BaseModel):
    """期望结果。status_codes 为可接受集合（不同实现可能 200/201，或统一包装 200）。"""
    status_codes: list[int] = Field(default_factory=list)
    # 宽松断言：只校验状态码类别（2xx/4xx/5xx），用于不确定场景
    status_class: str | None = None        # "2xx" | "4xx" | "5xx"
    json_path_contains: list[str] = Field(default_factory=list)  # 响应 JSON 需出现的字段路径，如 data.accessToken


class RequestSpec(BaseModel):
    method: str
    # 路径模板，保留 {param} 占位符，由 path_params 渲染
    path_template: str
    path_params: dict[str, str | int] = Field(default_factory=dict)
    query_params: dict[str, str | int] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict | list | None = None
    auth: str = "none"                     # none | jwt


class TestCase(BaseModel):
    id: str                                # 全局唯一，如 cart-service__addItem__positive-01
    service: str
    endpoint_key: str                      # 对应 Endpoint.key，如 "POST /api/cart/items"
    name: str
    category: CaseCategory
    description: str | None = None
    request: RequestSpec
    expected: ExpectedResult
    source: CaseSource = CaseSource.RULE
    tags: list[str] = Field(default_factory=list)


class TestCaseSet(BaseModel):
    """全部用例集合 + 生成统计（写盘产物，Phase 6 直接加载）。"""
    cases: list[TestCase] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)

    def merged_dedupe(self) -> "TestCaseSet":
        """AI 与规则用例合并去重：同 endpoint+category+name 视为重复，AI 优先。"""
        seen: dict[str, TestCase] = {}
        for c in self.cases:
            key = f"{c.service}|{c.request.method}|{c.request.path_template}|{c.category.value}|{c.name}"
            if key not in seen or c.source == CaseSource.AI:
                seen[key] = c
        self.cases = list(seen.values())
        return self
