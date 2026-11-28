"""lexicon 词表单测：词表是规则引擎的全部"知识"，收敛纪律（P1-5/P1-6）用测试钉死。

lexicon 没有判定函数、只有常量与正则——测试对象就是这些数据本身：
正则的捕获/容错行为 + 历史收敛决策不被回退（谁把删掉的词加回来就红给谁看）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requirements.lexicon import (
    IDENTITY_WORDS, NUMBER_UNIT, PERMISSION_WORDS, RULE_MARKERS, TBD, VAGUE,
)


# ---------- NUMBER_UNIT：数值+单位正则（testpoints 造边界值、analyze 比冲突共用） ----------

def test_number_unit_captures_value_and_unit():
    m = NUMBER_UNIT.search("密码长度至少 8 位")
    assert m and m.group(1) == "8" and m.group(2) == "位"

def test_number_unit_whitespace_tolerant():
    # "1秒" 和 "1 秒" 都要命中（历史坑：排版空格导致漏检）
    assert NUMBER_UNIT.search("响应时间不超过1秒")
    assert NUMBER_UNIT.search("响应时间不超过 1 秒")

def test_number_unit_finds_all_bounds():
    assert len(NUMBER_UNIT.findall("至少 8 位，最多 16 位")) == 2

def test_number_unit_no_false_positive():
    # 无单位数字不算（"第8章"不是可判定边界）
    assert NUMBER_UNIT.search("这是第8章") is None

# ---------- 模糊词/待定：命中 + 不误报 ----------

def test_vague_and_tbd_hit():
    sentence = "系统应尽快响应，具体数值待定"
    assert any(w in sentence for w in VAGUE)
    assert any(w in sentence for w in TBD)

def test_clean_sentence_no_vague_no_tbd():
    clean = "用户登录成功后跳转到首页，超时 3 秒提示错误"
    assert not any(w in clean for w in VAGUE)
    assert not any(w in clean for w in TBD)

# ---------- 收敛纪律回归钉（历史坑不能回退） ----------

def test_p1_5_you_removed_from_rule_markers():
    # "由" 曾导致 "由此可见/由于" 误报成 BR，已删——不许加回来
    assert "由" not in RULE_MARKERS

def test_p1_6_bare_jin_is_not_permission():
    # 裸"仅"多表业务范围限制（"仅支持微信支付"），不是身份权限：
    # 必须在 RULE_MARKERS（是约束）但不在 PERMISSION_WORDS（非权限）
    assert "仅" in RULE_MARKERS
    assert "仅" not in PERMISSION_WORDS

def test_permission_and_identity_are_complementary_signals():
    # 权限词与身份词是两组不同信号：身份词本身不该混进权限词表
    for w in ("登录", "管理员", "本人"):
        assert w in IDENTITY_WORDS
        assert w not in PERMISSION_WORDS
