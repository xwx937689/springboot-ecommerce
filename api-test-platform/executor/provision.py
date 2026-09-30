"""Phase 7 - 业务数据链供给。

空库上"查询/操作"类用例必然 404/400。这里按业务 Saga 顺序构造一份真实数据：

  注册/登录(AuthProvider) → 申请卖家 → 上架 listing → 加购 → 下单 → 支付

产出 registry = {userId, sellerId, listingId, productId, orderId, paymentId}，
执行器用它替换 positive/conflict/boundary 用例中的资源 ID 占位。

每一步 best-effort：失败记录原因、继续后续步骤（不同用例依赖不同前缀）。
"""
from __future__ import annotations

import httpx

from .auth import AuthProvider, SERVICE_PORTS

CARD = {
    "holderName": "ITest User",
    "number": "5528790000000008",
    "expireMonth": "12",
    "expireYear": "2030",
    "cvc": "123",
}


class Provisioning:
    def __init__(self, client: httpx.Client, auth: AuthProvider):
        self.client = client
        self.auth = auth
        self.registry: dict[str, int] = {}
        self.notes: list[str] = []

    def _url(self, svc: str, path: str) -> str:
        return f"http://localhost:{SERVICE_PORTS[svc]}{path}"

    def _headers(self, role: str = "USER") -> dict[str, str]:
        uid = self.auth.user_id or 1
        return {"X-User-Id": str(uid), "X-User-Role": role}

    def _post(self, name: str, url: str, body: dict, headers: dict | None = None,
              method: str = "POST") -> dict:
        """发请求并尽量抽取 data；失败记 note 返回空 dict。"""
        try:
            resp = self.client.request(method, url, json=body, headers=headers or {}, timeout=15)
            data = {}
            try:
                data = resp.json().get("data") or {}
            except Exception:  # noqa: BLE001 - 非 JSON 响应
                pass
            if resp.status_code < 400 and isinstance(data, dict):
                self.notes.append(f"{name}: OK id={data.get('id')}")
                return data
            self.notes.append(f"{name}: HTTP {resp.status_code} {str(resp.text)[:100]}")
        except Exception as exc:  # noqa: BLE001
            self.notes.append(f"{name}: 异常 {exc}")
        return {}

    def run(self) -> dict[str, int]:
        reg = self.registry
        uid = self.auth.user_id
        if uid is None:
            self.notes.append("未能确定 userId，跳过数据链（用例按原值执行）")
            return reg
        reg["userId"] = int(uid)
        email = self.auth.provisioned_user or "itest@example.com"

        # 1) 申请卖家（409=已有记录，改查 /api/sellers/me 拿回）
        seller = self._post(
            "apply-seller",
            self._url("seller-service", "/api/sellers/apply"),
            {"businessName": "ITest Shop", "contactEmail": email,
             "taxId": "TAX-ITEST", "iban": "TR0000000000000000"},
            self._headers("SELLER"),
        )
        if not seller.get("id"):
            try:
                me = self.client.get(
                    self._url("seller-service", "/api/sellers/me"),
                    headers=self._headers("SELLER"), timeout=15,
                )
                seller = me.json().get("data") or {}
                self.notes.append(f"seller/me: HTTP {me.status_code} id={seller.get('id')} status={seller.get('status')}")
            except Exception as exc:  # noqa: BLE001
                self.notes.append(f"seller/me: 异常 {exc}")
        if seller.get("id"):
            reg["sellerId"] = int(seller["id"])
            # 1b) 激活卖家（业务规则：申请后需 ADMIN 激活才能上架）
            self._post(
                "activate-seller",
                self._url("seller-service", f"/api/sellers/admin/{seller['id']}"),
                {"status": "ACTIVE"},
                self._headers("ADMIN"),
                method="PATCH",
            )

        # 2) 上架 listing（productId 依赖产品服务有数据；先用占位 1，若产品存在则真实可购）
        listing = self._post(
            "create-listing",
            self._url("seller-service", "/api/listings"),
            {"productId": 1, "sellerId": reg.get("sellerId", uid),
             "priceAmount": 10.0, "priceCurrency": "TRY",
             "stockQuantity": 5, "shippingDays": 2, "enabled": True},
            self._headers("SELLER"),
        )
        if listing.get("id"):
            reg["listingId"] = int(listing["id"])
            if listing.get("productId"):
                reg["productId"] = int(listing["productId"])
        else:
            # 409 等失败时回退：查自己的 listing 列表拿已有 ID（业务幂等，数据仍在）
            try:
                mine = self.client.get(
                    self._url("seller-service", "/api/listings/me"),
                    headers=self._headers("SELLER"), timeout=15,
                )
                payload = mine.json().get("data")
                items = payload if isinstance(payload, list) else (payload or {}).get("content") or []
                if isinstance(items, list) and items:
                    first = items[0]
                    if first.get("id"):
                        reg["listingId"] = int(first["id"])
                    if first.get("productId"):
                        reg["productId"] = int(first["productId"])
                    self.notes.append(f"listings/me: 取回 listingId={reg.get('listingId')} productId={reg.get('productId')}")
            except Exception as exc:  # noqa: BLE001
                self.notes.append(f"listings/me: 异常 {exc}")

        # 3) 加购（listingId 是可选参数且其读模型有同步问题，实测不传即可成功）
        self._post(
            "cart-add",
            self._url("cart-service", "/api/cart/items"),
            {"productId": reg.get("productId", 1), "quantity": 1},
            self._headers(),
        )

        # 4) 下单（对当前购物车结账）
        order = self._post(
            "place-order",
            self._url("order-service", "/api/orders"),
            {"card": CARD},
            self._headers(),
        )
        if order:
            oid = order.get("id") or order.get("orderId")
            if oid:
                reg["orderId"] = int(oid)

        # 5) 支付（对刚下的订单收款；payment-service 开放，无需头）
        if reg.get("orderId"):
            pay = self._post(
                "charge-payment",
                self._url("payment-service", "/api/payments"),
                {"orderId": reg["orderId"], "amount": 10.0,
                 "currency": "TRY", "card": CARD},
            )
            if pay:
                pid = pay.get("id") or pay.get("paymentId")
                if pid:
                    reg["paymentId"] = int(pid)

        return reg
