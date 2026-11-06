"""Phase 16 - 需求质量分析：歧义 / 冲突 / 遗漏 / 清晰度。

两层设计（延续 extract 的思路，但顺序反过来）：
  规则层【永远运行】：4 个确定性检测器，离线可跑、结果可复现
    _ambiguity()  模糊词 / 主观形容词        -> 无法判定通过失败
    _conflict()   同模块同单位同方向多值      -> 需求自相矛盾
    _omission()   待定项 / 只写成功路径 / 非功能无指标 -> 漏内容
    _clarity()    指代不明 / 开放式枚举        -> 表述不清
  LLM 层【可选】：逐模块补充语义级问题（业务矛盾、隐含前提），
    type 必须在 4 类枚举内，evidence/reason 非空，否则丢弃。

为什么规则层不依赖 LLM：
  质量结论要能进 CI、能对比版本、能解释。LLM 只做"补充"，不做"基线"。
  这与 WHartTest 纯 LLM 评审（每次结果不同、无法回归）是根本区别。

冲突检测的实现约束：
  只在【同一模块 + 同一方向 + 同一单位】内比较数值，宁可漏报不误报。
  跨模块比较容易把"购物车 50 件"和"订单 99 件"误判成冲突，暂不做。
"""
from __future__ import annotations

import json

from generator.config import LLMSettings
from generator.llm import LLMError, extract_json_array, make_client

from . import lexicon
from .extract import _split_statements, _walk
from .models import AmbiguityFinding, BusinessRule, FindingType, ReqModule, Severity

_SYSTEM_PROMPT = (
    "你是资深需求评审专家，负责找出 PRD 中的歧义、冲突、遗漏与表述不清。"
    "只报告会导致返工或无法测试的问题，每条必须引用原文。只输出 JSON 数组。"
)

_VALID_TYPES = {t.value for t in FindingType}
_VALID_SEVERITY = {s.value for s in Severity}
_CORE_WORDS = ("支付", "订单", "登录", "注册", "资金", "库存")
_NONFUNC_TITLES = ("非功能", "性能", "可用性", "安全", "可靠性")


def analyze(
    modules: list[ReqModule],
    rules: list[BusinessRule],
    settings: LLMSettings | None = None,
) -> tuple[list[AmbiguityFinding], dict]:
    """检测需求质量问题，返回 (findings, stats)。"""
    settings = settings or LLMSettings.from_env()
    client = make_client(settings) if settings.provider != "mock" else None

    flat = _walk(modules)
    findings: list[AmbiguityFinding] = []
    sentence_count = 0

    # 1) 规则层：逐模块扫句子
    for module in flat:
        sentences = _split_statements(module.content)
        sentence_count += len(sentences)
        findings += _ambiguity(module, sentences)
        findings += _omission(module, sentences)
        findings += _clarity(module, sentences)

    # 2) 规则层：跨模块的数值冲突（同模块内比较）
    findings += _conflict(rules)
    rule_findings = len(findings)

    # 3) LLM 层：逐模块补充
    stats = {
        "modules": len(flat),
        "sentences": sentence_count,
        "engine": "llm+rule" if client else "rule",
        "rule_findings": rule_findings,
        "llm_findings": 0,
        "llm_modules_ok": 0,
        "llm_modules_failed": 0,
    }
    if client is not None:
        for module in flat:
            if not module.content.strip():
                continue
            try:
                extra = _by_llm(client, module)
                findings += extra
                stats["llm_findings"] += len(extra)
                stats["llm_modules_ok"] += 1
            except (LLMError, json.JSONDecodeError, ValueError,
                    AttributeError, TypeError) as exc:
                # AttributeError/TypeError：LLM 返回元素不是 dict（["bad", null]），
                # 不拦会崩整个任务而非只降级该模块
                stats["llm_modules_failed"] += 1
                print(f"[WARN] LLM 需求分析失败 {module.id}: {exc}")

    findings = _dedupe(findings)
    for i, f in enumerate(findings, 1):
        f.id = f"F-{i:03d}"                 # 统一重排，保证连续

    # 填人可读的原文定位（人工审核从问题跳回 PRD 原文用）
    mod_by_id = {m.id: m for m in flat}
    for f in findings:
        m = mod_by_id.get(f.module_id)
        if m and m.line_end:
            f.source_location = (
                f"第 {m.line_start}-{m.line_end} 行"
                if m.line_end != m.line_start else f"第 {m.line_start} 行"
            )

    stats["findings"] = len(findings)
    stats["by_type"] = {t.value: sum(1 for f in findings if f.type == t) for t in FindingType}
    stats["by_severity"] = {
        s.value: sum(1 for f in findings if f.severity == s) for s in Severity
    }
    return findings, stats


