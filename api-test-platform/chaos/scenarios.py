"""Phase 14 - 韧性场景：降级、支付幂等竞态、库存超卖竞态。

断言的是"系统语义"而非"单接口契约"：
  - S1 优雅降级：依赖挂掉 → 快速失败（熔断/连接拒绝均可），恢复后自愈
  - S2 支付幂等竞态：并发重复扣款 → 恰好一次 2xx（多了=双重扣款缺陷）
  - S3 库存超卖竞态：并发抢购 → available_qty 永不为负

所有场景信任头直连（与执行器同口径），数据自适应（自动挑有库存的商品）。
"""
from __future__ import annotations

import concurrent.futures
import time

import httpx

from .faults import (
    COMPOSE_SERVICES,
    ensure_running,
    start_container,
    stop_container,
    wait_healthy,
)

CARD = {
    "holderName": "ITest User",
    "number": "5528790000000008",
    "expireMonth": "12",
    "expireYear": "2030",
    "cvc": "123",
}


def _pick_product(client: httpx.Client, min_stock: int = 30) -> tuple[int, int]:
    """在 seed 商品 1..20 中挑一个可用库存 >= min_stock 的，返回 (productId, stock)。"""
    last_err = ""
    for pid in range(1, 21):
        try:
            r = client.get(f"http://localhost:8084/api/inventory/{pid}", timeout=5)
            if r.status_code != 200:
                last_err = f"product {pid}: HTTP {r.status_code}"
                continue
            data = r.json().get("data") or {}
            avail = int(
                data.get("availableQty") or data.get("available_qty") or data.get("available") or 0
            )
            if avail >= min_stock:
                return pid, avail
            last_err = f"product {pid}: stock {avail}"
        except Exception as exc:  # noqa: BLE001
            last_err = f"product {pid}: {exc}"
    raise RuntimeError(f"没有可用库存 >= {min_stock} 的商品（{last_err}）")


def _place_order(client: httpx.Client, user_id: int, product_id: int, qty: int):
    """信任头用户：加购 + 下单。返回 (orderId, notes)。"""
    port = COMPOSE_SERVICES["cart-service"]
    # cart/order 是信任头模式，X-User-Id + X-User-Role 两个头都必须带
    # （实测只带 id 会 401——与执行器 provisioning 的 _headers() 同口径）
    headers = {"X-User-Id": str(user_id), "X-User-Role": "USER"}
    r = client.post(
        f"http://localhost:{port}/api/cart/items",
        json={"productId": product_id, "quantity": qty},
        headers=headers, timeout=10,
    )
    if r.status_code >= 400:
        return None, f"cart-add HTTP {r.status_code}"
    r = client.post(
        f"http://localhost:{COMPOSE_SERVICES['order-service']}/api/orders",
        json={"card": CARD}, headers=headers, timeout=15,
    )
    if r.status_code >= 400:
        return None, f"place-order HTTP {r.status_code}"
    data = r.json().get("data") or {}
    oid = data.get("id") or data.get("orderId")
    return (int(oid) if oid else None), "ok"


def scenario_degradation(client: httpx.Client) -> dict:
    """S1: recommendation-service 停机 → 优雅降级 → 重启自愈。"""
    notes: list[str] = []
    svc = "recommendation-service"
    url = f"http://localhost:{COMPOSE_SERVICES[svc]}/api/recommendations/users/1"

    if ensure_running(svc):
        notes.append("注入前发现容器未运行，已拉起（环境基线修复）")

    base = client.get(url, timeout=5)
    notes.append(f"基线: HTTP {base.status_code}")
    baseline_ok = base.status_code < 500

    stop_container(svc)
    notes.append(f"已 stop {svc}")

    t0 = time.perf_counter()
    degraded_status = None
    degraded_err = None
    try:
        degraded_status = client.get(url, timeout=5).status_code
    except httpx.HTTPError as exc:
        degraded_err = type(exc).__name__
    elapsed = round(time.perf_counter() - t0, 2)
    notes.append(f"降级响应: status={degraded_status} err={degraded_err} elapsed={elapsed}s")

    # 语义断言：快速失败（不挂死），且不是裸 500
    fast_fail = elapsed < 5.0 and degraded_status != 500
    notes.append(f"快速失败判定: {'PASS' if fast_fail else 'FAIL'}")

    start_container(svc)
    healthy = wait_healthy(svc)
    notes.append(f"重启后健康: {healthy}")
    recovered = False
    if healthy:
        r = client.get(url, timeout=5)
        recovered = r.status_code < 500
        notes.append(f"恢复后请求: HTTP {r.status_code}")

    passed = baseline_ok and fast_fail and healthy and recovered
    return {"scenario": "S1-优雅降级", "passed": passed, "notes": notes}


