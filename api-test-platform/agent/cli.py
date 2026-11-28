"""用例生成 Agent 的批量入口。

用法:
    python -m agent.cli --cases artifacts/req-cases.json \
        --product artifacts/requirements.json --endpoints artifacts/endpoints.json \
        [--limit 5] [--out artifacts/agent-cases.json]

行为:
    挑出 req-cases.json 中 gherkin 缺失的用例 -> 逐条跑 Agent 循环补全
    preconditions/steps/expected_results -> 原子写审计产物 agent-cases.json
    （trace + token 用量全留痕）。

provider=mock 时显式跳过（Agent 循环依赖真实 LLM），不伪造结果。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from generator.config import LLMSettings
from generator.llm import make_client

from .runner import run_case_agent
from .tools import ToolBox


def _gherkin(tp: dict, payload: dict) -> str:
    """与 requirements.cases 同款 Gherkin 文本组装（风格统一，便于 golden 打分）。"""
    lines = [f"@{tp.get('id', '?')} @{tp.get('priority', '?')} @{tp.get('kind', '?')}",
             f"Scenario: {tp.get('title', '')}"]
    lines.append(f"    Given {payload['preconditions']}")
    for i, step in enumerate(payload["steps"]):
        lines.append("    " + ("When " if i == 0 else "And ") + step)
    lines.append(f"    Then {payload['expected_results']}")
    return "\n".join(lines)


def _load(path: Path, label: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[ERROR] {label} 不存在: {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[ERROR] {label} JSON 解析失败: {e}")
        sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(prog="agent.cli", description="用例生成 Agent（harness 闭环）")
    ap.add_argument("--cases", default=str(root / "artifacts" / "req-cases.json"))
    ap.add_argument("--product", default=str(root / "artifacts" / "requirements.json"))
    ap.add_argument("--endpoints", default=str(root / "artifacts" / "endpoints.json"))
    ap.add_argument("--out", default=str(root / "artifacts" / "agent-cases.json"))
    ap.add_argument("--limit", type=int, default=5, help="本轮最多处理多少条缺失用例")
    args = ap.parse_args(argv)

    cases_doc = _load(Path(args.cases), "req-cases")
    product = _load(Path(args.product), "requirements")
    endpoints = _load(Path(args.endpoints), "endpoints")

    settings = LLMSettings.from_env()
    if settings.provider == "mock":
        print("[SKIP] LLM_PROVIDER=mock：Agent 循环依赖真实 LLM，不伪造结果。"
              "配置 LLM_API_KEY（或 DEEPSEEK_API_KEY）后重跑。")
        return 0
    client = make_client(settings)
    if not hasattr(client, "chat"):
        print(f"[ERROR] provider={settings.provider} 不支持工具循环（仅 openai 兼容端点）")
        return 1

    pending = [c for c in cases_doc.get("cases", []) if not c.get("gherkin")]
    tp_by_id = {tp["id"]: tp for tp in product.get("test_points", [])}
    ac_by_id = {ac["id"]: ac for ac in product.get("criteria", [])}
    rule_by_id = {r["id"]: r for r in product.get("rules", [])}
    examples = [{"title": c.get("title", ""), "gherkin": c["gherkin"]}
                for c in cases_doc.get("cases", []) if c.get("gherkin")]
    toolbox = ToolBox(product, endpoints, examples)

    batch = pending[: max(args.limit, 0)]
    if not batch:
        print("[OK] 没有待补全的用例")
        return 0
    print(f"[RUN] 待补全 {len(pending)} 条，本轮处理前 {len(batch)} 条（limit={args.limit}）")

    records: list[dict] = []
    n_filled = 0
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    for case in batch:
        tp = tp_by_id.get(case.get("tp_id"), {})
        ac = ac_by_id.get(case.get("ac_id"), {})
        rule_texts = [rule_by_id[r]["text"] for r in case.get("rule_ids", []) if r in rule_by_id]
        result = run_case_agent(tp, ac.get("text", ""), rule_texts, toolbox, client)
        for k in usage_total:
            usage_total[k] += result["usage"][k]
        record = {"case_id": case["id"], "tp_id": case.get("tp_id", ""), "title": case.get("title", ""),
                  **result}
        if result["status"] == "filled":
            n_filled += 1
            record["gherkin"] = _gherkin(tp, result)
            print(f"  [OK] {case['id']} turns={result['turns']} steps={len(result['steps'])}")
        else:
            print(f"  [FAIL] {case['id']} {result.get('reason', '')}")
        records.append(record)

    out_doc = {
        "schema_version": "1.0",
        "source_cases_sha": cases_doc.get("product_sha256", ""),
        "agent_model": settings.model or settings.provider,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {"attempted": len(records), "filled": n_filled,
                  "failed": len(records) - n_filled},
        "usage_total": usage_total,
        "records": records,
    }
    out_path = Path(args.out)
    tmp = Path(str(out_path) + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out_path)

    print(f"[DONE] 补全 {n_filled}/{len(records)} | tokens: {usage_total['prompt_tokens']}+"
          f"{usage_total['completion_tokens']}")
    print(f"  审计产物 -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
