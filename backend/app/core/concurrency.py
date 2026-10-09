"""推理并发的准入闸门（设计方案 5.3.1 / NFR-01）。

ONNX 推理是同步的 CPU 密集调用。直接写在 async 路由里会把整个事件循环占住：
实测 4 个并发上传的墙钟时间与串行一模一样（618 ms vs 610 ms），而且这段时间
连 /api/health 都要等 613 ms 才返回——它虽然跑在线程池里，但请求根本没能被
事件循环派发出去。

这里只做两件事：把推理交给线程池执行，以及用闸门限制「同时在跑的推理数」。
闸门上限不是越大越好：本机（16 逻辑核）实测吞吐在并发 4 时到顶（11.75 req/s），
再往上加并发，吞吐不变而单请求延迟翻倍（并发 8：11.97 req/s，但 340 ms → 668 ms）。
所以默认 4，可用 RECIPE_RECOGNIZE_MAX_CONCURRENCY 覆盖。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator, Optional


class InferenceGate:
    """限制同时在跑的推理数；超出的请求在闸门前排队，而不是一起抢 CPU。

    信号量在第一次用到时创建，并记下当时的事件循环：create_app() 在导入期执行，
    那时还没有事件循环，提前构造 asyncio.Semaphore 会把它绑到错误的循环上。
    判断与赋值之间没有 await，两个协程不会同时走进创建分支。
    """

    def __init__(self, limit: int = 4) -> None:
        self.limit = max(1, int(limit))
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def in_flight(self) -> int:
        """当前占用名额的推理数（健康检查与压测用）。"""

        if self._semaphore is None:
            return 0
        return self.limit - self._semaphore._value  # noqa: SLF001 - 只读展示

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """占一个推理名额；拿不到就排队等，不会把事件循环堵住。"""

        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._semaphore = asyncio.Semaphore(self.limit)
            self._loop = loop
        async with self._semaphore:
            yield
