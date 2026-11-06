"""Phase 16 CLI - 需求分析全链：PRD -> 模块树 -> 规则/验收标准 -> 问题 -> 测试点。

用法（在 api-test-platform 目录）:
  python -m requirements.cli                          # 跑示例 PRD
  python -m requirements.cli --prd my.md               # 指定文档
  python -m requirements.cli --provider openai         # 启用 LLM（默认跟随 .env）

退出码（文件类问题的收敛点都在本层，解析层只抛不吞）：
  0 成功
  1 文档为空 / 没解析出章节
  2 文件不存在或路径是目录
  3 文件不可读（权限/IO 错误）
  4 编码不是 UTF-8
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from generator.config import LLMSettings

from .analyze import analyze
from .extract import extract
from .models import (
    AnalysisMetrics,
    AnalysisStats,
    FindingType,
    RequirementAnalysis,
    Severity,
)
from .parse import parse_text
from .testpoints import generate

# 退出码常量：调用方（CI/脚本）按数字判断，不用猜
EXIT_OK = 0
EXIT_EMPTY = 1
EXIT_MISSING = 2
EXIT_UNREADABLE = 3
EXIT_ENCODING = 4


class _InputError(Exception):
    """输入文件问题，带退出码；由 run() 统一打印成人能懂的提示。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，中文输出会乱码/报错（与 analyzer/summary.py 同一处理）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Phase 16 需求分析")
    ap.add_argument("--prd", default=str(root / "requirements" / "samples" / "sample-prd.md"))
    ap.add_argument("--out", default=str(root / "artifacts" / "requirements.json"))
    ap.add_argument("--provider", default=None, help="覆盖 .env 的 LLM provider（默认跟随配置）")
    args = ap.parse_args(argv)

    settings = LLMSettings.from_env()
    if args.provider:
        settings.provider = args.provider
    return run(Path(args.prd), Path(args.out), settings)


