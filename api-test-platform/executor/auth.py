"""Phase 6 - 鉴权上下文。

被测系统双鉴权模式（来自 Phase 2 架构分析）：
  - user-service        : Authorization: Bearer <JWT>（/api/auth/login 换取）
  - cart/product/order/seller（网关内网可信头）: X-User-Id / X-User-Role
  - inventory/payment/recommendation: 开放
执行器按 service 自动注入对应凭证。
"""
from __future__ import annotations

import os
import threading

import httpx

from generator.config import load_dotenv

# 走内网可信头的服务（Phase 2 结论）
HEADER_AUTH_SERVICES = {"cart-service", "product-service", "order-service", "seller-service"}

# 服务 -> 直连端口（override 暴露），后续可切网关 8080
SERVICE_PORTS = {
    "user-service": 8081,
    "product-service": 8082,
    "cart-service": 8083,
    "inventory-service": 8084,
    "payment-service": 8085,
    "order-service": 8086,
    "notification-service": 8087,
    "recommendation-service": 8088,
    "catalog-stream-service": 8089,
    "seller-service": 8090,
}

LOGIN_EMAIL = os.getenv("TEST_LOGIN_EMAIL", "buyer1@example.com")
LOGIN_PASSWORD = os.getenv("TEST_LOGIN_PASSWORD", "password123")
TEST_USER_ID = os.getenv("TEST_USER_ID", "1")
TEST_USER_ROLE = os.getenv("TEST_USER_ROLE", "USER")


class AuthProvider:
    """登录一次拿 JWT，线程安全缓存；头模式按路径选择角色。

    凭据自供给：先用配置账号登录；若凭据无效（测试库未 seed），自动注册
    一个专属随机账号再登录——不依赖外部 seed 脚本，测试环境开箱即用。
    注册响应里的 userId 会被记录，供数据链（provision）使用。
    """

    def __init__(self, client: httpx.Client):
        load_dotenv()
        self._client = client
        self._token: str | None = None
        self._lock = threading.Lock()
        self.login_error: str | None = None
        self.provisioned_user: str | None = None
        self.user_id: int | None = None

    def _login(self, email: str, password: str) -> str | None:
        port = SERVICE_PORTS["user-service"]
        resp = self._client.post(
            f"http://localhost:{port}/api/auth/login",
            json={"email": email, "password": password},
            timeout=15,
        )
        if resp.status_code == 200:
            token = (resp.json().get("data") or {}).get("accessToken")
            if token:
                return token
        return None

    def ensure_ready(self) -> None:
        """显式触发登录/注册与 userId 反查（数据链供给前必须调用）。"""
        self._ensure_token()

    def _ensure_token(self) -> str | None:
        if self._token is not None or self.login_error:
            return self._token
        with self._lock:
            if self._token is not None or self.login_error:
                return self._token
            try:
                # 1) 配置账号
                token = self._login(LOGIN_EMAIL, LOGIN_PASSWORD)
                # 2) 兜底：自动注册专属测试账号（测试库可能未 seed）
                if not token:
                    import time

                    email = f"itest{int(time.time() * 1000) % 10**10}@example.com"
                    port = SERVICE_PORTS["user-service"]
                    reg = self._client.post(
                        f"http://localhost:{port}/api/auth/register",
                        json={"email": email, "password": LOGIN_PASSWORD},
                        timeout=15,
                    )
                    if reg.status_code in (200, 201):
                        try:
                            self.user_id = (reg.json().get("data") or {}).get("id")
                        except Exception:  # noqa: BLE001
                            self.user_id = None
                        token = self._login(email, LOGIN_PASSWORD)
                        if token:
                            self.provisioned_user = email
                    else:
                        self.login_error = (
                            f"register HTTP {reg.status_code}: {str(reg.text)[:150]}"
                        )
                if token:
                    self._token = token
                    # 登录分支没有 userId，用 /api/users/me 反查（数据链依赖它）
                    if self.user_id is None:
                        try:
                            me = self._client.get(
                                f"http://localhost:{SERVICE_PORTS['user-service']}/api/users/me",
                                headers={"Authorization": f"Bearer {token}"},
                                timeout=15,
                            )
                            self.user_id = (me.json().get("data") or {}).get("id")
                        except Exception:  # noqa: BLE001
                            pass
                elif not self.login_error:
                    self.login_error = "登录与自动注册均未取得 token"
            except Exception as exc:  # noqa: BLE001 - 登录失败必须兜底
                self.login_error = f"login 异常: {exc}"
        return self._token

    def headers_for(self, service: str, auth: str, path: str = "") -> dict[str, str]:
        """按服务与用例 auth 要求生成注入头；路径决定角色（ADMIN 专属路径）。"""
        if auth != "jwt":
            return {}
        if service in HEADER_AUTH_SERVICES:
            if path.startswith(("/api/coupons", "/api/admin")):
                role = "ADMIN"
            elif service == "seller-service":
                role = "SELLER"
            else:
                role = "USER"
            return {"X-User-Id": str(self.user_id or 1), "X-User-Role": role}
        token = self._ensure_token()
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

