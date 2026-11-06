"""Phase 16 - 测试点生成：AC/BR -> TestPoint。

生成策略（规则引擎，确定性，mock 可跑）：
  每条可测 AC 必产 1 个正向测试点；再按规则类型补充：

    AC 依据的 BR 含数值边界
      ├─ "至少 N"（下限）-> boundary: N-1 应拒绝
      └─ "最多/上限 N"（上限）-> boundary: N 应通过 + N+1 应拒绝
    BR/AC 含权限词（不能/禁止/仅）
      -> negative: 非法身份应被拒绝
    BR 无任何 AC 覆盖（约束类/权限类）
      -> 直接从 BR 补 1 个测试点（保证规则全覆盖，喂追溯覆盖率指标）

    模糊 AC（testable=False）不生成 -> 计入 vague_blocked，
    这部分正是 analyze.py 要报的歧义，先不编造测试点。

LLM 增强（provider != mock 时）：
  逐模块补充业务语义测试点（异常流/组合场景），输出必须引用已有 ac_id，
  引用不存在的 ac_id 直接丢弃（追溯由校验保证，不是约定）。

与后续阶段的串联（本阶段只设计，不改 generator）：
  Phase 5 generator 增加可选 --requirements 输入，
  TestCase 加 req_trace 字段（tp_id/ac_id/module_id），
  executor/analyzer 沿用它，失败即可追溯到需求源头。
"""
from __future__ import annotations

import json
import re

from generator.config import LLMSettings
from generator.llm import LLMError, extract_json_array, make_client

from . import lexicon
from .extract import _walk
from .models import (
    AcceptanceCriterion,
    BusinessRule,
    Priority,
    ReqModule,
    TestPoint,
    TestPointKind,
)

_SYSTEM_PROMPT = (
    "你是资深测试设计师，基于给出的验收标准补充业务语义测试点"
    "（异常流、状态组合、幂等/重复提交等）。只输出 JSON 数组。"
)

_TP_KINDS = {"positive", "boundary", "negative", "exception"}
_PERMISSION = ("不能", "不得", "禁止", "不允许", "仅", "只允许")
_CORE_WORDS = ("支付", "订单", "登录", "注册", "库存")   # 核心业务词 -> P0


def generate(
    modules: list[ReqModule],
    rules: list[BusinessRule],
    criteria: list[AcceptanceCriterion],
    settings: LLMSettings | None = None,
) -> tuple[list[TestPoint], dict]:
    """生成 (test_points, stats)。"""
    settings = settings or LLMSettings.from_env()
    client = make_client(settings) if settings.provider != "mock" else None

    rule_by_id = {r.id: r for r in rules}
    tps: list[TestPoint] = []

    # 1) 逐可测 AC 生成
    for ac in criteria:
        if not ac.testable:
            continue
        ref_rules = [rule_by_id[rid] for rid in ac.rule_ids if rid in rule_by_id]
        tps.extend(_tps_from_ac(ac, ref_rules))

    # 2) 没被任何 AC 覆盖的 BR 补测试点（约束/权限类才有测试价值）
    covered = {rid for ac in criteria if ac.testable for rid in ac.rule_ids}
    for r in rules:
        if r.id not in covered and r.category in ("constraint", "permission"):
            tps.append(_tp_from_rule(r))

    stats = {
        "criteria": len(criteria),
        "testable": sum(1 for c in criteria if c.testable),
        "vague_blocked": sum(1 for c in criteria if not c.testable),
        "brs_total": len(rules),
        "brs_covered": len(
            {rid for ac in criteria if ac.testable for rid in ac.rule_ids}
            | {_rule_id_of(tp) for tp in tps if _rule_id_of(tp)}
        ),
        "llm_modules_ok": 0,
        "llm_modules_failed": 0,
    }

    # 3) LLM 增强：逐模块补充业务语义测试点
    if client is not None:
        ac_by_module: dict[str, list[AcceptanceCriterion]] = {}
        for ac in criteria:
            if ac.testable:
                ac_by_module.setdefault(ac.module_id, []).append(ac)
        for module in _walk(modules):
            acs = ac_by_module.get(module.id)
            if not acs:
                continue
            try:
                tps.extend(_enrich_by_llm(client, module, acs))
                stats["llm_modules_ok"] += 1
            except (LLMError, json.JSONDecodeError, ValueError,
                    AttributeError, TypeError) as exc:
                stats["llm_modules_failed"] += 1
                print(f"[WARN] LLM 测试点增强失败 {module.id}: {exc}")

    for i, tp in enumerate(tps, 1):
        tp.id = f"TP-{i:03d}"          # 统一重排，保证连续
    stats["test_points"] = len(tps)
    stats["by_kind"] = {k: sum(1 for t in tps if t.kind == k) for k in sorted(_TP_KINDS)}
    return tps, stats


# ---------------------------------------------------------------- 规则引擎

def _tps_from_ac(ac: AcceptanceCriterion, ref_rules: list[BusinessRule]) -> list[TestPoint]:
    """一条可测 AC -> 正向 + 边界 + 负向测试点（全部带上 rule_ids 追溯）。"""
    rule_ids = [r.id for r in ref_rules]
    out = [
        TestPoint(
            id="",
            ac_id=ac.id,
            rule_ids=rule_ids,
            module_id=ac.module_id,
            title=f"正向：{ac.text[:40]}",
            kind="positive",
            priority=_priority(ac.text, ref_rules),
        )
    ]
    for r in ref_rules:
        out.extend(_boundary_tps(r, ac))
    if any(w in ac.text for w in _PERMISSION) or any(
        w in r.text for r in ref_rules for w in _PERMISSION
    ):
        out.append(
            TestPoint(
                id="",
                ac_id=ac.id,
                rule_ids=rule_ids,
                module_id=ac.module_id,
                title=f"负向：以未授权/非法身份执行 —— {ac.text[:34]}",
                kind="negative",
                priority="P0",
                notes="依据权限类规则，验证系统拒绝",
            )
        )
    return out


