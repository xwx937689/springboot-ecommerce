"""Phase 13 - 回归摘要写入 GitHub Job Summary。

动机（v0.1.0 门禁复盘）：诊断数据（通过率、归因分类、数据链笔记）只存在于
需要登录才能查看的 CI 日志里，外部无法排障。Job Summary 在 run 页面公开渲染，
把关键观测"浮"出来——可观测性实践的一部分。

用法（在 api-test-platform 目录）:
  python -m analyzer.summary

读取 artifacts/results.json 与 artifacts/failure-report.json（均可缺失，容错降级），
优先追加写入 $GITHUB_STEP_SUMMARY，否则打印 stdout。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _pct(passed: int, total: int) -> str:
    return f"{round(passed / total * 100, 1)}%" if total else "-"


def build_digest(artifacts_dir: Path) -> str:
    lines: list[str] = ["## 回归摘要", ""]

    results = _load(artifacts_dir / "results.json")
    if results is None:
        lines.append("> ⚠️ results.json 缺失或不可解析——执行器可能未运行")
    else:
        s = results["summary"]
        lines.append(f"- **回归通过率: {s.get('pass_rate', '-')}%**"
                     f"（{s.get('passed', 0)}/{s.get('total', 0)}）")
        if s.get("login_error"):
            lines.append(f"- ⚠️ 登录失败（JWT 用例无凭证执行）: `{s['login_error']}`")
        registry = s.get("registry") or {}
        if registry:
            lines.append(f"- 数据链 registry: `{registry}`")
        notes = s.get("chain_notes") or []
        if notes:
            lines += ["", "**数据链供给笔记:**"]
            lines += [f"- {n}" for n in notes]
        lines += ["", "### 按服务", "", "| 服务 | 通过/总数 | 通过率 |", "|---|---|---|"]
        by_service = (s.get("by") or {}).get("service") or {}
        for svc, v in sorted(by_service.items()):
            lines.append(f"| {svc} | {v['passed']}/{v['total']} | {_pct(v['passed'], v['total'])} |")

        failed = [r for r in results.get("results", []) if not r.get("ok")]
        if failed:
            by_status = Counter(str(r.get("status")) for r in failed)
            lines += ["", "### 失败状态码分布（Top 10）", "", "| 状态码 | 数量 |", "|---|---|"]
            for status, n in by_status.most_common(10):
                hint = "（连接异常）" if status == "0" else ""
                lines.append(f"| {status}{hint} | {n} |")

    report = _load(artifacts_dir / "failure-report.json")
    if report is not None:
        by_class = (report.get("summary") or {}).get("by_class") or {}
        if by_class:
            lines += ["", "### 失败归因分类", "", "| 分类 | 数量 |", "|---|---|"]
            for cls, n in sorted(by_class.items(), key=lambda kv: -kv[1]):
                lines.append(f"| {cls} | {n} |")
        real = [v.get("case_id") for v in report.get("verdicts", [])
                if v.get("classification") == "REAL_DEFECT"]
        real = [c for c in real if c]
        if real:
            lines += ["", f"### REAL_DEFECT 待提单（{len(real)} 条）", ""]
            lines += [f"- `{cid}`" for cid in real]

    # Phase 14 - 韧性场景结果（chaos-report.json 存在才渲染）
    chaos = _load(artifacts_dir / "chaos-report.json")
    if chaos is not None:
        cres = chaos.get("results") or []
        if cres:
            lines += ["", "### 韧性场景（Phase 14）", "",
                      "| 场景 | 结果 | 耗时 |", "|---|---|---|"]
            for r in cres:
                mark = "✅ PASS" if r.get("passed") else "❌ FAIL"
                lines.append(f"| {r.get('scenario')} | {mark} | {r.get('elapsed_s', '-')}s |")
            for r in cres:
                if not r.get("passed"):
                    for n in (r.get("notes") or [])[-3:]:
                        lines.append(f"- `{r.get('scenario')}`: {n}")

    # Phase 15 - 性能基准（perf-report.json 存在才渲染）
    perf = _load(artifacts_dir / "perf-report.json")
    if perf is not None:
        pres = perf.get("results") or []
        if pres:
            lines += ["", "### 性能基准（Phase 15）", "",
                      "| 端点 | RPS | p50 | p95 | p99 | 错误率 |",
                      "|---|---|---|---|---|---|"]
            for r in pres:
                lines.append(
                    f"| {r.get('target')} | {r.get('rps')} | {r.get('p50_ms')}ms"
                    f" | {r.get('p95_ms')}ms | {r.get('p99_ms')}ms"
                    f" | {r.get('error_rate', 0):.1%} |"
                )

    return "\n".join(lines) + "\n"


def main() -> int:
    # Windows 控制台默认 GBK，✅/❌ 等字符会导致 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    digest = build_digest(ROOT / "artifacts")
    dest = os.environ.get("GITHUB_STEP_SUMMARY")
    if dest:
        with open(dest, "a", encoding="utf-8") as f:
            f.write(digest)
        print("[OK] 回归摘要已写入 Job Summary")
    print(digest, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
