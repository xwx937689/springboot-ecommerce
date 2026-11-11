# golden  手写标准答案
#run_eval 打分脚本 读 golden +读产物 -- 输出四个分数
# ① 模块覆盖：golden.modules 每个都要在产物 module.title 里出现
covered = [w for w in g["modules"] if any(w in m["title"] for m in p["modules"])]
mod_score = len(covered) / len(g["modules"])

# ② 规则命中：rules[].must_have 的 text 要被子串命中
must = [r for r in g["rules"] if r.get("must_have")]
hit = [r for r in must if any(r["text"] in x["text"] for x in p["rules"])]
rule_score = len(hit) / len(must)

# ③ 问题发现：unambiguous_terms 每个要出现在某条 finding.text；
#    must_find_conflicts 的 keywords 要全部出现在 findings 里
terms_ok = [t for t in g["unambiguous_terms"]
            if any(t in f["text"] for f in p["findings"])]
conf_ok = []
for c in g["must_find_conflicts"]:
    kws = c["keywords"]
    conf_ok.append(all(any(k in f["text"] for f in p["findings"]) for k in kws))
issue_score = (len(terms_ok) + sum(conf_ok)) / (len(g["unambiguous_terms"]) + len(g["must_find_conflicts"]))

# ④ 测试点质量：tp_requirements 四条
tps = p["test_points"]
trace_ok   = all(tp.get("ac_id") or tp.get("rule_ids") for tp in tps)   # 必须可溯源
per_ac     = {tp["ac_id"] for tp in tps if tp.get("ac_id")}
max_ok     = all(sum(1 for tp in tps if tp.get("ac_id")==a) <= g["tp_requirements"]["max_per_ac"] for a in per_ac)
dup_ok     = len({tp["title"] for tp in tps}) == len(tps)
exp_ok     = all(tp.get("expected_results","").strip() for tp in tps)   # 注意字段名！
tp_pass    = all([trace_ok, max_ok, dup_ok, exp_ok])
