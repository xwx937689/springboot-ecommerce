"""Phase 15 CLI - 轻量负载测试。

对公开读端点做并发压测，产出 RPS 与延迟分位数（p50/p90/p95/p99）。
只测公开读端点（无鉴权、无写副作用），可安全地在 CI 每轮运行。

用法（在 api-test-platform 目录）:
  python -m perf.cli                          # 默认目标
  python -m perf.cli --requests 300 --workers 16
  python -m perf.cli --target products --target inventory

产物: artifacts/perf-report.json；stdout 输出 markdown 摘要（可直接入 Job Summary）
退出码: 恒为 0（性能是观测不是门禁；错误率超阈值时打 ::warning::）
"""
from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

# 公开读端点（无鉴权、无副作用），host 用本地直连端口（与执行器同口径）
TARGETS = {
    "products-list": "http://localhost:8082/api/products",
    "product-detail": "http://localhost:8082/api/products/1",
    "inventory-read": "http://localhost:8084/api/inventory/1",
    "recommendations": "http://localhost:8088/api/recommendations/users/1",
    "seller-public": "http://localhost:8090/api/sellers/1/public",
}

ERROR_RATE_WARN = 0.30  # 错误率超 30% 打 GitHub warning


def _percentile(sorted_ms: list[float], p: float) -> float:
    if not sorted_ms:
        return 0.0
    k = max(0, min(len(sorted_ms) - 1, math.ceil(p / 100 * len(sorted_ms)) - 1))
    return sorted_ms[k]


def run_target(client: httpx.Client, name: str, url: str,
               requests: int, workers: int) -> dict:
    latencies: list[float] = []
    errors = 0
    status_counter: dict[int, int] = {}

    def hit(_):
        t0 = time.perf_counter()
        try:
            r = client.get(url, timeout=10)
            ms = (time.perf_counter() - t0) * 1000
            return ms, r.status_code
        except httpx.HTTPError:
            return (time.perf_counter() - t0) * 1000, 0

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ms, status in pool.map(hit, range(requests)):
            latencies.append(ms)
            status_counter[status] = status_counter.get(status, 0) + 1
            if status == 0 or status >= 500:
                errors += 1
    wall = time.perf_counter() - t0

    latencies.sort()
    return {
        "target": name,
        "url": url,
        "requests": requests,
        "workers": workers,
        "wall_s": round(wall, 2),
        "rps": round(requests / wall, 1) if wall > 0 else 0,
        "p50_ms": round(_percentile(latencies, 50), 1),
        "p90_ms": round(_percentile(latencies, 90), 1),
        "p95_ms": round(_percentile(latencies, 95), 1),
        "p99_ms": round(_percentile(latencies, 99), 1),
        "max_ms": round(latencies[-1], 1) if latencies else 0,
        "errors": errors,
        "error_rate": round(errors / requests, 3) if requests else 0,
        "statuses": status_counter,
    }


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="Phase 15 轻量负载测试（公开读端点）")
    p.add_argument("--target", action="append", default=None,
                   choices=sorted(TARGETS), help="可多次指定，缺省全部")
    p.add_argument("--requests", type=int, default=200, help="每端点请求数")
    p.add_argument("--workers", type=int, default=10, help="并发数")
    p.add_argument("--out", default=str(root / "artifacts" / "perf-report.json"))
    args = p.parse_args(argv)

    names = args.target or list(TARGETS)
    results = []
    with httpx.Client() as client:
        for name in names:
            url = TARGETS[name]
            r = run_target(client, name, url, args.requests, args.workers)
            results.append(r)
            warn = " ⚠️" if r["error_rate"] > ERROR_RATE_WARN else ""
            print(f"[PERF] {name:18s} {r['rps']:>7} rps  "
                  f"p50={r['p50_ms']:>7}ms p95={r['p95_ms']:>7}ms "
                  f"err={r['errors']}/{r['requests']}{warn}")

    report = {"finished_at": datetime.now(timezone.utc).isoformat(),
              "requests_per_target": args.requests, "results": results}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    md = ["## 性能基准（Phase 15）", "",
          "| 端点 | RPS | p50 | p95 | p99 | 错误率 |", "|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['target']} | {r['rps']} | {r['p50_ms']}ms | "
                  f"{r['p95_ms']}ms | {r['p99_ms']}ms | {r['error_rate']:.1%} |")
    summary = "\n".join(md) + "\n"
    dest = __import__("os").environ.get("GITHUB_STEP_SUMMARY")
    if dest:
        with open(dest, "a", encoding="utf-8") as f:
            f.write(summary)
    print(summary, end="")
    print(f"  报告 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