def _boundary_tps(rule: BusinessRule, ac: AcceptanceCriterion) -> list[TestPoint]:
    """从规则文本抠数值边界：下限产 N-1，上限产 N 和 N+1。"""
    m = lexicon.NUMBER_UNIT.search(rule.text)
    if not m:
        return []
    n, unit = int(m.group(1)), m.group(2)
    tps: list[TestPoint] = []
    if any(w in rule.text for w in lexicon.LOWER_BOUND):
        tps.append(TestPoint(
            id="", ac_id=ac.id, rule_ids=[rule.id], module_id=ac.module_id,
            title=f"边界：取 {n - 1}{unit}（低于下限 {n}）应被拒绝",
            kind="boundary", priority="P1", notes=f"依据 {rule.id}",
        ))
    elif any(w in rule.text for w in lexicon.UPPER_BOUND):
        tps.append(TestPoint(
            id="", ac_id=ac.id, rule_ids=[rule.id], module_id=ac.module_id,
            title=f"边界：取 {n}{unit}（恰好等于上限）应通过",
            kind="boundary", priority="P1", notes=f"依据 {rule.id}",
        ))
        tps.append(TestPoint(
            id="", ac_id=ac.id, rule_ids=[rule.id], module_id=ac.module_id,
            title=f"边界：取 {n + 1}{unit}（超过上限 {n}）应被拒绝",
            kind="boundary", priority="P1", notes=f"依据 {rule.id}",
        ))
    return tps


def _tp_from_rule(rule: BusinessRule) -> TestPoint:
    """没有 AC 覆盖的 BR 直接出测试点，用 rule_ids 建立数据级追溯。"""
    kind: TestPointKind = "negative" if rule.category == "permission" else "positive"
    return TestPoint(
        id="",
        ac_id="",                  # 该规则无 AC 覆盖
        rule_ids=[rule.id],        # 追溯直达 BR 层，不再只靠 notes 字符串
        module_id=rule.module_id,
        title=f"{'负向' if kind == 'negative' else '正向'}：{rule.text[:40]}",
        kind=kind,
        priority=_priority(rule.text, [rule]),
        notes=f"来源：{rule.id}（无 AC 覆盖，建议补验收标准）",
    )


def _rule_id_of(tp: TestPoint) -> str:
    m = re.search(r"(BR-\d{3})", tp.notes or "")
    return m.group(1) if m else ""


def _priority(text: str, rules: list[BusinessRule]) -> Priority:
    all_text = text + " " + " ".join(r.text for r in rules)
    return "P0" if any(w in all_text for w in _CORE_WORDS) else "P1"


# ---------------------------------------------------------------- LLM 增强

def _enrich_by_llm(
    client, module: ReqModule, acs: list[AcceptanceCriterion]
) -> list[TestPoint]:
    reply = client.complete(_SYSTEM_PROMPT, _build_prompt(module, acs))
    raw_items = extract_json_array(reply.text)
    ac_by_id = {ac.id: ac for ac in acs}      # 只允许引用本模块的 AC
    out: list[TestPoint] = []
    for raw in raw_items:
        if not isinstance(raw, dict):      # 垃圾条目（字符串/数字/null）直接跳过
            continue
        ac_id = str(raw.get("ac_id", "")).strip()
        title = str(raw.get("title", "")).strip()
        kind = str(raw.get("kind", "")).strip().lower()
        if ac_id not in ac_by_id or not title or kind not in _TP_KINDS:
            continue                          # 追溯校验：引用不存在即丢弃
        prio = str(raw.get("priority") or "P2").strip().upper()
        if prio not in ("P0", "P1", "P2"):    # Literal 只认 P0~P2，非法降级不报废
            prio = "P2"
        out.append(
            TestPoint(
                id="",
                ac_id=ac_id,
                rule_ids=list(ac_by_id[ac_id].rule_ids),   # 继承 AC 的规则追溯
                module_id=module.id,
                title=title[:80],
                kind=kind,                    # type: ignore[arg-type]
                priority=prio,                # type: ignore[arg-type]
                notes=(str(raw.get("notes") or "")[:200] + "（来源：llm）").strip(),
            )
        )
    return out


def _build_prompt(module: ReqModule, acs: list[AcceptanceCriterion]) -> str:
    ac_lines = "\n".join(f"- {a.id}: {a.text}" for a in acs)
    return (
        f"章节：{module.title}（ID={module.id}）\n"
        f"已有验收标准：\n{ac_lines}\n\n"
        "为每条验收标准补充 1~2 个业务语义测试点（异常流/状态组合/重复提交等），\n"
        "每条必须引用上面已有的 ac_id。不要重复正向用例。\n\n"
        "输出 JSON 数组，元素结构：\n"
        + json.dumps(
            {
                "ac_id": "引用的验收标准 ID",
                "title": "测试点短名（中文）",
                "kind": "exception|boundary|negative|positive",
                "priority": "P0|P1|P2",
                "notes": "一句话设计依据",
            },
            ensure_ascii=False,
        )
    )
