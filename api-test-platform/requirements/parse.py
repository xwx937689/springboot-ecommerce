"""Phase 16 - PRD 解析：markdown -> 模块树。

算法（栈式一遍扫描，纯规则，无 LLM，离线可复现）：
  1. 逐行读文件；
  2. 进入代码围栏（``` 或 ~~~）后，直到闭合前【不识别标题】、内容也不进正文，
     只在所属模块上累加 code_blocks 计数
     （否则示例里的 "# 请求参数" 会被当成真实章节，把模块树拆歪）；
  3. 遇到标题行（# ~ ######）：按级别弹栈，直到栈顶级别 < 当前级别，
     然后挂到栈顶模块（或根列表）的 children 上；
  4. 遇到正文行：追加到栈顶模块的 content；
  5. 首个标题之前的内容归入虚拟模块 "(前言)"（level=0），空则丢弃。

为什么用"栈"而不是递归/两次遍历：
  标题天然是"任意深度、按出现顺序"的嵌套结构，栈能在 O(n) 内一遍建完树，
  且不需要预先知道文档有几级标题（比 WHartTest 的"按 split_level 切块"更简单）。

已知不支持（有意为之，PRD 里极少见）：
  - setext 标题（=== / --- 下划线式）；
  - 未闭合的围栏：余下全文按代码处理，不报错（宁可少解析，不可拆错树）。
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import ReqModule

# 匹配 markdown 标题：最多 3 个前导空格 + 1~6 个 # + 至少一个空格，末尾 # 可有可无
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")

# 代码围栏：最多 3 个前导空格 + 3 个以上反引号或波浪号
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def parse_markdown(path: Path) -> list[ReqModule]:
    """把 markdown 文件解析成模块树，返回根节点列表。"""
    text = path.read_text(encoding="utf-8-sig")   # utf-8-sig 兼容带 BOM 的文件
    roots: list[ReqModule] = []
    seq = 0                                        # 模块序号 -> M001, M002 ...

    def new_module(title: str, level: int) -> ReqModule:
        nonlocal seq
        seq += 1
        return ReqModule(id=f"M{seq:03d}", title=title, level=level)

    # 虚拟前言节点：保证"首个标题前就有正文"的情况不丢内容
    preface = new_module("(前言)", 0)
    stack: list[ReqModule] = [preface]   # 栈底永远是 preface，栈顶是最深模块

    in_fence = False          # 是否正处于代码围栏内
    fence_marker = ""         # 开启围栏的标记，如 "```" / "~~~~"

    for raw_line in text.splitlines():
        fence = _FENCE.match(raw_line)
        if in_fence:
            # 闭合条件：同种字符 + 长度不短于开启标记（``` 闭不上 ~~~）
            if fence and fence.group(1)[0] == fence_marker[0] \
                    and len(fence.group(1)) >= len(fence_marker):
                in_fence = False
                fence_marker = ""
            continue                     # 围栏内既不当标题，也不进正文

        if fence:
            in_fence = True
            fence_marker = fence.group(1)
            stack[-1].code_blocks += 1   # 记一笔，便于人工核对"内容去哪了"
            continue

        m = _HEADING.match(raw_line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            # 弹栈：把所有"级别 >= 当前级别"的模块关掉（它们是当前标题的平级或子级）
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            node = new_module(title, level)
            if stack[-1].level == 0:
                roots.append(node)        # 挂在根上（h1 或孤儿 h2）
            else:
                stack[-1].children.append(node)
            stack.append(node)
        else:
            # 正文行：追加到当前最深模块
            stack[-1].content += raw_line + "\n"

    # 清理：去掉空白的 content；若前言没内容则丢弃
    _strip_content(roots)
    result = roots
    if preface.content.strip() or preface.children:
        result = [preface] + roots
    _renumber(result)          # 丢弃前售后重排编号，保证从 M001 连续
    return result


def _strip_content(modules: list[ReqModule]) -> None:
    """递归去掉 content 首尾空白，并下探子节点。"""
    for m in modules:
        m.content = m.content.strip()
        _strip_content(m.children)


def _renumber(modules: list[ReqModule], counter: list[int] | None = None) -> None:
    """前序遍历重排 ID：前序顺序 == 文档出现顺序，保证 M001 起连续编号。"""
    counter = counter if counter is not None else [0]
    for m in modules:
        counter[0] += 1
        m.id = f"M{counter[0]:03d}"
        _renumber(m.children, counter)


def print_tree(modules: list[ReqModule], indent: str = "") -> None:
    """调试辅助：把模块树打印成人能看的样子。"""
    for m in modules:
        bar = "#" * m.level if m.level else "(root)"
        code = f" 代码块×{m.code_blocks}" if m.code_blocks else ""
        print(f"{indent}{m.id} {bar} {m.title}  [{len(m.content)} 字{code}]")
        print_tree(m.children, indent + "    ")