# ---------------------------------------------------------------- 检测器

def _ambiguity(module: ReqModule, sentences: list[str]) -> list[AmbiguityFinding]:
    out: list[AmbiguityFinding] = []
    for s in sentences:
        # 「待定/暂定」属于"没说清内容"，交给 _omission 报，避免同一句报两条
        vague = [w for w in lexicon.VAGUE if w in s and w not in lexicon.TBD]
        if vague:
            out.append(_finding(
                module.id, FindingType.AMBIGUITY,
                Severity.HIGH if _is_core(s) else Severity.MEDIUM,
                evidence=s,
                reason=f"出现模糊表述「{'、'.join(vague)}」，无法判定通过或失败",
                suggestion="替换为可度量指标（如「响应时间 ≤ 500ms」「5 分钟内跳转」）",
            ))
            continue                       # 一句只报一次，避免重复
        subj = [w for w in lexicon.SUBJECTIVE if w in s]
        if subj:
            out.append(_finding(
                module.id, FindingType.AMBIGUITY, Severity.MEDIUM,
                evidence=s,
                reason=f"出现主观形容词「{'、'.join(subj)}」，不同人理解不同",
                suggestion="改为可观测的行为或量化指标",
            ))
    return out


def _conflict(rules: list[BusinessRule]) -> list[AmbiguityFinding]:
    """同模块 + 同方向 + 同单位，数值不一致 -> 冲突。"""
    buckets: dict[tuple[str, str, str], list[tuple[BusinessRule, int]]] = {}
    for r in rules:
        direction = _direction(r.text)
        if not direction:
            continue                       # 没有上下限词，不参与冲突比较
        for m in lexicon.NUMBER_UNIT.finditer(r.text):
            key = (r.module_id, direction, m.group(2))
            buckets.setdefault(key, []).append((r, int(m.group(1))))

    out: list[AmbiguityFinding] = []
    for (module_id, direction, unit), items in buckets.items():
        values = sorted({n for _, n in items})
        if len(values) < 2:
            continue
        label = "上限" if direction == "upper" else "下限"
        out.append(_finding(
            module_id, FindingType.CONFLICT, Severity.HIGH,
            evidence=" | ".join(f"{r.id}: {r.text}" for r, _ in items),
            reason=f"同一模块对「{unit}」的{label}给出不同值 {values}，实现方无法确定按哪个做",
            suggestion="确认唯一取值，删除或标注废弃的一条",
        ))
    return out


def _omission(module: ReqModule, sentences: list[str]) -> list[AmbiguityFinding]:
    out: list[AmbiguityFinding] = []

    # 1) 明确写着"待定"
    for s in sentences:
        tbd = [t for t in lexicon.TBD if t in s]
        if tbd:
            out.append(_finding(
                module.id, FindingType.OMISSION, Severity.HIGH,
                evidence=s,
                reason=f"存在未确定项「{'、'.join(tbd)}」，开发和测试都无法推进",
                suggestion="在评审前补齐明确取值或标注为后续版本",
            ))

    # 2) 只写了成功路径，没有任何失败/异常描述
    has_failure = any(w in module.content for w in lexicon.FAILURE_WORDS)
    if not has_failure:
        actions = [s for s in sentences if any(a in s for a in lexicon.BUSINESS_ACTION)]
        if actions:
            out.append(_finding(
                module.id, FindingType.OMISSION, Severity.MEDIUM,
                evidence=actions[0],
                reason="只描述了业务动作的成功路径，未定义失败/异常/超时如何处理",
                suggestion="补充异常流：失败提示、状态回滚、超时与重试策略",
            ))

    # 3) 非功能章节没有任何数字
    if any(k in module.title for k in _NONFUNC_TITLES) and not lexicon.NUMERIC.search(module.content):
        out.append(_finding(
            module.id, FindingType.OMISSION, Severity.MEDIUM,
            evidence=(sentences[0][:60] if sentences else module.title),
            reason="非功能要求缺少可度量指标（并发量、响应时间、可用率）",
            suggestion="给出具体数值目标和测量方法",
        ))
    return out


