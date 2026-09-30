"""Phase 5 CLI - 用例生成入口。

用法（在 api-test-platform 目录）:
  python -m generator.cli                          # 规则基线（mock，免 Key）
  python -m generator.cli --provider openai        # LLM 增强（需 .env 配 Key）
  python -m generator.cli --service user-service   # 只生成某服务
  python -m generator.cli --limit 5                # 只取前 5 个接口（试跑/省 Token）
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import LLMSettings
from .generator import generate

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="AI 测试用例生成（Phase 5）")
    p.add_argument("--endpoints", default=str(root / "artifacts" / "endpoints.json"))
    p.add_argument("--out", default=str(root / "artifacts" / "testcases.json"))
    p.add_argument("--provider", default=None, help="mock | openai | anthropic（默认取 .env）")
    p.add_argument("--service", default=None, help="只生成指定服务")
    p.add_argument("--limit", type=int, default=None, help="只取前 N 个接口")
    args = p.parse_args(argv)

    settings = LLMSettings.from_env()
    if args.provider:
        settings.provider = args.provider

    case_set = generate(
        endpoints_path=Path(args.endpoints),
        out_path=Path(args.out),
        settings=settings,
        only_service=args.service,
        limit=args.limit,
    )
    s = case_set.stats
    print("[OK] 用例生成完成")
    print(f"  接口数: {s['endpoints']}  provider: {s['provider']}  model: {s['model']}")
    print(f"  规则用例: {s['rule_cases']}  AI用例: {s['ai_cases']}  (LLM 成功/失败接口: {s['ai_endpoints_ok']}/{s['ai_endpoints_failed']})")
    print(f"  分类分布: {s['by_category']}")
    print(f"  产物 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
