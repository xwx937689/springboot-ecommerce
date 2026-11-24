"""轻量 Agent harness：tool-loop 用例生成（与单发 prompt 模式对照）。

设计动机（对照 generator.generator 的单发模式）：
  单发模式把 schema/需求提示一次塞满 prompt——上下文大且只有 P0 标题 8 条提示；
  Agent 模式上下文按需取数：
    get_schema       按需查 request_body_schema 真实字段定义（防编造字段）
    get_requirement  按需查需求测试点全文（标题/前置/步骤/预期），决定覆盖与 tp_id 回填
    submit_cases     结构化收尾：用例数组作为工具参数提交，不靠解析正文
指标：filled 率 / req_trace 回填率 / token 曲线（prompt+completion+轮数），
与单发模式跑同批接口对照——agent 的增益必须用数据证明，不是凭感觉。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from .config import LLMSettings
from .generator import _case_from_ai, _count_by_category, load_endpoints
from .llm import LLMError, extract_json_array, make_client
from .models import CaseCategory, CaseSource, TestCase, TestCaseSet

logger = logging.getLogger(__name__)

_MAX_TURNS = 8          # 硬上限：防工具循环失控烧 token
_CATEGORY_VALUES = {c.value for c in CaseCategory}

AGENT_SYSTEM_PROMPT = (
    "你是资深接口自动化测试工程师，为 Spring Boot 微服务电商系统设计 API 测试用例。"
    "你可以调用工具按需获取信息：get_schema 查请求体真实字段定义，"
    "get_requirement 查需求测试点全文。信息足够后，调用 submit_cases 工具提交用例 JSON 数组"
    "（结构与用户消息中给出的 schema 一致），不要在文本里输出用例。"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": "按名称查询请求体 schema 的真实字段定义（properties），body 字段名必须从这里取，禁止编造字段。",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "schema 名称，如 CreateUserRequest"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_requirement",
            "description": "按 id 查询需求测试点全文（标题/前置/步骤/预期结果）。若与本接口相关，用例应覆盖它并在 tp_id 回填该 id。",
            "parameters": {
                "type": "object",
                "properties": {"tp_id": {"type": "string", "description": "测试点 id，如 TP-003"}},
                "required": ["tp_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_cases",
            "description": "提交最终测试用例 JSON 数组（元素结构与用户消息中的 schema 一致）。提交后任务结束。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cases": {"type": "array", "items": {"type": "object"}, "description": "用例数组"},
                },
                "required": ["cases"],
            },
        },
    },
]


class AgentHarness:
    """单接口的 tool-loop：查数 → 提交用例。usage 逐轮累计成 token 曲线。"""

    def __init__(self, client, schemas: dict, tp_by_id: dict[str, dict],
                 max_turns: int = _MAX_TURNS):
        self.client = client
        self.schemas = schemas
        self.tp_by_id = tp_by_id
        self.max_turns = max_turns

    def run(self, user_prompt: str) -> tuple[list[dict], dict]:
        """返回 (raw 用例数组, usage)。usage: turns/tool_calls/prompt_tokens/completion_tokens。"""
        usage = {"turns": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        messages: list[dict] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        for turn in range(self.max_turns):
            msg = self.client.chat(messages, tools=TOOLS)
            u = msg.get("_usage") or {}
            usage["prompt_tokens"] += u.get("prompt_tokens", 0)
            usage["completion_tokens"] += u.get("completion_tokens", 0)
            usage["turns"] = turn + 1
            assistant = {"role": "assistant", "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                assistant["tool_calls"] = msg["tool_calls"]
            messages.append(assistant)                    # 审计字段(_model/_usage)不进队列

            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                usage["tool_calls"] += len(tool_calls)
                for tc in tool_calls:
                    name, result, done = self._exec(tc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    })
                    if done:                              # submit_cases 成功 → 立即收尾，省一轮
                        return self._submitted, usage
                continue

            # 无工具调用：content 视为最终答案（兼容不主动用 submit_cases 的模型）
            try:
                return extract_json_array(msg.get("content") or ""), usage
            except LLMError:
                if turn == self.max_turns - 1:
                    raise
                messages.append({"role": "user",
                                 "content": "请调用 submit_cases 工具提交用例 JSON 数组。"})
        raise LLMError(f"agent 超过最大轮数 {self.max_turns}，未提交用例")

    def _exec(self, tc: dict) -> tuple[str, str, bool]:
        """执行一次工具调用，返回 (name, 结果JSON, 是否收尾)。"""
        fn = (tc.get("function") or {})
        name = str(fn.get("name", ""))
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return name, json.dumps({"error": "arguments 不是合法 JSON 对象"}, ensure_ascii=False), False

        if name == "get_schema":
            schema = self.schemas.get(str(args.get("name", "")))
            return name, json.dumps({"found": bool(schema), "schema": schema}, ensure_ascii=False), False
        if name == "get_requirement":
            tp = self.tp_by_id.get(str(args.get("tp_id", "")))
            return name, json.dumps({"found": bool(tp), "requirement": tp}, ensure_ascii=False), False
        if name == "submit_cases":
            cases = args.get("cases")
            if not isinstance(cases, list):
                return name, json.dumps({"error": "cases 必须是数组"}, ensure_ascii=False), False
            self._submitted = cases
            return name, json.dumps({"ok": True, "count": len(cases)}), True
        return name, json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False), False


def _load_tp_index(requirements_path: Path) -> dict[str, dict]:
    """测试点全文索引（get_requirement 工具的数据源）。"""
    data = json.loads(requirements_path.read_text(encoding="utf-8-sig"))
    return {tp["id"]: tp for tp in data.get("test_points", []) if tp.get("id")}


def generate_agent(
    endpoints_path: Path,
    out_path: Path,
    settings: LLMSettings | None = None,
    only_service: str | None = None,
    limit: int | None = None,
    requirements_path: Path | None = None,
    max_turns: int = _MAX_TURNS,
) -> TestCaseSet:
    """Agent 模式编排：规则基线 + 逐接口 tool-loop 生成。mock 自动跳过 LLM 段。"""
    settings = settings or LLMSettings.from_env()
    endpoints, schemas_by_service = load_endpoints(endpoints_path)
    if only_service:
        endpoints = [e for e in endpoints if e.service == only_service]
    if limit:
        endpoints = endpoints[:limit]

    req_hints: list[dict] = []
    tp_index: dict[str, dict] = {}
    tp_by_id: dict[str, dict] = {}
    if requirements_path is not None and requirements_path.exists():
        from .generator import _load_req_points
        req_hints, tp_index = _load_req_points(requirements_path)
        tp_by_id = _load_tp_index(requirements_path)

    # 规则基线（与单发模式同口径，保证对照只差 LLM 段）
    from . import rules
    cases: list[TestCase] = [
        c for ep in endpoints
        for c in rules.generate_rule_cases(ep, schemas_by_service.get(ep.service, {}))
    ]

    ai_stats = {"requested": 0, "ok": 0, "failed": 0, "cases": 0}
    usage_total = {"turns": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    client = make_client(settings) if settings.provider != "mock" else None
    if client is not None:
        from . import prompts
        for ep in endpoints:
            ai_stats["requested"] += 1
            try:
                harness = AgentHarness(client, schemas_by_service.get(ep.service, {}),
                                       tp_by_id, max_turns=max_turns)
                raw_cases, usage = harness.run(
                    prompts.build_user_prompt(ep, schemas_by_service.get(ep.service, {}), req_hints)
                )
                for k in usage_total:
                    usage_total[k] += usage[k]
                parsed: list[TestCase] = []
                for i, raw in enumerate(raw_cases, 1):
                    try:
                        if raw.get("category") not in _CATEGORY_VALUES:
                            raise ValueError(f"非法 category: {raw.get('category')}")
                        parsed.append(_case_from_ai(ep, i, raw, tp_index))
                    except (ValidationError, ValueError, TypeError, KeyError) as exc:
                        logger.warning("AI 用例校验失败，跳过: %s", exc)
                cases.extend(parsed)
                ai_stats["ok"] += 1
                ai_stats["cases"] += len(parsed)
            except LLMError as exc:
                ai_stats["failed"] += 1
                logger.warning("Agent 生成失败 %s: %s", ep.key, exc)

    case_set = TestCaseSet(cases=cases).merged_dedupe()
    ai_cases = [c for c in case_set.cases if c.source == CaseSource.AI]
    filled = [c for c in ai_cases
              if c.description and (c.expected.status_codes or c.expected.status_class)]
    case_set.stats = {
        "endpoints": len(endpoints),
        "provider": settings.provider,
        "model": settings.model or "-",
        "mode": "agent",
        "rule_cases": sum(1 for c in case_set.cases if c.source == CaseSource.RULE),
        "ai_cases": len(ai_cases),
        "ai_endpoints_ok": ai_stats["ok"],
        "ai_endpoints_failed": ai_stats["failed"],
        "filled_rate": round(len(filled) / len(ai_cases), 3) if ai_cases else 0.0,
        "req_points": len(req_hints),
        "trace_cases": sum(1 for c in case_set.cases if c.req_trace),
        "by_category": _count_by_category(case_set.cases),
        "agent_usage": dict(usage_total),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(case_set.model_dump_json(indent=2), encoding="utf-8")
    return case_set
