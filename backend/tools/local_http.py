"""打本机回环服务的 HTTP 客户端：绕开环境与系统代理。

开发机上常有两种代理配置：环境变量（`HTTP_PROXY` / `NO_PROXY`）与 Windows 注册表里的
系统代理。httpx 默认 `trust_env=True` 会把它们读进来，于是指向 `127.0.0.1` 的请求也可能
被送进代理：代理不转发回环流量时直接断连（`RemoteProtocolError`），就算转发，多绕一跳的
数字也不能当本机性能。压测与读指标打的是自己的服务，这里统一关掉。
"""

from __future__ import annotations

from typing import Any


def local_async_client(**kwargs: Any):
    """异步客户端；默认 `trust_env=False`，调用方显式传值仍可覆盖。"""

    import httpx

    kwargs.setdefault("trust_env", False)
    return httpx.AsyncClient(**kwargs)


def local_get(url: str, **kwargs: Any):
    """同步 GET；同样不吃代理。"""

    import httpx

    kwargs.setdefault("trust_env", False)
    return httpx.get(url, **kwargs)
