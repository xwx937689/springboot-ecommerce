"""变异验证工具：证明测试网"会红"——每条变异必须至少被一个测试抓住。

纪律背景：绿测试不等于有效测试。对核心逻辑逐条注入变异（删除防护层、
破坏合并追溯），跑目标测试集：变异被抓 = 测试有效；变异存活 = 测试盲区。

用法:
  python -m tools.mutation --file requirements/testpoints.py \
      --spec tools/mutations_dedupe.json --tests tests/test_dedupe.py

变异谱格式（JSON 数组）: [{"name": "...", "old": "原文片段", "new": "变异后"}]
安全机制：变异前备份原文件，每条变异跑完立即恢复（finally 兜底），
异常时也保证原文件还原。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _run_tests(tests: str) -> bool:
    """跑测试集，返回是否通过（True=绿）。"""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", tests, "-q", "--no-header"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.returncode == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mutation")
    ap.add_argument("--file", required=True, help="被变异的源文件")
    ap.add_argument("--spec", required=True, help="变异谱 JSON")
    ap.add_argument("--tests", required=True, help="应当抓住变异的测试文件")
    args = ap.parse_args(argv)

    src = Path(args.file)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    original = src.read_text(encoding="utf-8")

    if not _run_tests(args.tests):
        print("[ERROR] 基线测试就是红的——先修测试再谈变异")
        return 1
    print(f"[基线] {args.tests} 全绿，开始注入 {len(spec)} 条变异\n")

    caught, survived, equiv = [], [], []
    try:
        for m in spec:
            if original.count(m["old"]) != 1:
                print(f"[SKIP] {m['name']}: 变异锚点不唯一或不存在")
                survived.append(m["name"])
                continue
            src.write_text(original.replace(m["old"], m["new"]), encoding="utf-8")
            green = _run_tests(args.tests)
            if green and m.get("equivalent"):
                # 等价变异：变异后行为语义不变（通常被更强防护层吸收），
                # 存活是预期结论，不算盲区——但必须显式记录这个判断
                equiv.append(m["name"])
                print(f"[EQUIV-OK] {m['name']}（{m.get('note', '等价变异')}）")
                continue
            status = "SURVIVED(盲区!)" if green else "CAUGHT"
            (survived if green else caught).append(m["name"])
            print(f"[{status}] {m['name']}")
    finally:
        src.write_text(original, encoding="utf-8")     # 兜底还原，绝不留变异在盘上

    print(f"\n[结论] 抓住 {len(caught)}/{len(spec)}"
          + (f"，等价 {len(equiv)}" if equiv else "")
          + (f"，盲区: {survived}" if survived else "，测试网有效"))
    src_text = src.read_text(encoding="utf-8")
    if src_text != original:
        print("[FATAL] 原文件未正确还原！")
        return 1
    return 0 if not survived else 1


if __name__ == "__main__":
    raise SystemExit(main())
