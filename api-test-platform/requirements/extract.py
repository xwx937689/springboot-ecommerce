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
_RULE_CATEGORIES = {"constraint", "calculation", "workflow", "permission", "general"}
_TESTABILITY_VALUES = {"testable", "partial", "untestable"}
_LIST_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_SENTENCE_SEP = re.compile(r"[。；;]")
_MIN_SEG = 4        # 陈述句最短长度：再短（"如下："）没有分析价值
_ENUM_SEG = 8       # ；切出的碎片低于此长度视为列举，整行保留


def _norm(s: str) -> str:
    """去全部空白后比对：LLM 的 evidence 与原文只在空格/换行上有差异也能命中。"""
    return re.sub(r"\s+", "", s)


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
            # 局部序号：LLM 中途失败时已推进的序号随局部结果一起丢弃，
            # 全局序号不被污染（否则回退后 ID 断号）
            local_br, local_ac = br_seq.copy(), ac_seq.copy()
            try:
                r, a = _extract_by_llm(client, module, local_br, local_ac)
                rules.extend(r)
                criteria.extend(a)
                br_seq[0], ac_seq[0] = local_br[0], local_ac[0]   # 全模块成功才提交
                stats["llm_modules_ok"] += 1
                used_llm = True
            except (LLMError, json.JSONDecodeError, ValueError,
                    AttributeError, TypeError) as exc:
                # AttributeError/TypeError：LLM 返回的元素不是 dict（["bad", null]）
                # 这类垃圾若不拦会整个任务崩掉，而不只是回退
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
    stats["untestable_criteria"] = sum(1 for c in criteria if c.testability == "untestable")
    stats["partial_criteria"] = sum(1 for c in criteria if c.testability == "partial")
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
                evidence=text,             # 规则引擎的 text 就是原文陈述，证据即自身
                review_status="approved",  # 原文直取，无需人工复核
            )
            rules.append(rule)

        if _is_testable(text):
            ac_seq[0] += 1
            status, reason, questions = _testability(text)
            criteria.append(
                AcceptanceCriterion(
                    id=f"AC-{ac_seq[0]:03d}",
                    module_id=module.id,
                    rule_ids=[rule.id] if rule else [],
                    text=text,
                    evidence=text,
                    review_status="approved",
                    testable=status == "testable",
                    testability=status,
                    testability_reason=reason,
                    clarification_questions=questions,
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
    """正文 -> 陈述句列表：去列表符号，按中英文句号/分号切。

    两个语义保护：
      - ；切出的碎片过短（<8 字）说明是列举（"状态包括：待支付；已支付；已取消"），
        整行保留为一条，避免后几个枚举值失去上下文；
      - 最短阈值 4 字而非 6："仅限 VIP""不可重复" 这类短句可能是关键约束。
    """
    out: list[str] = []
    for line in content.splitlines():
        line = _LIST_BULLET.sub("", line.strip())
        if not line:
            continue
        parts = [p.strip() for p in _SENTENCE_SEP.split(line) if p.strip()]
        if len(parts) > 1 and any(len(p) < _ENUM_SEG for p in parts):
            out.append(line.rstrip("。"))     # 列举句：保上下文，不切
        else:
            out.extend(p for p in parts if len(p) >= _MIN_SEG)
    return out


def _is_rule(text: str) -> bool:
    return any(m in text for m in lexicon.RULE_MARKERS)


def _is_testable(text: str) -> bool:
    """有数值边界（最可靠）或可观察结果，即可判定。"""
    return bool(lexicon.NUMERIC.search(text)) or any(v in text for v in lexicon.OBSERVABLE)


def _is_vague(text: str) -> bool:
    return any(w in text for w in lexicon.VAGUE)


def _testability(text: str) -> tuple[str, str, list[str]]:
    """三态可测性判定：可测 / 部分可测 / 不可测。

    区分依据：
      - 含 TBD 词（待定/暂定）：连取值都没有 -> untestable；
      - 含其他模糊词（尽快/大约）：有可验证部分但缺量化标准 -> partial；
      - 都没有 -> testable。
    """
    tbd = [w for w in lexicon.TBD if w in text]
    if tbd:
        return (
            "untestable",
            f"存在未确定项「{'、'.join(tbd)}」，取值未定无法判定",
            [f"请明确「{w}」的具体取值" for w in tbd],
        )
    vague = [w for w in lexicon.VAGUE if w in text]
    if vague:
        return (
            "partial",
            f"含模糊表述「{'、'.join(vague)}」，缺少量化标准",
            [f"请明确「{w}」的量化标准（如具体数值/时间）" for w in vague],
        )
    return "testable", "", []


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
    ref_to_id: dict[str, str] = {}        # LLM 模块内临时 ref（R1...）-> 正式 BR ID
    text_to_rule_id: dict[str, str] = {}  # 兼容兜底：按原文精确匹配（ref 缺失时）
    dropped = 0

    for raw in raw_items:
        if not isinstance(raw, dict):     # ["invalid", null, 123] 这类垃圾条目
            dropped += 1
            continue
        kind = str(raw.get("kind", "")).strip().lower()
        text = str(raw.get("text", "")).strip()
        evidence = str(raw.get("evidence", "")).strip()
        if kind not in _ITEM_KIND or not text:
            dropped += 1
            continue
        # 幻觉防线：evidence 必须真的出自本模块正文（去空白比对）。
        # 程序只能验证"原文存在"，验证不了"语义没被放大"——后者靠 review_status=pending 走人工
        if not evidence or _norm(evidence) not in _norm(module.content):
            dropped += 1
            continue

        if kind == "rule":
            ref = str(raw.get("ref") or "").strip()
            if ref and ref in ref_to_id:  # ref 重复：无效条目
                dropped += 1
                continue
            br_seq[0] += 1
            rid = f"BR-{br_seq[0]:03d}"
            if ref:
                ref_to_id[ref] = rid
            text_to_rule_id[text] = rid
            # LLM 给了不认识的分类 -> 降级 general，不整模块报废
            category = str(raw.get("category") or "general").strip().lower()
            if category not in _RULE_CATEGORIES:
                category = "general"
            rules.append(
                BusinessRule(
                    id=rid,
                    module_id=module.id,
                    text=text[:300],
                    category=category,          # type: ignore[arg-type]
                    evidence=evidence[:300],
                    review_status="pending",    # LLM 产出必须过人工复核
                )
            )
        else:
            ac_seq[0] += 1
            derived = str(raw.get("derived_from") or "").strip()
            # 优先 ref（LLM 改写文字也不影响关联），退回原文精确匹配
            rid = ref_to_id.get(derived) or text_to_rule_id.get(derived) or ""
            # LLM 可直接给三态；只给旧布尔时按 testable 映射
            status = str(raw.get("testability") or "").strip().lower()
            if status not in _TESTABILITY_VALUES:
                status = "testable" if bool(raw.get("testable", True)) else "untestable"
            criteria.append(
                AcceptanceCriterion(
                    id=f"AC-{ac_seq[0]:03d}",
                    module_id=module.id,
                    rule_ids=[rid] if rid else [],
                    text=text[:300],
                    evidence=evidence[:300],
                    review_status="pending",
                    testable=status == "testable",
                    testability=status,         # type: ignore[arg-type]
                    testability_reason=str(raw.get("testability_reason") or "").strip()[:200],
                )
            )
    if not rules and not criteria:
        raise ValueError("LLM 未产出任何合法条目")
    if dropped:
        print(f"[WARN] LLM 条目 {module.id}: 丢弃 {dropped} 条（无原文证据/结构非法）")
    return rules, criteria


def _build_prompt(module: ReqModule) -> str:
    return (
        f"章节：{module.title}（ID={module.id}）\n"
        f"正文：\n{module.content}\n\n"
        "请拆解成条目，逐条输出。要求：\n"
        "- kind=rule：系统必须满足的约束，category 取 constraint|permission|workflow|general；\n"
        "- kind=ac：可直接判定通过/失败的可验证陈述，必须能对应到具体行为或数值；\n"
        "- rule 条目用 ref 起模块内临时编号（R1、R2...），供 ac 条目引用；\n"
        "- evidence：必须填【正文中真实存在】的原文片段（该条目所依据的句子），"
        "程序会校验它确实出现在正文里，找不到的条目会被整条丢弃；\n"
        "- ac 条目若由某条 rule 推导而来，用 derived_from 填该 rule 的 ref（如 R1），否则填空串；\n"
        "- testability：testable(可直接判定)|partial(部分可验证但含模糊表述)|untestable(取值未定无法判定)，"
        "含模糊词（尽快/大约/合理/高可用）至少是 partial，含「待定」是 untestable；\n"
        "- testability_reason：testability 不是 testable 时，一句话说明原因；\n"
        "- 不要合并无关句子，不要编造正文里没有的内容。\n\n"
        "输出 JSON 数组，元素结构：\n"
        + json.dumps(
            {
                "kind": "rule|ac",
                "ref": "R1（仅 rule 填）",
                "text": "条目原文或规范化后的陈述",
                "evidence": "正文中该条目所依据的原文片段",
                "category": "constraint|calculation|permission|workflow|general",
                "derived_from": "对应 rule 的 ref，或空串",
                "testability": "testable|partial|untestable",
                "testability_reason": "非 testable 时的一句话原因",
            },
            ensure_ascii=False,
        )
    )
