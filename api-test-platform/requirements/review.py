import argparse, json
from  pathlib import Path

def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))
def _flatten(modules):
    out =[]
    for m in modules:
        out.append(m)
        out.extend(_flatten(m.get("children",[])))
    return out
def _cmd_list(args):
    product = _load(args.product)
    flat = _flatten(product.get("modules",[]))
    titles = {m["id"]:m.get("title","") for m in flat}
    rules = [r for r in product.get("rules",[]) if r.get("review_status","pending") == "pending"]
    acs = [c for c in product.get("criteria",[]) if c.get("review_status","pending") == "pending"]
    print(f"=== 待裁决项（pending）: 规则 {len(rules)} / AC {len(acs)} ===\n")

    groups = {}
    for r in rules: groups.setdefault(r["module_id"],{"r":[],"a":[]})["r"].append(r)
    for c in acs:   groups.setdefault(c["module_id"],{"r":[],"a":[]})["a"].append(c)
    for mid in [m["id"] for m in flat]:
        if mid not in groups: continue
        g = groups[mid]
        print(f"[{mid}] {titles.get(mid,'')}")
        for r in sorted(g["r"],key = lambda x : x["id"]):
            print(f" {r['id']} | evidence:{r.get('evidence','')}")
            print(f"     text:{r.get('text','')}")
        for c in sorted(g["a"],key=lambda x: x["id"]):
            print(f" {c['id']} | evidence:{c.get('evidence','')}")
            print(f"    text:{c.get('text','')}")
        print()
def _cmd_apply(args):
    product = _load(args.product)
    decisions = _load(args.decisions)
    by_id = {}
    for r in product["rules"]:
        by_id[r["id"]] = r
    for c in product["criteria"]:
        by_id[c["id"]] = c

    decs = decisions.get("decisions", {})
    VALID = {"approved", "rejected"}
    # 第一遍：全校验，任一不合法立即报错且不写文件（要么全对、要么不动）
    for id_, status in decs.items():
        if id_ not in by_id:
            print(f"[ERROR] 决策文件里的 ID 在产物中不存在: {id_}")
            return 1
        if status not in VALID:
            print(f"[ERROR] 非法裁决值 {status!r}（只允许 approved/rejected）: {id_}")
            return 1
        if by_id[id_]["review_status"] != "pending":
            print(f"[ERROR] {id_} 已裁决（{by_id[id_]['review_status']}），不能覆盖")
            return 1
    # 第二遍：全合法才落盘
    for id_, status in decs.items():
        by_id[id_]["review_status"] = status
    Path(args.product).write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding="utf-8")
    approved = sum(1 for s in decs.values() if s == "approved")
    rejected = sum(1 for s in decs.values() if s == "rejected")
    remaining = sum(1 for it in by_id.values() if it["review_status"] == "pending")
    print(f"approved: {approved} / rejected: {rejected} / 剩余 pending: {remaining}")
    return 0
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest = "cmd",required = True)
    p_list = sub.add_parser("list")
    p_list.add_argument("--product",required=True)
    p_list.set_defaults(func=_cmd_list)
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--product",required=True)
    p_apply.add_argument("--decisions",required=True)
    p_apply.set_defaults(func=_cmd_apply)
    a = ap.parse_args()
    raise SystemExit(a.func(a))

if __name__ == "__main__":
    main()
