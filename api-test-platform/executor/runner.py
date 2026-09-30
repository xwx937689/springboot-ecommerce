"""Phase 6 - 用例执行引擎。

流程：加载 testcases.json -> 渲染路径参数 -> 按服务注入鉴权 -> httpx 执行
      -> 按 expected 评估（status_codes 精确集 或 status_class 类别）
      -> 产出 results.json + report.html（企业风味的自包含 HTML 报告）。

评估口径：
  - expected.status_codes 非空 -> 实际码必须命中集合
  - 否则 status_class: 2xx/4xx/5xx 按百位匹配
  - 响应为 JSON 且 json_path_contains 非空 -> 逐个校验顶层路径存在（如 data.accessToken）
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .auth import AuthProvider, SERVICE_PORTS
from .provision import Provisioning

# Phase 7 - 可替换的资源 ID 字段名（小写） -> registry 键
_ID_FIELD_MAP = {
    "orderid": "orderId", "paymentid": "paymentId", "listingid": "listingId",
    "productid": "productId", "sellerid": "sellerId", "cartid": "cartId",
    "userid": "userId",
}
# 泛型 {id} 路径参数按服务推断资源类型
# 注意 cart 的 /api/cart/items/{productId} 语义是"商品ID"而非购物车ID
_SERVICE_DEFAULT_ID = {
    "payment-service": "paymentId", "order-service": "orderId",
    "seller-service": "listingId", "product-service": "productId",
    "cart-service": "productId",
}
# 只有这些分类做 ID 替换（not_found/auth/negative 必须保持原值才有意义）
_SUBSTITUTABLE_CATEGORIES = {"positive", "conflict", "boundary"}


class CaseResult(dict):
    """轻量结果对象（dict 直出 JSON，免去再建模型）。"""


def _render_path(template: str, params: dict) -> str:
    path = template
    for k, v in (params or {}).items():
        path = path.replace("{" + k + "}", str(v))
    return path


def _substitute_ids(obj, registry: dict, service: str):
    """递归替换资源 ID 字段为数据链产出的真实值（Phase 7 核心）。"""
    if not registry:
        return obj

    def lookup(key: str):
        kl = key.lower()
        if kl in _ID_FIELD_MAP:
            return registry.get(_ID_FIELD_MAP[kl])
        if kl == "id":
            default = _SERVICE_DEFAULT_ID.get(service)
            return registry.get(default) if default else None
        return None

    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            rep = lookup(k)
            if rep is not None and isinstance(v, (int, str)) and str(v).lstrip("-").isdigit():
                out[k] = rep
            else:
                out[k] = _substitute_ids(v, registry, service)
        return out
    if isinstance(obj, list):
        return [_substitute_ids(v, registry, service) for v in obj]
    return obj


def _normalize_auth(cases: list[dict], auth_map: dict[str, str]) -> list[dict]:
    """归一化鉴权标志（修复 AI 乱写 auth=none）：
    jwt 端点 -> 除 auth 类用例外一律 jwt（auth 类用例显式无凭证测 401）；
    公开端点 -> 一律 none，且 auth 类用例无意义直接剔除。
    """
    out = []
    for c in cases:
        ep_auth = auth_map.get(c.get("endpoint_key", ""))
        if ep_auth is None:
            out.append(c)
            continue
        if ep_auth == "none":
            if c.get("category") == "auth":
                continue
            c["request"]["auth"] = "none"
        else:
            c["request"]["auth"] = "none" if c.get("category") == "auth" else "jwt"
        out.append(c)
    return out


def _match(status: int, expected: dict) -> bool:
    codes = expected.get("status_codes") or []
    if codes:
        return status in codes
    klass = (expected.get("status_class") or "").strip()
    if not klass:
        return True  # 未声明期望 -> 仅记录不判失败
    want = int(klass[0]) * 100
    return status // 100 * 100 == want


def _check_json_paths(payload, paths: list[str]) -> list[str]:
    """校验响应 JSON 顶层点路径存在（如 data.accessToken）。返回缺失列表。"""
    missing: list[str] = []
    if not paths or not isinstance(payload, dict):
        return missing
    for p in paths:
        cur = payload
        ok = True
        for part in p.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if not ok:
            missing.append(p)
    return missing


def run_case(client: httpx.Client, auth: AuthProvider, case: dict, registry: dict | None = None) -> CaseResult:
    req = case["request"]
    service = case["service"]
    port = SERVICE_PORTS.get(service)
    if port is None:
        return CaseResult(
            case_id=case["id"], service=service, category=case.get("category", ""),
            name=case.get("name", ""), source=case.get("source", "rule"),
            method=req.get("method", ""), url=f"unknown://{service}",
            status=0, ok=False, error=f"未知服务端口: {service}", elapsed_ms=0,
        )

    # Phase 7: positive/conflict/boundary 用例注入数据链真实 ID
    if registry and case.get("category") in _SUBSTITUTABLE_CATEGORIES:
        if req.get("path_params"):
            req["path_params"] = _substitute_ids(req["path_params"], registry, service)
        if req.get("body"):
            req["body"] = _substitute_ids(req["body"], registry, service)

    url = f"http://localhost:{port}{_render_path(req['path_template'], req.get('path_params'))}"
    headers = {
        **case["request"].get("headers", {}),
        **auth.headers_for(service, req.get("auth", "none"), req.get("path_template", "")),
    }

    started = time.perf_counter()
    status, payload, text_snippet, err = 0, None, "", None
    try:
        resp = client.request(
            method=req["method"],
            url=url,
            params={k: str(v) for k, v in (req.get("query_params") or {}).items()},
            headers=headers,
            json=req.get("body") if isinstance(req.get("body"), (dict, list)) else None,
            timeout=15,
        )
        status = resp.status_code
        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError):
            text_snippet = resp.text[:200]
    except httpx.HTTPError as exc:
        err = f"HTTP error: {type(exc).__name__}"
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    expected = case.get("expected", {})
    ok = err is None and _match(status, expected)
    missing: list[str] = []
    # 字段断言为软校验：状态码命中即 PASS，字段未命中仅记警告（AI 猜错路径不应淹没状态级结论）
    if ok and not err and expected.get("json_path_contains"):
        missing = _check_json_paths(payload, expected["json_path_contains"])

    return CaseResult(
        case_id=case["id"], service=service, category=case["category"],
        name=case["name"], source=case.get("source", "rule"),
        method=req["method"], url=url, status=status, ok=ok,
        expected=expected, error=err,
        missing_fields=missing, resp_preview=text_snippet,
        json_keys=list(payload.keys())[:8] if isinstance(payload, dict) else None,
        elapsed_ms=elapsed_ms,
    )


def _summary(results: list[CaseResult]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    by = {"service": {}, "category": {}, "source": {}}
    for r in results:
        for key, val in (("service", r["service"]), ("category", r["category"]), ("source", r["source"])):
            d = by[key].setdefault(val, {"total": 0, "passed": 0})
            d["total"] += 1
            d["passed"] += 1 if r["ok"] else 0
    return {
        "total": total, "passed": passed, "failed": total - passed,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "by": by,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>API Test Report</title><style>
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#f7f8fa;color:#222}
h1{font-size:20px} .cards{display:flex;gap:12px;margin:16px 0}
.card{background:#fff;border-radius:10px;padding:14px 22px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.card b{font-size:26px;display:block}
.pass{color:#0a7d38}.fail{color:#c0392b}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border-bottom:1px solid #eee;padding:7px 9px;text-align:left;white-space:nowrap}
tr:hover{background:#f0f6ff}
.badge{padding:2px 8px;border-radius:10px;font-size:12px}
.b-pass{background:#e6f7ec;color:#0a7d38}.b-fail{background:#fdeaea;color:#c0392b}
details summary{cursor:pointer}
</style></head><body>
<h1>API 自动化测试报告 <small style="font-size:12px;color:#888">@@FINISHED@@</small></h1>
<div class="cards">
<div class="card">总用例<b>@@TOTAL@@</b></div>
<div class="card pass">通过<b>@@PASSED@@</b></div>
<div class="card fail">失败<b>@@FAILED@@</b></div>
<div class="card">通过率<b>@@RATE@@%</b></div>
</div>
<h3>按服务</h3><table><tr><th>服务</th><th>通过/总数</th><th>通过率</th></tr>@@BY_SERVICE@@</table>
<h3>明细</h3><table><tr><th>结果</th><th>ID</th><th>分类</th><th>来源</th><th>请求</th><th>状态码</th><th>耗时</th><th>说明</th></tr>@@ROWS@@</table>
</body></html>"""


