"""run_eval 打分函数的单测：每个历史踩过的坑都固化成一条回归测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 兜底：直接跑 pytest 也能找到包

from requirements.eval.run_eval import (
    _any_hit, check_binding, score_modules, score_rules, score_issue, score_tp_quality,
)


def _product(titles: list[str]) -> dict:
    """构造最小产物：模块树（children 嵌套，顶层平铺即可）。"""
    return {"modules": [{"title": t, "children": []} for t in titles]}


# ---------- score_modules ----------

def test_score_modules_all_hit():
    g = {"modules": ["用户登录", "购物车", "订单", "非功能要求"]}
    score, missed = score_modules(g, _product(g["modules"]))
    assert score == 1.0 and missed == []

def test_score_modules_one_missed():
    # 历史坑：分数曾焊反成"失败率"（全对得 0 分）
    g = {"modules": ["用户登录", "购物车", "订单", "不存在的模块"]}
    score, missed = score_modules(g, _product(["用户登录", "购物车", "订单"]))
    assert score == 0.75
    assert missed == ["不存在的模块"]

def test_score_modules_empty_golden_passes():
    # 历史坑：空 golden 曾返回 0.0
    score, missed = score_modules({"modules": []}, _product(["随便什么"]))
    assert score == 1.0 and missed == []

def test_score_modules_flattens_tree():
    # 模块藏在 children 里也要被找到
    g = {"modules": ["子模块A"]}
    p = {"modules": [{"title": "根", "children": [{"title": "子模块A", "children": []}]}]}
    score, _ = score_modules(g, p)
    assert score == 1.0


# ---------- score_rules ----------

def test_score_rules_must_have_only():
    g = {"rules": [
        {"text": "1 秒内响应", "must_have": True},
        {"text": "随便选考", "must_have": False},   # 选考的不进分母
    ]}
    p = {"rules": [{"text": "登录接口应在大约 1 秒内响应"}]}
    score, missed = score_rules(g, p)
    assert score == 1.0 and missed == []

def test_score_rules_no_must_passes():
    # 历史坑：must 为空时的放行语义
    score, missed = score_rules({"rules": [{"text": "x", "must_have": False}]},
                                {"rules": [{"text": "y"}]})
    assert score == 1.0 and missed == []


# ---------- _any_hit ----------

def test_any_hit_whitespace_insensitive():
    # 历史坑：'1秒内响应' vs '1 秒内响应' 曾误报冤案
    assert _any_hit("1秒内响应", ["登录接口应在大约 1 秒内响应"]) is True

def test_any_hit_case_insensitive():
    assert _any_hit("Cart", ["The cart limit"]) is True

def test_any_hit_miss():
    assert _any_hit("不存在的词", ["完全无关的内容"]) is False


# ---------- score_issue ----------

def _product_findings(evidences: list[str]) -> dict:
    return {"findings": [{"evidence": e, "reason": "测试"} for e in evidences]}

def test_score_issue_terms_and_conflicts():
    g = {"unambiguous_terms": ["尽快"],
         "must_find_conflicts": [{"keywords": ["50", "99"], "desc": "上限冲突"}]}
    p = _product_findings(["登录成功后尽快跳转", "上限 50 与 99 冲突"])
    score, missed = score_issue(g, p)
    assert score == 1.0 and missed == []

def test_score_issue_missed_conflict():
    g = {"unambiguous_terms": [], "must_find_conflicts": [{"keywords": ["50", "99"], "desc": "x"}]}
    # 只出现一个关键词（50），99 缺席 -> 冲突未命中（历史坑：fixture 曾写成"只有 50 没有 99"，
    # 99 也在句子里，共现判定正确发现冲突，测试意图永远触发不了）
    score, missed = score_issue(g, _product_findings(["上限只有 50，未提另一个值"]))
    assert missed == ["x"]


# ---------- score_tp_quality ----------

def test_tp_quality_empty_tps_fails_trace():
    # 历史坑：all([]) 恒 True 会把空产物误判为通过
    ok, fails = score_tp_quality({"tp_requirements": {"must_trace_to_ac": True}},
                                 {"test_points": []})
    assert ok is False and "must_trace_to_ac" in fails

def test_tp_quality_duplicate_titles():
    tps = [{"title": "重复", "ac_id": "AC-001", "rule_ids": ["BR-001"], "expected_results": "x"},
           {"title": "重复", "ac_id": "AC-001", "rule_ids": ["BR-001"], "expected_results": "x"}]
    ok, fails = score_tp_quality({"tp_requirements": {"no_duplicate_titles": True}},
                                 {"test_points": tps})
    assert "duplicate_titles" in fails


# ---------- check_binding（golden 版本绑定，防旧标尺重放） ----------

def test_binding_mismatch_rejected():
    assert check_binding({"product_sha256": "aaa"}, {"source_sha256": "bbb"}) is False

def test_binding_match_passes():
    assert check_binding({"product_sha256": "aaa"}, {"source_sha256": "aaa"}) is True

def test_binding_unbound_passes_through():
    # 任一侧未绑定不拦截——绑定是增强项，不是硬前提
    assert check_binding({}, {"source_sha256": "aaa"}) is True
    assert check_binding({"product_sha256": "aaa"}, {}) is True
