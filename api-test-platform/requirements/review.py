from  pathlib import Path
import argparse, json, os

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
    print(f"产物 sha256: {product.get('source_sha256','')}")

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
    try:
       product = _load(args.product)
       decisions = _load(args.decisions)
    except FileNotFoundError as e:
        print(f"[ERROR] 文件不存在: {e.filename}")
        return 1
    except json.JSONDecodeError as e:
        print(f"[ERROR] 决策文件JSON解析失败: {e}")
        return 1
    by_id = {}
    for r in product["rules"]:
        by_id[r["id"]] = r
    for c in product["criteria"]:
        by_id[c["id"]] = c
    if not  isinstance(decisions,dict):
        print("[ERROR] 决策文件必须是JSON对象（{...}）")
        return 1
    decs = decisions.get("decisions", {})
    if not isinstance(decs,dict) or not decs:
        print('[ERROR] 决策文件缺少非空的decisions 对象，格式：{"decisions":{"BR-001":"approved"}}')
        return 1
    VALID = {"approved", "rejected"}
    # 版本绑定：决策文件带 product_sha256 时必须与产物一致，防旧决策重放到新版本产物上
    expect_sha = product.get("source_sha256", "")
    claimed_sha = decisions.get("product_sha256", "")
    if claimed_sha and claimed_sha != expect_sha:
        print(f"[ERROR] 决策文件对应的是另一版产物（决策 sha={claimed_sha[:8]}… / 产物 sha={expect_sha[:8]}…）")
        return 1
    if not claimed_sha:
        print("[WARN] 决策文件未绑定 product_sha256（建议从 list 输出复制，防旧决策重放）")
    # 第一遍：全校验，收集全部错误一次报清（fail-fast 只报第一个错，
    # 用户得改一次跑一次；收集式一轮修完）。任一错误仍不写文件（要么全对、要么不动）
    errors = []
    for id_, status in decs.items():
        if id_ not in by_id:
            errors.append(f"[ERROR] 决策文件里的 ID 在产物中不存在: {id_}")
            continue          # 条目不存在，后续检查无从谈起
        if status not in VALID:
            errors.append(f"[ERROR] 非法裁决值 {status!r}（只允许 approved/rejected）: {id_}")
        if by_id[id_]["review_status"] != "pending":
            errors.append(f"[ERROR] {id_} 已裁决（{by_id[id_]['review_status']}），不能覆盖")
    if errors:
        for e in errors:
            print(e)
        print(f"[ERROR] 共 {len(errors)} 处问题，未落盘（要么全对、要么不动）")
        return 1
    # 第二遍：全合法才落盘
    for id_, status in decs.items():
        by_id[id_]["review_status"] = status
    tmp = Path(str(args.product) + ".tmp")
    tmp.write_text(json.dumps(product, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, args.product)      # 原子替换，中途崩溃不留半个 JSON
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
