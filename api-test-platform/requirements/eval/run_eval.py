# golden  手写标准答案
#run_eval 打分脚本 读 golden +读产物 -- 输出四个分数
import json
from pathlib import Path
import argparse


def _parse_args():
    parser = argparse.ArgumentParser(description="需求分析 golden 标尺打分" )
    parser.add_argument("--golden", required=True, help="标准答案 JSON路径")
    parser.add_argument("--product", required=True, help="分析产物 JSON路径")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    golden = json.loads(Path(args.golden).read_text(encoding = "utf-8"))
    product = json.loads(Path(args.product).read_text(encoding = "utf-8"))
    mods = _flatten_modules(product["modules"])
    print("产物模块数(拍平后):", len(mods))
    score, missed = score_modules(golden, product)
    print("模块覆盖:", score, "未命中:", missed)
    score_r, missed_r = score_rules(golden, product)
    print("规则命中:", score_r, "未命中:", missed_r)
    si,mi = score_issue(golden, product)
    print("问题发现:", si, "未命中:", mi)
    stp,mtp = score_tp_quality(golden, product)
    print("测试点质量通过:", stp, "失败项:", mtp)
    ok = score_r == 1.0 and missed_r == 1.0 and si == 1.0 and stp
    print("结论:", "PASS" if ok else "FAIL")
    return 0 if ok else 1
def _finding_text(f:dict) -> str:
    """findings 没有text字段，可匹配的只有 evidence + reason。"""
    return (f.get("evidence", "") +" "+ f.get("reason", "")).strip().lower()
def _any_hit(needle:str,haystack:list[str]) -> bool:
    """空白不敏感匹配：删全部空白再比子串，容忍中文排版空格。"""
    n = "".join(needle.split()).lower()
    return any(n in "".join(h.split()).lower() for h in haystack)

def score_modules(g:dict,p:dict) -> tuple[float,list[str]]:
    """golden 的 modules 每个都按子串在产物 module.title 里查；返回（分数，未命中名）"""
    titles = [m["title"] for m in _flatten_modules(p["modules"])]
    missed = [w for w in g["modules"] if not _any_hit(w, titles)]
    total = len(g["modules"])
    if total == 0:
        return 1.0, []
    return (total-len(missed)) / total, missed
def score_issue(g:dict,p:dict) -> tuple[float,list[str]]:
    texts = [_finding_text(f) for f in p["issues"]]
    terms = g.get("unambiguous_terms",[])
    miss_t = [t for t in terms if not _any_hit(t, texts)]
    miss_c = []
    for c in confs:
        if not all(_any_hit(k,texts) for k in c.get("keywords",[])):
            miss_c.append(c["desc"])
    total = len(terms) + len(confs)
    if total == 0:
        return 1.0, []
    missed = miss_t + miss_c
    return (total - len(missed)) / total, missed
def _flatten_modules(modules:list[dict]) -> list[dict]:
    """modules在产物里是树（children 嵌套），拍平成列表再匹配。"""
    out:list[dict] = []
    for m in modules:
        out.append(m)
        out.extend(_flatten_modules(m.get("children",[])))
    return out
def score_rules(g: dict, p: dict) -> tuple[float, list[str]]:
    """golden 的必选规则在产物 rules[].text 中子串命中；返回 (分数, 未命中锚点列表)。"""

    # 1) 先筛：只考 must_have 标记为 true 的规则
    #    .get("must_have") 而不是 ["must_have"]：没写这个键的规则默认"选考"，不报 KeyError
    must = [r for r in g["rules"] if r.get("must_have")]
    # 2) 防空：golden 里没有必选规则 → 没有可失败的项 → 默认通过
    if not must:
        return 1.0, []
    # 3) 备好 haystack：产物侧所有规则的 text
    #    只取 text，不要把 evidence 拼进来——这里考的是"规则本身被提取到了没有"
    texts = [x["text"] for x in p["rules"]]
    # 4) 逐条判定：没命中的，把它的 text 记进 missed（明细要给 CI 和人看）
    missed = [r["text"] for r in must if not _any_hit(r["text"], texts)]
    # 5) 得分 = 命中数 / 必选总数   ← 上次焊反的那个坑，这次自己防
    return (len(must) - len(missed)) / len(must), missed
def score_tp_quality(g:dict,p:dict) -> tuple[float,list[str]]:
   req = g.get("tp_requirements",{})
   tps = p.get("test_points",[])
   fails:list[str] = []
   if req.get("max_per_ac"):
       per_ac = {}
       for tp in tps:
           a = tp.get("ac_id")
           if a:
               per_ac[a] = per_ac.get(a, 0) + 1
       over = [a for a, n in per_ac.items() if n > req["max_per_ac"]]
       if over:
           fails.append(f"max_per_ac>{req['max_per_ac']}:{over}")
   if req.get("no_duplicate_titles"):
       if len({tp.get("title") for tp in tps}) != len(tps):
           fails.append("duplicate_titles")
   if req.get("must_have_expected"):
       if not all(tp.get("expected_results", "").strip() for tp in tps):  # 注意字段名
           fails.append("must_have_expected")
   return (len(fails) == 0), fails


if __name__ == "__main__":
    main()
