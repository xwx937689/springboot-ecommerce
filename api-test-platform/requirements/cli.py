"""Phase 16 CLI - 需求分析全链：PRD -> 模块树 -> 规则/验收标准 -> 问题 -> 测试点。

用法（在 api-test-platform 目录）:
  python -m requirements.cli                          # 跑示例 PRD
  python -m requirements.cli --prd my.md               # 指定文档
  python -m requirements.cli --provider openai         # 启用 LLM（默认跟随 .env）

退出码：0 成功 / 1 文档无内容 / 2 文件不存在（与 parser.cli 一致）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from generator.config import LLMSettings

from .analyze import analyze
from .extract import extract
from .models import FindingType, RequirementAnalysis
from .parse import parse_markdown
from .testpoints import generate


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
    if not prd.exists():
        print(f"[ERROR] 需求文档不存在: {prd}", file=sys.stderr)
        return 2

    # 1) 解析
    modules = parse_markdown(prd)
    module_count = _count(modules)
    if module_count == 0:
        print(f"[WARN] 文档 {prd} 中没有解析出任何章节", file=sys.stderr)
        return 1

    # 2) 提取规则/验收标准
    rules, criteria, extract_stats = extract(modules, settings)

    # 3) 质量问题
    findings, analyze_stats = analyze(modules, rules, settings)

    # 4) 测试点
    test_points, tp_stats = generate(modules, rules, criteria, settings)

    # 5) 指标 + 落盘
    metrics = _metrics(criteria, findings, test_points, analyze_stats, tp_stats)
    analysis = RequirementAnalysis(
        source_file=str(prd),
        modules=modules,
        rules=rules,
        criteria=criteria,
        findings=findings,
        test_points=test_points,
        stats={
            "extract": extract_stats,
            "analyze": analyze_stats,
            "testpoints": tp_stats,
            "metrics": metrics,
        },
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _print_summary(analysis, metrics, out)
    return 0


def _metrics(
    criteria, findings, test_points, analyze_stats: dict, tp_stats: dict
) -> dict:
    """4 个硬指标：全由结构化字段算出，不含 LLM 主观打分。"""
    n_crit = len(criteria)
    testable = sum(1 for c in criteria if c.testable)
    ambiguous = sum(1 for f in findings if f.type == FindingType.AMBIGUITY)
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