def _clarity(module: ReqModule, sentences: list[str]) -> list[AmbiguityFinding]:
    out: list[AmbiguityFinding] = []
    for s in sentences:
        if s.startswith(lexicon.PRONOUN_START) and len(s) <= 20:
            out.append(_finding(
                module.id, FindingType.CLARITY, Severity.LOW,
                evidence=s,
                reason="主语缺失或指代不明，读者无法确定操作对象",
                suggestion="把代词替换为明确的业务实体名",
            ))
        elif s.rstrip().endswith(("等", "等等")):
            out.append(_finding(
                module.id, FindingType.CLARITY, Severity.LOW,
                evidence=s,
                reason="开放式枚举（以「等」结尾），范围不确定",
                suggestion="列出完整清单，或明确「等」的边界",
            ))
    return out


# ---------------------------------------------------------------- LLM 层

def _by_llm(client, module: ReqModule) -> list[AmbiguityFinding]:
    reply = client.complete(_SYSTEM_PROMPT, _build_prompt(module))
    out: list[AmbiguityFinding] = []
    for raw in extract_json_array(reply.text):
        if not isinstance(raw, dict):      # 垃圾条目（字符串/数字/null）直接跳过
            continue
        ftype = str(raw.get("type", "")).strip().lower()
        evidence = str(raw.get("evidence", "")).strip()
        reason = str(raw.get("reason", "")).strip()
        if ftype not in _VALID_TYPES or not evidence or not reason:
            continue                       # 结构非法直接丢，不猜
        sev = str(raw.get("severity", "medium")).strip().lower()
        out.append(_finding(
            module.id, FindingType(ftype),
            Severity(sev) if sev in _VALID_SEVERITY else Severity.MEDIUM,
            evidence=evidence[:200],
            reason=reason[:200],
            suggestion=str(raw.get("suggestion", ""))[:200],
            source="llm",
        ))
    return out


def _build_prompt(module: ReqModule) -> str:
    return (
        f"章节：{module.title}（ID={module.id}）\n"
        f"正文：\n{module.content}\n\n"
        "找出该章节的问题，类型只能是：ambiguity(歧义) / conflict(冲突) / "
        "omission(遗漏) / clarity(表述不清)。\n"
        "要求：\n"
        "- 只报告会导致返工或无法写测试用例的问题，不要泛泛而谈；\n"
        "- evidence 必须是正文原句片段，不得改写；\n"
        "- 同一句只报一次，同一问题不要重复；\n"
        "- 没有问题时输出空数组 []。\n\n"
        "输出 JSON 数组，元素结构：\n"
        + json.dumps(
            {
                "type": "ambiguity|conflict|omission|clarity",
                "severity": "high|medium|low",
                "evidence": "原文片段",
                "reason": "一句话说明为什么是问题",
                "suggestion": "一句话修改建议",
            },
            ensure_ascii=False,
        )
    )


# ---------------------------------------------------------------- 工具

def _finding(
    module_id: str,
    ftype: FindingType,
    severity: Severity,
    evidence: str,
    reason: str,
    suggestion: str = "",
    source: str = "rule",
) -> AmbiguityFinding:
    return AmbiguityFinding(
        id="",                           # analyze() 末尾统一重排
        module_id=module_id,
        type=ftype,
        severity=severity,
        evidence=evidence,
        reason=reason,
        suggestion=suggestion,
        source=source,
    )


def _direction(text: str) -> str:
    if any(w in text for w in lexicon.LOWER_BOUND):
        return "lower"
    if any(w in text for w in lexicon.UPPER_BOUND):
        return "upper"
    return ""


def _is_core(text: str) -> bool:
    return any(w in text for w in _CORE_WORDS)


def _dedupe(findings: list[AmbiguityFinding]) -> list[AmbiguityFinding]:
    """规则层先入，LLM 层重复的（同模块+同类型+同证据片段）丢弃。"""
    seen: set[tuple[str, str, str]] = set()
    out: list[AmbiguityFinding] = []
    for f in findings:
        key = (f.module_id, f.type.value, f.evidence[:20])
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out
