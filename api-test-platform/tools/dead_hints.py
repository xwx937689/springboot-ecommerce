"""死提示分析：需求 hint 注入后，哪些从未被任何用例追溯命中。

QueryLog 思想（借鉴 WHartTest）的离线评测件——检索质量的标尺：
  python -m tools.dead_hints --product artifacts/testcases-xxx.json --req artifacts/requirements.json

死提示多 = 注入策略低效（token 白花）或缺待检索优化（按服务分组/语义检索）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dead_hints")
    ap.add_argument("--product", required=True, help="用例产物 JSON（generator 产出）")
    ap.add_argument("--req", default="artifacts/requirements.json", help="需求产物 JSON")
    args = ap.parse_args(argv)

    cases = json.loads(Path(args.product).read_text(encoding="utf-8")).get("cases", [])
    traced = {c["req_trace"]["tp_id"] for c in cases if c.get("req_trace")}

    req = json.loads(Path(args.req).read_text(encoding="utf-8-sig"))
    tps = req.get("test_points", [])
    titles = {t["id"]: t.get("title", "") for t in tps if t.get("id")}
    # 与 generator._load_req_points 同口径：P0 全量 + P1 补充，上限 24，
    # 只统计 api 域（domain 字段优先，旧产物回退关键词）——评测基准必须等于实际注入集合
    from generator.generator import _fallback_domain
    p0 = [t["id"] for t in tps if t.get("priority") == "P0"
          and (t.get("domain") or _fallback_domain(str(t.get("title", "")))) == "api"]
    p1 = [t["id"] for t in tps if t.get("priority") == "P1"
          and (t.get("domain") or _fallback_domain(str(t.get("title", "")))) == "api"]
    hints = (p0 + p1)[:24]

    alive = [h for h in hints if h in traced]
    dead = [h for h in hints if h not in traced]
    print(f"注入 {len(hints)} 条 hint | 命中 {len(alive)} | 死提示 {len(dead)}"
          f" | 命中率 {round(len(alive) / max(len(hints), 1) * 100, 1)}%")
    for h in dead:
        print(f"  [死] {h}  {titles.get(h, '')[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
