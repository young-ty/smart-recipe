"""识别并发的回归契约（设计方案 NFR-01）。

ONNX 推理是同步的 CPU 密集调用。早先它直接写在 async 路由里，会把整个事件循环占住：
实测 4 个并发上传的墙钟时间与串行完全相同（618 ms vs 610 ms），而且这期间
`/api/health` 要等 613 ms 才返回——健康检查虽然跑在线程池里，请求根本没被事件循环
派发出去。这里用「阻塞式假识别器」把当时的现象固化成回归用例：推理可以慢，但不能把
其它接口一起拖住，也不能让超过闸门上限的推理同时抢 CPU。

运行：python -m pytest backend/tests/test_recognize_concurrency.py -v
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
import time

import httpx
import pytest

from app.api.deps import get_recognizer
from app.main import create_app
from app.services.recognizer import DishCandidate, RecognitionResult

BLOCKING_SECONDS = 0.6
# 未被堵住时：睡眠 0.2 s + 健康检查几毫秒；被堵住时会被拉到推理的 0.6 s 量级。
# 阈值取两者之间，留出机器抖动的余量。
HEALTH_BUDGET_SECONDS = BLOCKING_SECONDS * 2 / 3


class BlockingRecognizer:
    """假识别器：像真模型一样同步阻塞，并记录同时在跑的推理数。"""

    primary = None
    model_version = "blocking-stub"
    degraded = False
    class_count = 208
    labels_available = True
    providers = ("CPUExecutionProvider",)

    def __init__(self, delay: float = BLOCKING_SECONDS) -> None:
        self.delay = delay
        self.calls = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()
        self._in_flight = 0

    def recognize(self, image: bytes) -> RecognitionResult:
        with self._lock:
            self.calls += 1
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            time.sleep(self.delay)  # 同步阻塞，等价于一次 CPU 推理
        finally:
            with self._lock:
                self._in_flight -= 1
        return RecognitionResult(
            candidates=[
                DishCandidate(index=0, name="麻婆豆腐", confidence=0.9, provisional_name=False)
            ],
            model_version=self.model_version,
            class_count=self.class_count,
            labels_available=self.labels_available,
            degraded=False,
        )


def _build_app(settings, recognizer):
    app = create_app(settings)
    app.dependency_overrides[get_recognizer] = lambda: recognizer
    return app


def test_slow_inference_does_not_stall_other_endpoints(settings, png_bytes, upload):
    recognizer = BlockingRecognizer()
    app = _build_app(settings, recognizer)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inference = asyncio.create_task(client.post("/api/recognize", files=upload(png_bytes)))
            await asyncio.sleep(0.02)  # 让识别先进到阻塞段
            # 量「睡眠 + 健康检查」的总耗时：事件循环一旦被推理占住，连 0.2 秒的定时器
            # 都要等推理结束才恢复，这一段的总耗时会被拉到与推理同量级。
            started = time.perf_counter()
            await asyncio.sleep(BLOCKING_SECONDS / 3)
            health = await client.get("/api/health")
            elapsed = time.perf_counter() - started
            result = await inference
            return result, health, elapsed

    result, health, elapsed = asyncio.run(scenario())

    assert result.status_code == 200, result.text
    assert health.status_code == 200, health.text
    assert health.json()["model"]["version"] == "blocking-stub"
    assert elapsed < HEALTH_BUDGET_SECONDS, (
        "识别在跑的时候事件循环被堵了 %.0f ms，说明推理又回到事件循环里了" % (elapsed * 1000)
    )


def test_concurrent_inference_is_capped_by_the_gate(settings, png_bytes, upload):
    """闸门上限 2：6 个并发上传最多只应有两个推理同时在跑。"""

    gate_limit = 2
    recognizer = BlockingRecognizer(delay=0.2)
    app = _build_app(
        dataclasses.replace(settings, recognize_max_concurrency=gate_limit), recognizer
    )

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await asyncio.gather(
                *[client.post("/api/recognize", files=upload(png_bytes)) for _ in range(6)]
            )

    responses = asyncio.run(scenario())
    assert [item.status_code for item in responses] == [200] * 6
    assert recognizer.calls == 6
    assert recognizer.max_in_flight <= gate_limit, (
        "同时在跑的推理数 %d 超过闸门上限 %d" % (recognizer.max_in_flight, gate_limit)
    )


def test_recognition_logs_inference_and_queue_time(client, png_bytes, upload, caplog):
    """每次成功识别都要留下推理耗时与排队耗时，慢请求才有得查。"""

    with caplog.at_level("INFO", logger="app.api.routes_recognize"):
        response = client.post("/api/recognize", files=upload(png_bytes))

    assert response.status_code == 200, response.text
    messages = [record.getMessage() for record in caplog.records]
    assert any("识别完成" in message and "排队=" in message for message in messages), messages
