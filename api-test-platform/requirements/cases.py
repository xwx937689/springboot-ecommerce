"""TP -> Gherkin 功能用例投影层（P-Cases：需求分析的下游第一段）。

用法:
    python -m requirements.cases --product artifacts/requirements.json [--out PATH]

行为:
    读产物 -> 逐条 TestPoint 投影为 FunctionalCase（Given/When/Then 文本 + 完整追溯链）
    -> 原子写 req-cases.json（缺省 artifacts/req-cases.json）。

不编造纪律:
    preconditions / steps / expected_results 任一为空时 gherkin=None，
    missing 记录缺什么——结构化字段始终完整，缺的只是可执行文本。
    （正向 TP 的 steps=[] 是上游刻意设计：操作细节是用例生成 Agent 的职责。）

与 generator 的关系:
    本层产出"业务级功能用例"（人看/评审/BDD 导出）；generator 产出"接口级可执行
    用例"（executor 消费，带 req_trace 钩子消费本层上游的 TP）。两者互补不同层。
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .models import RequirementAnalysis, TestPoint


class FunctionalCase(BaseModel):
    """业务级功能用例：TP 的 Gherkin 投影，追溯链完整保留。"""

    id: str                     # TC-001 全局重排
    tp_id: str                  # 来源测试点（回链）
    ac_id: str
    rule_ids: list[str] = Field(default_factory=list)
    module_id: str
    title: str
    kind: str                   # positive/boundary/negative/exception
    priority: str
    gherkin: str | None = None  # 信息齐全才生成（不编造纪律），缺则 None
    missing: list[str] = Field(default_factory=list)  # 缺什么: ["preconditions","steps"]


class CaseSet(BaseModel):
    """用例集合（写盘产物，schema 自带版本）。"""

    schema_version: str = "1.0"
    product_sha256: str = ""    # 版本绑定：来源产物的 source_sha256（review 同款哲学）
    generated_at: str
    cases: list[FunctionalCase] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


def _build_gherkin(tp: TestPoint) -> tuple[str | None, list[str]]:
    """齐全则拼 Gherkin 文本，否则返回 (None, missing 字段列表)。"""
    missing: list[str] = []
    if not tp.preconditions.strip():
        missing.append("preconditions")
    if not tp.steps:
        missing.append("steps")
    if not tp.expected_results.strip():
        missing.append("expected_results")
    if missing:
        return None, missing

    lines = [f"@{tp.id} @{tp.priority} @{tp.kind}", f"Scenario: {tp.title}"]
    lines.append(f"    Given {tp.preconditions}")
    for i, step in enumerate(tp.steps):
        lines.append("    " + ("When " if i == 0 else "And ") + step)
    lines.append(f"    Then {tp.expected_results}")
    return "\n".join(lines), []


def _project(tp: TestPoint, seq: int) -> FunctionalCase:
    gherkin, missing = _build_gherkin(tp)
    return FunctionalCase(
        id=f"TC-{seq:03d}",
        tp_id=tp.id,
        ac_id=tp.ac_id,
        rule_ids=tp.rule_ids,
        module_id=tp.module_id,
        title=tp.title,
        kind=tp.kind,
        priority=tp.priority,
        gherkin=gherkin,
        missing=missing,
    )


def project(product_path: Path, out_path: Path | None) -> int:
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

    cases = [_project(tp, i) for i, tp in enumerate(analysis.test_points, 1)]

    by_kind: dict[str, int] = {}
    for c in cases:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    n_full = sum(1 for c in cases if c.gherkin)

    case_set = CaseSet(
        product_sha256=str(raw.get("source_sha256", "")),
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cases=cases,
        stats={
            "total": len(cases),
            "by_kind": by_kind,
            "gherkin_full": n_full,
            "gherkin_missing": len(cases) - n_full,
        },
    )

    out_path = out_path or Path(__file__).resolve().parent.parent / "artifacts" / "req-cases.json"
    tmp = Path(str(out_path) + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(case_set.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, out_path)   # 原子写，与 review.apply / tps 同款

    print(f"[OK] 用例投影: {len(cases)} 条（by_kind: {by_kind}）")
    print(f"     gherkin 齐全 {n_full} / 缺失 {len(cases) - n_full}（缺失字段见 missing，不编造）")
    print(f"  用例 -> {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="requirements.cases", description="TP -> Gherkin 功能用例投影")
    ap.add_argument("--product", required=True, help="需求分析产物 JSON")
    ap.add_argument("--out", default=None, help="输出路径（缺省 artifacts/req-cases.json）")
    args = ap.parse_args()
    return project(Path(args.product), Path(args.out) if args.out else None)


if __name__ == "__main__":
    raise SystemExit(main())
