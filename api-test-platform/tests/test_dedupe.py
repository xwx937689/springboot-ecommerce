"""testpoints 去重单测：三元组精确键 + 语义签名（AC-013 案例回归）。

历史背景：规则引擎 TP「边界：取 5 个（恰好等于上限）应通过」与 LLM TP
「恰好 5 条通过」语义完全相同、措辞不同——精确标题去重挡不住，
签名去重（ac_id + kind + 首个数值 + 判定方向）负责兜底。
"""
from requirements.models import TestPoint as TPModel  # 别名避开 pytest 的 Test* 收集规则
from requirements.testpoints import _dedupe_tps, _sig_key


def _tp(title: str, ac: str = "AC-013", kind: str = "boundary",
        expected: str = "e", rules: tuple = ("BR-013",)) -> TPModel:
    return TPModel(
        id="", ac_id=ac, rule_ids=list(rules), module_id="M004",
        title=title, kind=kind, priority="P1",
        preconditions="", steps=[], expected_results=expected,
    )


# ---------- 语义签名：该合并的 ----------

def test_sig_dedupe_merges_pass_side_paraphrase():
    tps = [_tp("边界：取 5 个（恰好等于上限）应通过"),
           _tp("恰好 5 条通过")]
    out, merged = _dedupe_tps(tps)
    assert len(out) == 1 and merged == 1

def test_sig_dedupe_merges_reject_side_paraphrase():
    tps = [_tp("边界：取 6 个（超过上限 5）应被拒绝"),
           _tp("6 条失败")]
    out, merged = _dedupe_tps(tps)
    assert len(out) == 1 and merged == 1

# ---------- 语义签名：不该合并的（宁可漏合并，不可误合并）----------

def test_sig_dedupe_keeps_different_direction():
    tps = [_tp("取 5 个通过"), _tp("取 5 个应被拒绝")]
    out, _ = _dedupe_tps(tps)
    assert len(out) == 2

def test_sig_dedupe_keeps_different_ac():
    tps = [_tp("取 5 个通过", ac="AC-013"), _tp("恰好 5 条通过", ac="AC-014")]
    out, _ = _dedupe_tps(tps)
    assert len(out) == 2

def test_sig_dedupe_keeps_different_number():
    tps = [_tp("取 5 个通过"), _tp("取 6 个通过")]
    out, _ = _dedupe_tps(tps)
    assert len(out) == 2

def test_no_numbers_not_merged_by_sig():
    # 无数值的措辞差异不参与签名（信息不足，宁漏勿误），只走三元组精确键
    tps = [_tp("高可用集群部署验证", kind="positive", expected="a"),
           _tp("集群部署的高可用验证", kind="positive", expected="a")]
    out, merged = _dedupe_tps(tps)
    assert len(out) == 2 and merged == 0

# ---------- 追溯不丢：去重必须合并 rule_ids ----------

def test_triple_exact_merge_keeps_trace():
    a = _tp("边界：取 7 位（低于下限 8）应被拒绝", rules=("BR-005",))
    b = _tp("边界：取 7 位（低于下限 8）应被拒绝", rules=("BR-006",))
    out, merged = _dedupe_tps([a, b])
    assert len(out) == 1 and merged == 1
    assert set(out[0].rule_ids) == {"BR-005", "BR-006"}

def test_sig_merge_keeps_trace():
    a = _tp("边界：取 5 个（恰好等于上限）应通过", rules=("BR-009",))
    b = _tp("恰好 5 条通过", rules=("BR-009", "BR-010"))
    out, _ = _dedupe_tps([a, b])
    assert len(out) == 1
    assert set(out[0].rule_ids) == {"BR-009", "BR-010"}

# ---------- 签名函数本身 ----------

def test_sig_key_none_without_numbers():
    assert _sig_key(_tp("高可用验证")) is None


# ---------- 第三层：同模块同标题硬约束 ----------

def test_title_layer_merges_cross_ac_same_title():
    # 真实案例：AC-069/AC-085 都产"未盖章体检单重复提交"，预期措辞略异
    a = _tp("未盖章体检单重复提交", ac="AC-069", kind="exception",
            expected="系统拒绝该体检单进入体检环节")
    b = _tp("未盖章体检单重复提交", ac="AC-085", kind="exception",
            expected="系统拒绝该体检单进入体检环节并给出未盖章提示")
    out, merged = _dedupe_tps([a, b])
    assert len(out) == 1 and merged == 1
    # 保留更完整的预期
    assert "未盖章提示" in out[0].expected_results
    # 副 AC 留痕，追溯不丢
    assert "AC-085" in out[0].notes

def test_title_layer_global_uniqueness():
    # 标尺 no_duplicate_titles 是全局口径，生成端镜像同口径：跨子模块同名也合并
    a = _tp("重复提交应被拒绝", ac="AC-001")
    b = _tp("重复提交应被拒绝", ac="AC-002")
    b.module_id = "M099"
    out, merged = _dedupe_tps([a, b])
    assert len(out) == 1 and merged == 1
    assert "AC-002" in out[0].notes   # 副 AC 留痕