def scenario_payment_race(client: httpx.Client) -> dict:
    """S2: 同一订单 6 线程并发扣款 → 恰好一次 2xx。"""
    notes: list[str] = []
    product_id, _stock = _pick_product(client)
    notes.append(f"测试商品: {product_id}")

    order_id, note = _place_order(client, 7001, product_id, 1)
    if not order_id:
        notes.append(f"前置下单失败（{note}），场景跳过")
        return {"scenario": "S2-支付幂等竞态", "passed": False,
                "notes": notes, "skipped": True}
    notes.append(f"orderId={order_id}")

    def charge(i: int):
        try:
            r = client.post(
                f"http://localhost:{COMPOSE_SERVICES['payment-service']}/api/payments",
                json={"orderId": order_id, "amount": 10.0, "currency": "TRY", "card": CARD},
                timeout=10,
            )
            return r.status_code
        except httpx.HTTPError as exc:
            return f"ERR:{type(exc).__name__}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(charge, range(6)))
    notes.append(f"6 并发扣款状态码: {statuses}")

    ok_count = sum(1 for s in statuses if isinstance(s, int) and s < 300)
    if ok_count == 1:
        notes.append("幂等语义: PASS（恰好一次成功）")
        return {"scenario": "S2-支付幂等竞态", "passed": True, "notes": notes}
    if ok_count > 1:
        notes.append(f"幂等语义: FAIL（{ok_count} 次成功 = 双重扣款缺陷）")
    else:
        notes.append("幂等语义: 无法判定（0 次成功，扣款全被拒，检查前置数据）")
    return {"scenario": "S2-支付幂等竞态", "passed": False, "notes": notes}


def scenario_oversell_race(client: httpx.Client) -> dict:
    """S3: 5 个虚拟用户并发抢购 → available_qty 不为负。"""
    notes: list[str] = []
    product_id, stock = _pick_product(client)
    qty = max(1, stock // 3)
    notes.append(f"测试商品: {product_id}  初始库存: {stock}  每人抢购: {qty}")

    def buy(i: int) -> str:
        user_id = 8100 + i
        try:
            order_id, note = _place_order(client, user_id, product_id, qty)
            return f"user{user_id}: {note} (order={order_id})"
        except Exception as exc:  # noqa: BLE001
            return f"user{user_id}: EXC {type(exc).__name__}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(buy, range(5)))
    notes += outcomes

    r = client.get(f"http://localhost:8084/api/inventory/{product_id}", timeout=5)
    if r.status_code != 200:
        notes.append(f"回读库存失败: HTTP {r.status_code}")
        return {"scenario": "S3-库存超卖竞态", "passed": False, "notes": notes}
    data = r.json().get("data") or {}
    after = int(data.get("availableQty") or data.get("available_qty") or data.get("available") or -999)
    notes.append(f"回读库存: {after}")
    ok_orders = sum(1 for o in outcomes if "order=None" not in o and "HTTP" not in o and "EXC" not in o)
    oversold = after < 0
    over_allocated = ok_orders * qty > stock
    notes.append(
        f"成功下单 {ok_orders} 单 x {qty} = {ok_orders * qty} / 初始 {stock}"
        f"  超卖={oversold}  超分配={over_allocated}"
    )
    if ok_orders == 0:
        # 空洞通过不算数：0 单成功说明前置就坏了，超卖语义根本没被 exercise
        notes.append("不变量判定: 无法判定（0 单成功，先修环境再跑）")
        return {"scenario": "S3-库存超卖竞态", "passed": False, "notes": notes}
    passed = (not oversold) and (not over_allocated)
    notes.append(f"不变量判定: {'PASS' if passed else 'FAIL'}")
    return {"scenario": "S3-库存超卖竞态", "passed": passed, "notes": notes}


SCENARIOS = {
    "degradation": scenario_degradation,
    "payment-race": scenario_payment_race,
    "oversell-race": scenario_oversell_race,
}


def run(scenarios: list[str] | None = None) -> list[dict]:
    names = scenarios or list(SCENARIOS)
    results: list[dict] = []
    with httpx.Client() as client:
        for name in names:
            fn = SCENARIOS.get(name)
            if fn is None:
                results.append({"scenario": name, "passed": False,
                                "notes": [f"未知场景，可选: {list(SCENARIOS)}"]})
                continue
            t0 = time.perf_counter()
            try:
                res = fn(client)
            except Exception as exc:  # noqa: BLE001 - 场景失败不中断其余场景
                res = {"scenario": name, "passed": False,
                       "notes": [f"场景异常: {type(exc).__name__}: {exc}"]}
            res["elapsed_s"] = round(time.perf_counter() - t0, 1)
            results.append(res)
    return results
