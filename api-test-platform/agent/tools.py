"""Agent 工具层：schema 定义（给 LLM 看）+ ToolBox（真实实现，给循环调）。

设计约束：
  - 工具返回的是给 LLM 读的紧凑文本（token 是钱），不是原始 JSON 全量；
  - dispatch 对垃圾入参（非法 JSON / 未知工具 / 字段缺失）一律收敛为
    [ERROR] 文本喂回模型，绝不炸循环；
  - submit_case 是唯一结束通道，由 runner 特判，dispatch 只做参数回显。
"""
from __future__ import annotations

import json

# 给 LLM 的工具 schema（OpenAI function calling 格式）
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "lookup_interfaces",
        "description": "按业务关键词检索平台已登记的真实接口契约（匹配路径/标签/服务名），"
                       "返回 method/path/auth/请求体结构。写测试步骤前必须先查，禁止编造接口路径。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "业务关键词，如 购物车/cart、订单/order"}},
            "required": ["keyword"]},
    }},
    {"type": "function", "function": {
        "name": "search_examples",
        "description": "按关键词检索已有的完整 Gherkin 用例作为风格与粒度参照（词面重叠检索）。",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string"}},
            "required": ["keyword"]},
    }},
    {"type": "function", "function": {
        "name": "submit_case",
        "description": "提交最终测试步骤。唯一结束方式：preconditions 数据/状态准备；"
                       "steps 1~10 条真实操作（引用查到的接口）；expected_results 单一可断言结果。",
        "parameters": {"type": "object", "properties": {
            "preconditions": {"type": "string"},
            "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "expected_results": {"type": "string"},
            "notes": {"type": "string", "description": "一句话设计依据"}},
            "required": ["preconditions", "steps", "expected_results"]},
    }},
]

_MAX_EP_SHOWN = 5          # lookup_interfaces 最多返回条数
_MAX_EXAMPLES = 2          # search_examples 最多返回条数
_EXAMPLE_CUT = 500         # 参照用例文本截断


def _compact_ep(ep: dict) -> dict:
    body = ep.get("request_body") or {}
    return {
        "service": ep.get("service", ""),
        "method": ep.get("method", ""),
        "path": ep.get("path", ""),
        "auth": ep.get("auth", ""),
        "request_schema": (body.get("schema_ref") or "").replace("#/components/schemas/", ""),
    }


class ToolBox:
    """工具实现集合：对 Agent 暴露 dispatch(name, args_json)，对测试暴露可注入的纯数据。"""

    def __init__(self, product: dict, endpoints: dict, examples: list[dict]):
        # endpoints: {"services": [{endpoints: [...]}]} -> 拍平
        self._eps: list[dict] = [
            ep for svc in endpoints.get("services", []) for ep in svc.get("endpoints", [])
        ]
        self._examples = examples          # [{"title":..., "gherkin":...}] 已有完整用例
        self._submit_payload: dict | None = None   # runner 从这里取最终提交

    def dispatch(self, name: str, args_json: str) -> str:
        """执行一个工具调用，返回给 LLM 的文本。任何异常收敛为 [ERROR] 文本。"""
        try:
            args = json.loads(args_json or "{}")
        except json.JSONDecodeError:
            return "[ERROR] 工具参数不是合法 JSON"
        if not isinstance(args, dict):
            return "[ERROR] 工具参数必须是 JSON 对象"
        if name == "lookup_interfaces":
            return self._lookup_interfaces(str(args.get("keyword", "")))
        if name == "search_examples":
            return self._search_examples(str(args.get("keyword", "")))
        if name == "submit_case":
            self._submit_payload = args
            return json.dumps(args, ensure_ascii=False)
        return f"[ERROR] 未知工具 {name}（可用: lookup_interfaces/search_examples/submit_case）"

    def _lookup_interfaces(self, keyword: str) -> str:
        kw = keyword.strip().lower()
        if not kw:
            return "[ERROR] keyword 不能为空"
        hits = [
            _compact_ep(ep) for ep in self._eps
            if kw in (ep.get("path", "") + ep.get("tag", "") + ep.get("service", "")
                      + str(ep.get("summary") or "")).lower()
        ]
        if not hits:
            return f"接口库中没有匹配「{keyword}」的接口（共 {len(self._eps)} 个可查）。"
        shown = hits[:_MAX_EP_SHOWN]
        more = f"\n（共 {len(hits)} 个命中，仅显示前 {len(shown)} 个）" if len(hits) > len(shown) else ""
        return json.dumps(shown, ensure_ascii=False) + more

    def _search_examples(self, keyword: str) -> str:
        kw = keyword.strip().lower()
        if not kw:
            return "[ERROR] keyword 不能为空"
        scored = [
            ex for ex in self._examples
            if kw in (ex.get("title", "") + ex.get("gherkin", "")).lower()
        ]
        if not scored:
            return "没有检索到风格参照用例，请按标准 Gherkin 自行组织。"
        texts = [
            f"参照[{i + 1}] {ex.get('title', '')}\n{ex.get('gherkin', '')[:_EXAMPLE_CUT]}"
            for i, ex in enumerate(scored[:_MAX_EXAMPLES])
        ]
        return "\n\n".join(texts)
