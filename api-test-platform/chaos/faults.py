"""容器级故障注入原语（docker CLI）。

容器名解析不写死 compose 项目前缀：用 `docker ps` 按 service 名模糊匹配，
本地（springboot-ecommerce-*）与 CI（runner 上同名）均适用。
"""
from __future__ import annotations

import subprocess
import time

import httpx

COMPOSE_SERVICES = {
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


def _docker(*args: str, timeout: int = 60) -> str:
    proc = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} 失败: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def ensure_running(service: str) -> bool:
    """确保容器在跑：stopped 则 start 并等健康。返回是否做了拉起动作。

    动机（2026-10-02 实测）：本地栈 recommendation-service 已退出（看门狗
    只在登录时跑的遗留坑），注入前必须先有可用基线。
    """
    running = _docker("ps", "--format", "{{.Names}}").splitlines()
    if any(service in n for n in running):
        return False
    all_names = _docker("ps", "-a", "--format", "{{.Names}}").splitlines()
    hits = [n for n in all_names if service in n]
    if not hits:
        raise RuntimeError(f"{service} 连 stopped 容器都没有，请先 docker compose up -d {service}")
    _docker("start", hits[0])
    wait_healthy(service)
    return True


def resolve_container(service: str) -> str:
    """按 service 名在运行中的容器里模糊匹配（如 *-recommendation-service-1）。"""
    names = _docker("ps", "--format", "{{.Names}}").splitlines()
    hits = [n for n in names if service in n]
    if not hits:
        raise RuntimeError(f"找不到 {service} 的运行容器，现有: {names}")
    return hits[0]


def stop_container(service: str) -> None:
    _docker("stop", "-t", "5", resolve_container(service))


def start_container(service: str) -> None:
    # 注意：刚 stop 的容器不在 `docker ps`（运行中）里，必须从 `ps -a` 解析
    # （2026-10-02 实测：S1 停机后起不回来就是这个原因，Exit 143 遗留）
    names = _docker("ps", "-a", "--format", "{{.Names}}").splitlines()
    hits = [n for n in names if service in n]
    if not hits:
        raise RuntimeError(f"找不到 {service} 的容器（含 stopped）: {names}")
    _docker("start", hits[0])


def wait_healthy(service: str, timeout_s: int = 90) -> bool:
    """轮询 actuator/health 直到 200 或超时。"""
    port = COMPOSE_SERVICES[service]
    deadline = time.time() + timeout_s
    url = f"http://localhost:{port}/actuator/health"
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False
