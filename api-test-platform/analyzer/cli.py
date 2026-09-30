"""Phase 9 CLI - 失败归因分析 + Phase 10 修复回灌。

用法（在 api-test-platform 目录）:
  python -m analyzer.cli                       # 归因最新 results.json
  python -m analyzer.cli --repair              # 归因 + 生成修复版用例
  python -m analyzer.cli --repair-only         # 跳过归因，直接用已有 failure-report.json 修复
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .classify import analyze
from .repair import load_actual, repair


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="AI 失败归因（Phase 9）+ 修复回灌（Phase 10）")
    p.add_argument("--results", default=str(root / "artifacts" / "results.json"))
    p.add_argument("--out", default=str(root / "artifacts" / "failure-report.json"))
    p.add_argument("--cases", default=str(root / "artifacts" / "testcases.json"))
    p.add_argument("--repair-out", default=str(root / "artifacts" / "testcases-repaired.json"))
    p.add_argument("--repair", action="store_true", help="归因后生成修复版用例")
    p.add_argument("--repair-only", action="store_true", help="跳过 LLM 归因，直接回灌已有报告")
    args = p.parse_args(argv)

    if not args.repair_only:
        report = analyze(results_path=Path(args.results), out_path=Path(args.out))
        s = report["summary"]
        print("[OK] 失败归因完成")
        print(f"  失败总数: {s['failures']}  (LLM 批次 成功/失败: {s['llm_batches_ok']}/{s['llm_batches_failed']})")
        for cls, n in sorted(s["by_class"].items(), key=lambda kv: -kv[1]):
            bar = "#" * max(1, round(n / max(s["failures"], 1) * 40))
            print(f"    {cls:17s} {n:4d}  {bar}")
        print(f"  产物 -> {args.out}")
        defects = [v for v in report["verdicts"] if v["classification"] == "REAL_DEFECT"]
        if defects:
            print(f"\n[!] 真缺陷候选 {len(defects)} 条（建议提单）:")
            for v in defects[:10]:
                print(f"    - {v['case_id']}: {v['reason']}")

    if args.repair or args.repair_only:
        load_actual(Path(args.results))
        stat = repair(Path(args.out), Path(args.cases), Path(args.repair_out))
        print(f"\n[OK] 修复回灌完成: 修复 {stat['repaired']} 条, 跳过 {stat['skipped']} 条"
              f"（REAL_DEFECT {stat['untouched_real_defects']} 条保持原样待提单）")
        print(f"  修复版用例 -> {args.repair_out}")
        print("  验证: python -m executor.cli --cases artifacts/testcases-repaired.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
