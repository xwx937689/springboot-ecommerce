"""Phase 16 - 业务规则(BR) / 验收标准(AC) 提取。

双引擎设计（与 WHartTest 纯 LLM 的关键差别）：
  rule 引擎：分词 -> 按规范性词汇/可观察结果/数值边界判定 -> 产出 BR+AC
             离线可跑、结果完全可复现，是 mock 模式下的唯一来源；
  llm  引擎：逐模块喂正文，要求输出结构化 JSON，逐条做 schema 校验，
             单模块失败只回退该模块到 rule 引擎（不中断整体）。

追溯：
  BR.id / AC.id 全局连续编号；
  AC.rule_ids 指向"同一条陈述"产出的 BR（rule 引擎精确关联；
  llm 引擎按 derived_from 文本匹配，匹配不到就留空，绝不瞎猜）。
"""
from __future__ import annotations

import json
import re

from generator.config import LLMSettings
from generator.llm import LLMError, extract_json_array, make_client

from . import lexicon
from .models import AcceptanceCriterion, BusinessRule, ReqModule

SYSTEM_PROMPT = (
    "你是资深需求分析师，正在把 PRD 拆解为【业务规则】与【验收标准】。"
    "业务规则=系统必须满足的约束；验收标准=可以直接判定通过或失败的可验证陈述。"
    "只输出 JSON 数组，不要任何解释或 markdown 代码块。"
)

_ITEM_KIND = {"rule", "ac"}
_LIST_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_SENTENCE_SEP = re.compile(r"[。；;]")


def extract(
    modules: list[ReqModule], settings: LLMSettings | None = None
) -> tuple[list[BusinessRule], list[AcceptanceCriterion], dict]:
    """从模块树提取 (rules, criteria, stats)。"""
    settings = settings or LLMSettings.from_env()
    client = make_client(settings) if settings.provider != "mock" else None

    rules: list[BusinessRule] = []
    criteria: list[AcceptanceCriterion] = []
    br_seq = [0]
    ac_seq = [0]
    flat = _walk(modules)          # 扁平化一次，统计与遍历共用
    stats = {
        "modules": len(flat),
        "engine": "llm" if client else "rule",
        "llm_modules_ok": 0,
        "llm_modules_failed": 0,
        "llm_modules_fallback": 0,
    }

    for module in flat:
        if not module.content.strip():
            continue          # 纯容器章节（只有标题没正文）跳过，避免造出空规则

        used_llm = False
        if client is not None:
            try:
                r, a = _extract_by_llm(client, module, br_seq, ac_seq)
                rules.extend(r)
                criteria.extend(a)
                stats["llm_modules_ok"] += 1
                used_llm = True
            except (LLMError, json.JSONDecodeError, ValueError) as exc:
                stats["llm_modules_failed"] += 1
                print(f"[WARN] LLM 提取失败 {module.id}，回退规则引擎: {exc}")

        if not used_llm:
            r, a = extract_rule_based(module, br_seq, ac_seq)
            rules.extend(r)
            criteria.extend(a)
            if client is not None:
                stats["llm_modules_fallback"] += 1

    stats["rules"] = len(rules)
    stats["criteria"] = len(criteria)
    stats["untestable_criteria"] = sum(1 for c in criteria if not c.testable)
    return rules, criteria, stats


def extract_rule_based(
    module: ReqModule, br_seq: list[int] | None = None, ac_seq: list[int] | None = None
) -> tuple[list[BusinessRule], list[AcceptanceCriterion]]:
    """规则引擎：单模块提取。纯函数，可单独测试。"""
    br_seq = br_seq if br_seq is not None else [0]
    ac_seq = ac_seq if ac_seq is not None else [0]
    rules: list[BusinessRule] = []
    criteria: list[AcceptanceCriterion] = []

    for text in _split_statements(module.content):
        rule: BusinessRule | None = None
        if _is_rule(text):
            br_seq[0] += 1
            rule = BusinessRule(
                id=f"BR-{br_seq[0]:03d}",
                module_id=module.id,
                text=text,
                category=_classify_rule(text),
            )
            rules.append(rule)

        if _is_testable(text):
            ac_seq[0] += 1
            criteria.append(
                AcceptanceCriterion(
                    id=f"AC-{ac_seq[0]:03d}",
                    module_id=module.id,
                    rule_ids=[rule.id] if rule else [],
                    text=text,
                    testable=not _is_vague(text),
                )
            )
    return rules, criteria


