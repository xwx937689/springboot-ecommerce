"""extract() 双引擎切换的回归测试。

历史 bug（2026-11-28 外部代码审查发现）：LLM 成功路径漏置 used_llm=True，
导致成功模块仍执行规则引擎 → 双引擎重复产出 BR/AC + fallback 统计失真。
产物（d422b884 那次分析）生成时代码正确（fallback=failed=4 可证），
本 bug 是其后重构引入的回归——用测试钉死，谁再丢赋值谁红。

判别信号：review_status——LLM 条目必为 pending（须人工复核），
规则引擎条目必为 approved（原文直取）。断言不依赖具体分词行为。
"""
import json
from types import SimpleNamespace

import pytest

from requirements import extract as ex
from requirements.extract import LLMError
from requirements.models import ReqModule


def _module(content: str = "系统响应时间应低于2秒。") -> ReqModule:
    return ReqModule(id="M001", title="性能需求", level=2, content=content)


class FakeClient:
    """替身 client：complete() 返回预置 reply 或抛异常。"""

    def __init__(self, reply: str | Exception):
        self.reply = reply
        self.calls = 0

    def complete(self, system: str, user: str):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(text=self.reply)


def _llm_reply(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False)


def _run(monkeypatch, client, content="系统响应时间应低于2秒。"):
    monkeypatch.setattr(ex, "make_client", lambda settings: client)
    rules, criteria, stats = ex.extract(
        [_module(content)], settings=SimpleNamespace(provider="openai")
    )
    return rules, criteria, stats


# LLM 输出：ref 关联齐全的合法条目（evidence 必须是正文子串，否则被幻觉防线丢弃）
_GOOD_ITEMS = [
    {"kind": "rule", "ref": "R1", "text": "系统响应时间应低于2秒",
     "evidence": "系统响应时间应低于2秒", "category": "constraint"},
    {"kind": "ac", "derived_from": "R1", "text": "响应时间低于2秒即可判定通过",
     "evidence": "系统响应时间应低于2秒", "testability": "testable"},
]


def test_llm_success_skips_rule_engine(monkeypatch):
    """LLM 成功：只保留 LLM 结果，规则引擎不得再跑一遍（历史 bug 的直接回归钉）。"""
    client = FakeClient(_llm_reply(_GOOD_ITEMS))
    rules, criteria, stats = _run(monkeypatch, client)

    assert client.calls == 1
    assert len(rules) == 1 and len(criteria) == 1          # 无重复产出
    assert all(r.review_status == "pending" for r in rules)   # 全是 LLM 产物
    assert stats["llm_modules_ok"] == 1
    assert stats["llm_modules_fallback"] == 0              # 成功不算回退
    # 全局序号只被 LLM 消费一次：ID 连续不跳号
    assert rules[0].id == "BR-001" and criteria[0].id == "AC-001"


def test_llm_failure_falls_back_to_rule_engine(monkeypatch):
    """LLM 失败：该模块整体回退规则引擎（设计文档承诺的行为）。"""
    client = FakeClient(LLMError("网络超时"))
    rules, criteria, stats = _run(monkeypatch, client)

    assert len(rules) >= 1 and len(criteria) >= 1          # 规则引擎有产出
    assert all(r.review_status == "approved" for r in rules)  # 全是规则引擎产物
    assert stats["llm_modules_failed"] == 1
    assert stats["llm_modules_fallback"] == 1


def test_llm_partial_dropped_no_fallback(monkeypatch):
    """LLM 部分条目被丢弃：保留存活条目，不回退规则引擎（防重复生成）。

    设计决定：被丢弃的是"结构非法/无原文证据"的垃圾条目，回退重跑只会
    把存活条目再产一遍——宁缺勿重。丢弃数已由 llm_modules_partial 显式记账。
    """
    items = _GOOD_ITEMS + [
        {"kind": "rule", "text": "凭空捏造的规则", "evidence": "正文中不存在的句子"},
    ]
    client = FakeClient(_llm_reply(items))
    rules, criteria, stats = _run(monkeypatch, client)

    assert len(rules) == 1 and len(criteria) == 1           # 只有 LLM 存活条目
    assert all(r.review_status == "pending" for r in rules)
    assert stats["llm_modules_partial"] == 1
    assert stats["llm_modules_fallback"] == 0


def test_llm_empty_output_falls_back(monkeypatch):
    """LLM 返回空数组（合法 JSON 但零产出）：视为失败，回退规则引擎。"""
    client = FakeClient(_llm_reply([]))
    rules, criteria, stats = _run(monkeypatch, client)

    assert len(rules) >= 1                                  # 规则引擎兜底有产出
    assert stats["llm_modules_failed"] == 1
    assert stats["llm_modules_fallback"] == 1