def _render_html(results: list[CaseResult], summary: dict) -> str:
    import html as _html

    def esc(v) -> str:
        return _html.escape(str(v), quote=True)

    by_service = "".join(
        f"<tr><td>{esc(k)}</td><td>{v['passed']}/{v['total']}</td>"
        f"<td>{round(v['passed'] / v['total'] * 100, 1) if v['total'] else 0}%</td></tr>"
        for k, v in sorted(summary["by"]["service"].items())
    )
    # 失败排前，便于一眼定位问题
    rows: list[str] = []
    for r in sorted(results, key=lambda x: x["ok"]):
        badge = f"<span class='badge {'b-pass' if r['ok'] else 'b-fail'}'>{'PASS' if r['ok'] else 'FAIL'}</span>"
        note = r.get("error") or ""
        if r.get("missing_fields"):
            note = "缺失字段: " + ",".join(r["missing_fields"])
        elif not note and not r["ok"]:
            note = "期望: " + json.dumps(r.get("expected", {}), ensure_ascii=False)
        rows.append(
            f"<tr><td>{badge}</td>"
            f"<td><details><summary>{esc(r['case_id'])}</summary><pre style='white-space:pre-wrap'>{esc(r['name'])}<br>{esc(note)}</pre></details></td>"
            f"<td>{esc(r['category'])}</td><td>{esc(r['source'])}</td>"
            f"<td>{esc(r['method'])} {esc(r['url'].replace('http://localhost', ''))}</td>"
            f"<td>{r['status']}</td><td>{r['elapsed_ms']}ms</td><td>{esc(note)}</td></tr>"
        )

    html = _HTML
    for token, value in {
        "@@FINISHED@@": summary["finished_at"][:19],
        "@@TOTAL@@": summary["total"],
        "@@PASSED@@": summary["passed"],
        "@@FAILED@@": summary["failed"],
        "@@RATE@@": summary["pass_rate"],
        "@@BY_SERVICE@@": by_service,
        "@@ROWS@@": "".join(rows),
    }.items():
        html = html.replace(token, str(value))
    return html