# ---------------------------------------------------------------- 规则引擎内部

def _walk(modules: list[ReqModule]) -> list[ReqModule]:
    """前序遍历成扁平列表（文档顺序）。"""
    out: list[ReqModule] = []
    for m in modules:
        out.append(m)
        out.extend(_walk(m.children))
    return out


def _split_statements(content: str) -> list[str]:
    """正文 -> 陈述句列表：去列表符号，按中英文句号/分号切。"""
    out: list[str] = []
    for line in content.splitlines():
        line = _LIST_BULLET.sub("", line.strip())
        if not line:
            continue
        for seg in _SENTENCE_SEP.split(line):
            seg = seg.strip()
            if len(seg) >= 6:      # 太短的片段（"如下："）没有分析价值
                out.append(seg)
    return out


def _is_rule(text: str) -> bool:
    return any(m in text for m in lexicon.RULE_MARKERS)


def _is_testable(text: str) -> bool:
    """有数值边界（最可靠）或可观察结果，即可判定。"""
    return bool(lexicon.NUMERIC.search(text)) or any(v in text for v in lexicon.OBSERVABLE)


def _is_vague(text: str) -> bool:
    return any(w in text for w in lexicon.VAGUE)


def _classify_rule(text: str) -> str:
    """粗分规则类型，供后续按类型生成测试点。"""
    if lexicon.NUMERIC.search(text) or any(
        k in text for k in ("最多", "最少", "至少", "不超过", "上限", "下限")
    ):
        return "constraint"
    if any(k in text for k in ("不能", "不得", "禁止", "不允许", "仅", "只允许")):
        return "permission"
    if any(k in text for k in ("状态", "变为", "生成", "流程")):
        return "workflow"
    return "general"


# ---------------------------------------------------------------- LLM 引擎

def _extract_by_llm(
    client, module: ReqModule, br_seq: list[int], ac_seq: list[int]
) -> tuple[list[BusinessRule], list[AcceptanceCriterion]]:
    reply = client.complete(SYSTEM_PROMPT, _build_prompt(module))
    raw_items = extract_json_array(reply.text)

    rules: list[BusinessRule] = []
    criteria: list[AcceptanceCriterion] = []
    text_to_rule_id: dict[str, str] = {}

    for raw in raw_items:
        kind = str(raw.get("kind", "")).strip().lower()
        text = str(raw.get("text", "")).strip()
        if kind not in _ITEM_KIND or not text:
            continue                       # schema 校验：不合法直接丢，不猜
        if kind == "rule":
            br_seq[0] += 1
            rid = f"BR-{br_seq[0]:03d}"
            text_to_rule_id[text] = rid
            rules.append(
                BusinessRule(
                    id=rid,
                    module_id=module.id,
                    text=text[:300],
                    category=str(raw.get("category") or "general"),
                )
            )
        else:
            ac_seq[0] += 1
            derived = str(raw.get("derived_from") or "").strip()
            criteria.append(
                AcceptanceCriterion(
                    id=f"AC-{ac_seq[0]:03d}",
                    module_id=module.id,
                    rule_ids=[text_to_rule_id[derived]] if derived in text_to_rule_id else [],
                    text=text[:300],
                    testable=bool(raw.get("testable", True)),
                )
            )
    if not rules and not criteria:
        raise ValueError("LLM 未产出任何合法条目")
    return rules, criteria


def _build_prompt(module: ReqModule) -> str:
    return (
        f"章节：{module.title}（ID={module.id}）\n"
        f"正文：\n{module.content}\n\n"
        "请拆解成条目，逐条输出。要求：\n"
        "- kind=rule：系统必须满足的约束，category 取 constraint|permission|workflow|general；\n"
        "- kind=ac：可直接判定通过/失败的可验证陈述，必须能对应到具体行为或数值；\n"
        "- ac 条目若由某条 rule 推导而来，用 derived_from 填该 rule 的 text 原文，否则填空串；\n"
        "- testable：陈述含模糊词（尽快/大约/合理/高可用等）时为 false；\n"
        "- 不要合并无关句子，不要编造正文里没有的内容。\n\n"
        "输出 JSON 数组，元素结构：\n"
        + json.dumps(
            {
                "kind": "rule|ac",
                "text": "条目原文或规范化后的陈述",
                "category": "constraint|permission|workflow|general",
                "derived_from": "对应 rule 的 text，或空串",
                "testable": True,
            },
            ensure_ascii=False,
        )
    )
