"""TP 独立生成阶段（P1+ 流程重排）。

用法:
    python -m requirements.tps --product artifacts/requirements.json [--out PATH] [--provider mock]

行为:
    读产物 -> 过滤 review_status==approved 的 BR/AC -> testpoints.generate 重建测试点
    -> 重算 tp 相关 stats/metrics -> 原子写回（--out 缺省覆盖原文件）。
    pending/rejected 条目不进 TP：人工复核从此生效到下游。

指标口径说明（有意的变化）:
    重算基于 approved 子集——ac_testable_rate/br_coverage 的分母从"全部"变为
    "已复核通过"，这正是复核生效的度量。brs_total = approved 规则数。

完整工作流:
    cli（分析，可 --skip-tps） -> review list/apply（裁决） -> tps（重建 TP） -> 下游用例生成
"""
import argparse
import json
import os
from pathlib import Path

from pydantic import ValidationError

from .cli import _metrics
from generator.config import LLMSettings
from .models import RequirementAnalysis
from .testpoints import generate


def regenerate(product_path: Path, out_path: Path | None = None,
               provider: str | None = None) -> int:
    try:
        raw = json.loads(product_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[ERROR] 产物文件不存在: {product_path}")
        return 1
    except json.JSONDecodeError as e:
        print(f"[ERROR] 产物 JSON 解析失败: {e}")
        return 1
    try:
        analysis = RequirementAnalysis.model_validate(raw)
    except ValidationError as e:
        print(f"[ERROR] 产物结构不合法（schema v{raw.get('schema_version', '?')}）: {e}")
        return 1

    rules_ok = [r for r in analysis.rules if r.review_status == "approved"]
    crit_ok = [c for c in analysis.criteria if c.review_status == "approved"]
    n_rule_skip = len(analysis.rules) - len(rules_ok)
    n_crit_skip = len(analysis.criteria) - len(crit_ok)

    settings = LLMSettings.from_env()
    if provider:
        settings.provider = provider          # dataclass，直接赋值（与 cli.main 同款）

    tps, tp_stats = generate(analysis.modules, rules_ok, crit_ok, settings)

    # 复用 cli 的指标计算（分母=approved 子集，口径变化见模块 docstring）
    metrics = _metrics(crit_ok, analysis.findings, tps,
                       analysis.stats.stages.get("analyze", {}), tp_stats)

    analysis.stats.test_points = len(tps)
    analysis.stats.metrics = type(analysis.stats.metrics)(**metrics)
    analysis.stats.stages["testpoints"] = tp_stats
    raw["test_points"] = [tp.model_dump(mode="json") for tp in tps]
    raw["stats"] = analysis.stats.model_dump(mode="json")

    out_path = out_path or product_path
    tmp = Path(str(out_path) + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out_path)          # 原子替换，与 review.apply 同款：中途崩溃不留半个 JSON

    print(f"[OK] TP 重生成: 消费 规则 {len(rules_ok)} / AC {len(crit_ok)}"
          f"（跳过未通过: 规则 {n_rule_skip} / AC {n_crit_skip}）")
    print(f"     测试点 {len(tps)} 条（by_kind: {tp_stats.get('by_kind')}）")
    print(f"     指标: BR 覆盖率 {metrics['br_coverage'] * 100:.1f}%"
          f" | 每 AC 测试点 {metrics['tp_per_ac']}")
    print(f"  产物 -> {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(
        prog="requirements.tps",
        description="TP 独立生成阶段：只消费 review_status==approved 的 BR/AC",
    )
    ap.add_argument("--product", required=True, help="分析产物 JSON 路径")
    ap.add_argument("--out", default=None, help="输出路径，缺省覆盖原产物")
    ap.add_argument("--provider", default=None, help="覆盖 LLM provider（如 mock）")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else None
    return regenerate(Path(args.product), out, args.provider)


if __name__ == "__main__":
    raise SystemExit(main())