def run_suite(
    cases_path: Path,
    out_dir: Path,
    workers: int = 8,
    only_service: str | None = None,
    only_category: str | None = None,
) -> dict:
    data = json.loads(cases_path.read_text(encoding="utf-8-sig"))
    cases = data["cases"]
    if only_service:
        cases = [c for c in cases if c["service"] == only_service]
    if only_category:
        cases = [c for c in cases if c["category"] == only_category]

    # Phase 7: 归一化鉴权标志（修复 AI 乱写 auth=none）
    auth_map: dict[str, str] = {}
    ep_file = cases_path.parent / "endpoints.json"
    if ep_file.exists():
        try:
            for svc in json.loads(ep_file.read_text(encoding="utf-8-sig")).get("services", []):
                for e in svc.get("endpoints", []):
                    # 注意：Endpoint.key 是 @property，model_dump() 不包含它，必须手工拼
                    auth_map[f'{e["method"]} {e["path"]}'] = e["auth"]
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] endpoints.json 解析失败，跳过归一化: {exc}")
    cases = _normalize_auth(cases, auth_map)

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[CaseResult] = []
    print(f"[RUN] 共 {len(cases)} 条用例, 并发 {workers}, 开始执行...", flush=True)
    with httpx.Client() as client:
        auth = AuthProvider(client)
        auth.ensure_ready()  # 先完成登录/注册 + userId 反查，数据链才能拿到 uid
        # Phase 7: 数据链供给（注册/卖家/listing/购物车/订单/支付 -> 真实 ID）
        registry: dict = {}
        prov = Provisioning(client, auth)
        try:
            registry = prov.run()
            if registry:
                print(f"[PROVISION] 数据链 registry: {registry}", flush=True)
            for note in prov.notes:
                print(f"  [chain] {note}", flush=True)
        except Exception as exc:  # noqa: BLE001 - 供给失败不阻断执行
            print(f"[WARN] 数据链供给失败（用例按原值执行）: {exc}", flush=True)
        summary_extra = {"registry": registry, "chain_notes": prov.notes}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_case, client, auth, c, registry) for c in cases]
            done = 0
            for f in as_completed(futures):
                results.append(f.result())
                done += 1
                # 实时进度：每 25 条或最后一条打印一次，避免长时间静默
                if done % 25 == 0 or done == len(cases):
                    print(f"  进度 {done}/{len(cases)}", flush=True)

    summary = _summary(results)
    summary["login_error"] = auth.login_error
    summary["provisioned_user"] = auth.provisioned_user
    summary.update(summary_extra)
    (out_dir / "results.json").write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "report.html").write_text(_render_html(results, summary), encoding="utf-8")
    return summary