def run(prd: Path, out: Path, settings: LLMSettings) -> int:
    # 1) 读文件：不存在/目录/权限/编码/空文件 全部在这里收敛
    try:
        text = _load_text(prd)
    except _InputError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return exc.code

    # 2) 解析结构（warnings = 非致命问题，如疑似 setext 标题）
    warnings: list[str] = []
    modules = parse_text(text, warnings)
    for w in warnings[:5]:                       # 最多提示 5 条，防止刷屏
        print(f"[WARN] {w}", file=sys.stderr)
    if len(warnings) > 5:
        print(f"[WARN] ...另有 {len(warnings) - 5} 条类似提示", file=sys.stderr)
    module_count = _count(modules)
    if module_count == 0:
        print(f"[WARN] 文档 {prd} 中没有解析出任何章节（缺少 markdown 标题）", file=sys.stderr)
        return EXIT_EMPTY

    # 2) 提取规则/验收标准
    rules, criteria, extract_stats = extract(modules, settings)

    # 3) 质量问题
    findings, analyze_stats = analyze(modules, rules, settings)

    # 4) 测试点
    test_points, tp_stats = generate(modules, rules, criteria, settings)

    # 5) 指标 + 落盘（stats 用固定结构的 AnalysisStats，报表/CI 不依赖自由 dict）
    metrics = _metrics(criteria, findings, test_points, analyze_stats, tp_stats)
    analysis = RequirementAnalysis(
        schema_version="1.0",
        source_file=str(prd),
        source_name=prd.name,
        source_path=_relative(prd),
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        analyzed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        modules=modules,
        rules=rules,
        criteria=criteria,
        findings=findings,
        test_points=test_points,
        stats=AnalysisStats(
            modules=module_count,
            rules=len(rules),
            criteria=len(criteria),
            testable_criteria=sum(1 for c in criteria if c.testability == "testable"),
            partial_criteria=sum(1 for c in criteria if c.testability == "partial"),
            untestable_criteria=sum(1 for c in criteria if c.testability == "untestable"),
            test_points=len(test_points),
            findings=len(findings),
            high_findings=sum(1 for f in findings if f.severity == Severity.HIGH),
            metrics=AnalysisMetrics(**metrics),
            stages={                       # 各阶段原始统计，仅供排查，报表别依赖
                "extract": extract_stats,
                "analyze": analyze_stats,
                "testpoints": tp_stats,
            },
        ),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _print_summary(analysis, metrics, out)
    return EXIT_OK


def _relative(path: Path) -> str:
    """相对路径优先（跨机器可读），不在 cwd 下就退回原样。"""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _load_text(path: Path) -> str:
    """读 PRD 文本，把 I/O 异常翻译成【带退出码 + 人能看懂】的提示。

    覆盖 4 类输入问题（对应退出码 2/2/3/4 与 1）：
      - 文件不存在 / 传了个目录
      - 权限不足或磁盘读失败（PermissionError/OSError）
      - 编码不是 UTF-8（UnicodeDecodeError，常见于 Windows 记事本存成 GBK）
      - 内容是空白（只有空格/换行）
    """
    if not path.exists():
        raise _InputError(EXIT_MISSING, f"需求文档不存在: {path}")
    if path.is_dir():
        raise _InputError(EXIT_MISSING, f"这是目录不是文件: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except PermissionError:
        raise _InputError(EXIT_UNREADABLE, f"文件不可读（权限不足）: {path}") from None
    except UnicodeDecodeError:
        raise _InputError(
            EXIT_ENCODING,
            f"文件编码不是 UTF-8，无法解析: {path}（用记事本/VS Code 另存为 UTF-8 再试）",
        ) from None
    except OSError as exc:
        raise _InputError(EXIT_UNREADABLE, f"读取失败: {path}（{exc}）") from None
    if not text.strip():
        raise _InputError(EXIT_EMPTY, f"文件内容为空: {path}")
    return text


def _metrics(
    criteria, findings, test_points, analyze_stats: dict, tp_stats: dict
) -> dict:
    """4 个硬指标：全由结构化字段算出，不含 LLM 主观打分。"""
    n_crit = len(criteria)
    testable = sum(1 for c in criteria if c.testable)
    # 口径：只算规则引擎的歧义（规则层一句最多报一条，密度恒 ≤1）；
    # LLM 补充的歧义不计入该指标，避免分母失真（原始数据在 stats.stages.analyze）
    ambiguous = sum(
        1 for f in findings if f.type == FindingType.AMBIGUITY and f.source == "rule"
    )
    sentences = max(analyze_stats.get("sentences", 0), 1)
    return {
        "ac_testable_rate": round(testable / n_crit, 3) if n_crit else 0.0,
        "ambiguity_density": round(ambiguous / sentences, 3),
        "br_coverage": round(tp_stats["brs_covered"] / max(tp_stats["brs_total"], 1), 3),
        "tp_per_ac": round(len(test_points) / max(testable, 1), 2),
    }


def _print_summary(analysis: RequirementAnalysis, metrics: dict, out: Path) -> None:
    print(
        f"[OK] 需求分析完成: {_count(analysis.modules)} 模块 / "
        f"{len(analysis.rules)} 规则 / {len(analysis.criteria)} 验收标准 / "
        f"{len(analysis.test_points)} 测试点"
    )

    by_type: dict[str, int] = {}
    for f in analysis.findings:
        by_type[f.type.value] = by_type.get(f.type.value, 0) + 1
    total = max(len(analysis.findings), 1)
    print(f"\n  问题 {len(analysis.findings)} 条:")
    for cls, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        bar = "#" * max(1, round(n / total * 30))
        print(f"    {cls:10s} {n:3d}  {bar}")

    high = [f for f in analysis.findings if f.severity.value == "high"]
    if high:
        print(f"\n[!] 高优先级 {len(high)} 条（建议评审前澄清）:")
        for f in high[:8]:
            print(f"    - [{f.module_id}] {f.evidence[:50]}")
            print(f"      {f.reason}")

    print("\n  指标:")
    print(f"    AC 可测率      {metrics['ac_testable_rate'] * 100:5.1f}%")
    print(f"    歧义密度       {metrics['ambiguity_density']:5.3f} / 句")
    print(f"    BR 覆盖率      {metrics['br_coverage'] * 100:5.1f}%")
    print(f"    每 AC 测试点   {metrics['tp_per_ac']:5.2f} 个")
    print(f"\n  产物 -> {out}")


def _count(modules) -> int:
    """模块树节点总数（含子节点）。"""
    return sum(1 + _count(m.children) for m in modules)


if __name__ == "__main__":
    raise SystemExit(main())
