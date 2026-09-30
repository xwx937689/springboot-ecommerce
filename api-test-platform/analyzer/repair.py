"""Phase 10 - AI 修复建议回灌（Expectation Repair）。

原则：
  - 只修 EXPECTATION_ISSUE / DATA_STATE（用例预期与系统实际契约/业务状态不符）
  - REAL_DEFECT / ENVIRONMENT 一律不动——前者要提单，后者重跑即消失
  - 原始期望保留在 expected_original，repaired 标记 + AI 理由入档，全程可审计
  - 修复后的期望 = 执行时实测状态码（系统实际契约行为的锚定）
"""
from __future__ import annotations

import json
from pathlib import Path

REPAIRABLE = {"EXPECTATION_ISSUE", "DATA_STATE"}


def repair(report_path: Path, cases_path: Path, out_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    verdicts = {v["case_id"]: v for v in report["verdicts"]}
    data = json.loads(cases_path.read_text(encoding="utf-8-sig"))

    repaired = dropped = 0
    for c in data["cases"]:
        v = verdicts.get(c["id"])
        if not v or v["classification"] not in REPAIRABLE:
            continue
        if v.get("confidence", 0) < 0.6:
            dropped += 1
            continue
        # 实测状态码从基线结果中锚定（v.case_id 对应的最近一次执行）
        actual = _actual_status.get(c["id"])
        if actual is None:
            dropped += 1
            continue
        c["expected_original"] = c["expected"]
        c["expected"] = {
            "status_codes": [actual],
            "status_class": None,
            "json_path_contains": [],   # 修复版不做字段级断言，字段断言属 Phase 7 读模型
        }
        c["repaired"] = True
        c["repair_reason"] = v.get("reason", "")
        repaired += 1

    data["stats"]["repaired_cases"] = repaired
    data["stats"]["repair_skipped"] = dropped
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"repaired": repaired, "skipped": dropped,
            "untouched_real_defects": sum(1 for v in verdicts.values()
                                          if v["classification"] == "REAL_DEFECT")}


# case_id -> 最近一次执行的实测状态码（由 cli 注入）
_actual_status: dict[str, int] = {}


def load_actual(results_path: Path) -> None:
    d = json.loads(results_path.read_text(encoding="utf-8-sig"))
    _actual_status.clear()
    for r in d["results"]:
        _actual_status[r["case_id"]] = r["status"]
