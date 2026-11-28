"""parse_text 建树单测：栈式解析的每个已知坑都固化成回归测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from requirements.parse import parse_text


def _flat_titles(modules: list) -> list[str]:
    """拍平模块树取 title 序列（前序遍历 == 文档出现顺序）。"""
    out: list[str] = []
    for m in modules:
        out.append(m.title)
        out.extend(_flat_titles(m.children))
    return out


# ---------- 基础建树 ----------

def test_h1_with_h2_children():
    roots = parse_text("# 用户管理\n## 登录\n登录正文\n## 注册\n注册正文")
    assert len(roots) == 1
    root = roots[0]
    assert root.title == "用户管理" and root.level == 1
    assert [c.title for c in root.children] == ["登录", "注册"]
    assert root.children[0].content == "登录正文"

def test_orphan_h2_goes_to_root():
    # 文档没有 h1 直接上 h2：不能丢（栈底 preface level=0，挂根）
    roots = parse_text("## 孤儿章节\n正文")
    assert len(roots) == 1 and roots[0].title == "孤儿章节"

def test_deep_nesting_pops_stack():
    # # A -> ## B -> ### C -> # D：D 出现时 A/B/C 全弹栈，D 回到根
    roots = parse_text("# A\n## B\n### C\n正文\n# D")
    assert _flat_titles(roots) == ["A", "B", "C", "D"]
    assert roots[1].title == "D" and roots[1].children == []

# ---------- 前言（虚拟模块） ----------

def test_preface_kept_when_present():
    roots = parse_text("这是前言说明。\n# 第一章\n正文")
    assert roots[0].title == "(前言)" and "这是前言说明" in roots[0].content
    assert roots[1].title == "第一章"

def test_preface_dropped_when_empty():
    roots = parse_text("# 第一章\n正文")
    assert [m.title for m in roots] == ["第一章"]   # 不留空壳

# ---------- 代码围栏（历史坑：示例里的 "# 请求参数" 拆歪树） ----------

def test_fence_hides_headings():
    text = "# 示例\n```\n# 请求参数\n~~~子标题\n```\n# 真实章节"
    roots = parse_text(text)
    assert _flat_titles(roots) == ["示例", "真实章节"]
    assert roots[0].code_blocks == 1                # 围栏记在所属模块上

def test_unclosed_fence_treats_rest_as_code():
    # 未闭合围栏：余下全文按代码处理，不拆出假章节（宁可少解析，不可拆错树）
    roots = parse_text("# 示例\n```\n# 请求参数\n正文也吞掉")
    assert _flat_titles(roots) == ["示例"]

# ---------- 非致命问题（warnings 通道） ----------

def test_setext_warning_but_not_heading():
    warnings: list[str] = []
    roots = parse_text("疑似标题\n===\n# 真章节", warnings)
    assert warnings and "Setext" in warnings[0]     # 主动提示，不静默
    # === 按正文处理：归入虚拟前言（非空则保留），不建假模块
    assert [m.title for m in roots] == ["(前言)", "真章节"]
    assert "===" in roots[0].content and "疑似标题" in roots[0].content

# ---------- 编号 ----------

def test_ids_renumbered_continuous_in_doc_order():
    roots = parse_text("# A\n## B\n# C")
    assert [m.id for m in roots] == ["M001", "M003"]        # 前序遍历连续
    assert roots[0].children[0].id == "M002"
