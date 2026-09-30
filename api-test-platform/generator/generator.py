"""Phase 5 - 用例生成编排器。

流程：加载 endpoints.json -> 规则基线全量生成 -> （可选）LLM 逐接口增强
      -> 校验/去重 -> 统计 -> 写 artifacts/testcases.json。

容错策略：单个接口 LLM 失败只跳过并计数，不中断整体（企业级必备）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from parser.models import Endpoint

from . import prompts, rules
from .config import LLMSettings
from .llm import LLMError, extract_json_array, make_client
from .models import (
    CaseCategory,
    CaseSource,
    ExpectedResult,
    RequestSpec,
    TestCase,
    TestCaseSet,
)

logger = logging.getLogger(__name__)

_CATEGORY_VALUES = {c.value for c in CaseCategory}


def load_endpoints(path: Path) -> tuple[list[Endpoint], dict[str, dict]]:
    """返回 (endpoints, service->schemas)。schemas 供造数据与 LLM 上下文使用。"""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    endpoints: list[Endpoint] = []
    schemas_by_service: dict[str, dict] = {}
    for svc in data.get("services", []):
        schemas_by_service[svc.get("service", "")] = svc.get("schemas", {}) or {}
        endpoints.extend(Endpoint.model_validate(e) for e in svc.get("endpoints", []))
    return endpoints, schemas_by_service


def _case_from_ai(endpoint: Endpoint, idx: int, raw: dict) -> TestCase:
    req = raw.get("request", {})
    exp = raw.get("expected", {})
    return TestCase(
        id=f"{endpoint.service}__{endpoint.operation_id or 'op'}__ai-{idx:02d}",
        service=endpoint.service,
        endpoint_key=endpoint.key,
        name=str(raw.get("name", "AI用例"))[:80],
        category=CaseCategory(raw.get("category", "positive")),
        description=raw.get("description"),
        request=RequestSpec(
            method=req.get("method", endpoint.method.value).upper(),
            path_template=req.get("path_template", endpoint.path),
            path_params=req.get("path_params", {}) or {},
            query_params=req.get("query_params", {}) or {},
            headers=req.get("headers", {}) or {},
            body=req.get("body"),
            auth=req.get("auth", "none" if endpoint.auth.value == "none" else "jwt"),
        ),
        expected=ExpectedResult(
            status_codes=[int(s) for s in (exp.get("status_codes") or [])],
            status_class=exp.get("status_class"),
            json_path_contains=exp.get("json_path_contains", []) or [],
        ),
        source=CaseSource.AI,
        tags=["ai"] + list(raw.get("tags", [])),
    )


def generate(
    endpoints_path: Path,
    out_path: Path,
    settings: LLMSettings | None = None,
    only_service: str | None = None,
    limit: int | None = None,
) -> TestCaseSet:
    settings = settings or LLMSettings.from_env()
    endpoints, schemas_by_service = load_endpoints(endpoints_path)
    if only_service:
        endpoints = [e for e in endpoints if e.service == only_service]
    if limit:
        endpoints = endpoints[:limit]

    # 1) 规则基线（基于真实 schema 造数据）
    cases: list[TestCase] = []
    for ep in endpoints:
        cases.extend(rules.generate_rule_cases(ep, schemas_by_service.get(ep.service, {})))

    # 2) LLM 增强（mock/未配 Key 时自动跳过，纯基线可用）
    ai_stats = {"requested": 0, "ok": 0, "failed": 0, "cases": 0}
    client = make_client(settings) if settings.provider != "mock" else None
    if client is not None:
        for ep in endpoints:
            ai_stats["requested"] += 1
            try:
                reply = client.complete(
                    prompts.SYSTEM_PROMPT,
                    prompts.build_user_prompt(ep, schemas_by_service.get(ep.service, {})),
                )
                raw_cases = extract_json_array(reply.text)
                parsed: list[TestCase] = []
                for i, raw in enumerate(raw_cases, 1):
                    try:
                        if raw.get("category") not in _CATEGORY_VALUES:
                            raise ValueError(f"非法 category: {raw.get('category')}")
                        parsed.append(_case_from_ai(ep, i, raw))
                    except (ValidationError, ValueError, TypeError, KeyError) as exc:
                        logger.warning("AI 用例校验失败，跳过: %s", exc)
                cases.extend(parsed)
                ai_stats["ok"] += 1
                ai_stats["cases"] += len(parsed)
            except (LLMError, json.JSONDecodeError) as exc:
                ai_stats["failed"] += 1
                logger.warning("LLM 生成失败 %s: %s", ep.key, exc)

    case_set = TestCaseSet(cases=cases).merged_dedupe()
    case_set.stats = {
        "endpoints": len(endpoints),
        "provider": settings.provider,
        "model": settings.model or "-",
        "rule_cases": sum(1 for c in case_set.cases if c.source == CaseSource.RULE),
        "ai_cases": sum(1 for c in case_set.cases if c.source == CaseSource.AI),
        "ai_endpoints_ok": ai_stats["ok"],
        "ai_endpoints_failed": ai_stats["failed"],
        "by_category": _count_by_category(case_set.cases),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(case_set.model_dump_json(indent=2), encoding="utf-8")
    return case_set


def _count_by_category(cases: list[TestCase]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in cases:
        out[c.category.value] = out.get(c.category.value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
