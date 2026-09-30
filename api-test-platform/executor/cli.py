"""Phase 6 CLI - 用例执行入口。

用法（在 api-test-platform 目录）:
  python -m executor.cli                                # 全量执行
  python -m executor.cli --service user-service         # 只跑某服务
  python -m executor.cli --category auth                # 只跑某分类
  python -m executor.cli --workers 16                   # 并发数
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .runner import run_suite


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="API 用例执行器（Phase 6）")
    p.add_argument("--cases", default=str(root / "artifacts" / "testcases.json"))
    p.add_argument("--out", default=str(root / "artifacts"))
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--service", default=None)
    p.add_argument("--category", default=None)
    args = p.parse_args(argv)

    scope = args.service or "全部服务"
    print(f"[EXEC] 目标: {scope}  用例文件: {args.cases}")
    summary = run_suite(
        cases_path=Path(args.cases),
        out_dir=Path(args.out),
        workers=args.workers,
        only_service=args.service,
        only_category=args.category,
    )
    if summary.get("login_error"):
        print(f"[WARN] 登录失败（JWT 用例将以无 token 执行）: {summary['login_error']}")
    elif summary.get("provisioned_user"):
        print(f"[INFO] 测试库未 seed，已自动注册专属账号: {summary.get('provisioned_user')}")
    print("[OK] 执行完成")
    print(f"  总数: {summary['total']}  通过: {summary['passed']}  失败: {summary['failed']}  通过率: {summary['pass_rate']}%")
    print("  按服务:")
    for svc, v in sorted(summary["by"]["service"].items()):
        rate = round(v["passed"] / v["total"] * 100, 1) if v["total"] else 0
        print(f"    - {svc:<24} {v['passed']}/{v['total']}  {rate}%")
    print(f"  报告 -> {Path(args.out) / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
