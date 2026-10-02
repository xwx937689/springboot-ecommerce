"""Phase 14 CLI - 韧性场景执行入口。

用法（在 api-test-platform 目录）:
  python -m chaos.cli                          # 全部场景
  python -m chaos.cli --scenario degradation   # 单场景
  python -m chaos.cli --scenario payment-race --scenario oversell-race

产物: artifacts/chaos-report.json
退出码: 全部 PASS -> 0，任一 FAIL/异常 -> 1（CI 中建议以 continue-on-error 起步）
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .scenarios import SCENARIOS, run


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="Phase 14 韧性场景（故障注入 + 并发竞态）")
    p.add_argument("--scenario", action="append", default=None,
                   choices=sorted(SCENARIOS), help="可多次指定，缺省全部")
    p.add_argument("--out", default=str(root / "artifacts" / "chaos-report.json"))
    args = p.parse_args(argv)

    results = run(args.scenario)

    passed = sum(1 for r in results if r["passed"])
    print(f"\n[CHAOS] 场景 {passed}/{len(results)} PASS")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['scenario']}  ({r.get('elapsed_s', '?')}s)")
        for n in r.get("notes", []):
            print(f"         - {n}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps({"finished_at": datetime.now(timezone.utc).isoformat(),
                    "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  报告 -> {args.out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
