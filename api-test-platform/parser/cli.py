"""Phase 4 CLI - 解析 OpenAPI 契约目录，产出统一接口清单 + 统计报告。

用法:
    python -m parser.cli --contracts ../openapi-contracts --out artifacts/endpoints.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .models import ServiceContract
from .openapi_parser import parse_dir

# 文件名 -> 服务名（与 openapi-contracts 对齐）
SERVICE_MAPPING = {
    "user-service": "user-service",
    "product-service": "product-service",
    "cart-service": "cart-service",
    "inventory-service": "inventory-service",
    "payment-service": "payment-service",
    "order-service": "order-service",
    "notification-service": "notification-service",
    "recommendation-service": "recommendation-service",
    "catalog-stream-service": "catalog-stream-service",
    "seller-service": "seller-service",
}


def run(contracts_dir: Path, out: Path) -> int:
    if not contracts_dir.exists():
        print(f"[ERROR] 契约目录不存在: {contracts_dir}", file=sys.stderr)
        return 2

    contracts: list[ServiceContract] = parse_dir(contracts_dir, SERVICE_MAPPING)
    if not contracts:
        print(f"[WARN] 目录 {contracts_dir} 下没有 .json 契约文件", file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "services": [c.model_dump(mode="json") for c in contracts],
    }
    (out / "endpoints.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 控制台统计
    total = sum(len(c.endpoints) for c in contracts)
    auth_counter: Counter = Counter()
    for c in contracts:
        for ep in c.endpoints:
            auth_counter[ep.auth.value] += 1

    print(f"[OK] 解析 {len(contracts)} 个服务, 共 {total} 个接口")
    print("  per-service:")
    for c in contracts:
        print(f"    - {c.service:28s} {len(c.endpoints):3d} endpoints")
    print(f"  auth: {dict(auth_counter)}")
    print(f"  artifacts -> {out / 'endpoints.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 4 OpenAPI Parser")
    ap.add_argument("--contracts", default="../openapi-contracts", type=Path)
    ap.add_argument("--out", default="artifacts", type=Path)
    args = ap.parse_args()
    return run(args.contracts, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
