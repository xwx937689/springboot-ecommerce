"""Phase 9 CLI - 失败归因分析入口。

用法（在 api-test-platform 目录）:
  python -m analyzer.cli                       # 归因最新 results.json
  python -m analyzer.cli --results artifacts/baseline-phase8-20260930/results.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from .classify import analyze


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="AI 失败归因（Phase 9）")
    p.add_argument("--results", default=str(root / "artifacts" / "results.json"))
    p.add_argument("--out", default=str(root / "artifacts" / "failure-report.json"))
    args = p.parse_args(argv)

    report = analyze(results_path=Path(args.results), out_path=Path(args.out))
    s = report["summary"]
    print("[OK] 失败归因完成")
    print(f"  失败总数: {s['failures']}  (LLM 批次 成功/失败: {s['llm_batches_ok']}/{s['llm_batches_failed']})")
    print("  分类分布:")
    for cls, n in sorted(s["by_class"].items(), key=lambda kv: -kv[1]):
        bar = "#" * max(1, round(n / max(s["failures"], 1) * 40))
        print(f"    {cls:17s} {n:4d}  {bar}")
    print(f"  产物 -> {args.out}")

    # 真缺陷候选置顶输出（作品/提单入口）
    defects = [v for v in report["verdicts"] if v["classification"] == "REAL_DEFECT"]
    if defects:
        print(f"\n[!] 真缺陷候选 {len(defects)} 条（建议提单）:")
        for v in defects[:10]:
            print(f"    - {v['case_id']}: {v['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
