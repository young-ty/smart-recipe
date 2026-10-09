"""请求上下文、结构化日志与指标接口测试（设计方案 5.5）。

覆盖：request id 的沿用与生成、/metrics 的暴露内容、业务指标确实被写进去，
以及日志记录带没带上同一条 request id。
"""

from __future__ import annotations

import dataclasses
import json
import logging

import pytest

from app.core.metrics import (
    GENERATOR_DURATION,
    GENERATOR_REQUESTS,
    GENERATOR_TOKENS,
    RECIPE_CACHE,
    RECIPE_RESOLVE,
    RECOGNIZE_IN_FLIGHT,
    RECOGNIZE_INFERENCE,
    RECOGNIZE_QUEUE,
    RECOGNIZE_REQUESTS,
    REGISTRY,
)
from app.core.observability import (
    JsonFormatter,
    RequestIdFilter,
    TextFormatter,
    configure_logging,
    request_id_context,
    sanitize_request_id,
)
from app.services.generator import OpenAICompatibleGenerator, record_usage
from app.services.recipe_resolver import RecipeResolver

GENERATED_DISH = "土豆肉末拌面"


@pytest.fixture()
def clean_registry():
    """指标是进程内全局的，用例前后各清一次，断言才能写成绝对值。"""

    REGISTRY.reset()
    yield REGISTRY
    REGISTRY.reset()


class FakeResponse:
    """大模型响应的替身：只保留生成层真正读到的三个方法。"""

    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


def generated_body() -> dict:
    payload = {
        "name": "红烧肉",
        "cook_time_minutes": 90,
        "difficulty": "中等",
        "servings": 4,
        "description": "小火慢炖，收汁到挂勺",
        "required": [{"name": "五花肉", "amount": "600 克"}],
        "optional": [{"name": "葱", "amount": "1 根"}],
        "steps": [
            "五花肉切三厘米见方的块，冷水下锅焯去血沫",
            "锅里少油炒糖色到枣红，下肉块翻匀上色",
            "加开水没过肉面，小火炖六十分钟",
            "开盖转大火收汁，汤汁能挂住勺就可以出锅",
        ],
    }
    return {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 210, "completion_tokens": 380},
    }


# --------------------------------------------------------------- request id

def test_request_id_is_generated_and_echoed(client):
    response = client.get("/api/health")
    assert len(response.headers["x-request-id"]) == 16


def test_request_id_from_header_is_reused(client):
    response = client.get("/api/health", headers={"X-Request-ID": "trace-abc-123"})
    assert response.headers["x-request-id"] == "trace-abc-123"


def test_oversized_request_id_is_regenerated(client):
    response = client.get("/api/health", headers={"X-Request-ID": "x" * 200})
    assert response.headers["x-request-id"] != "x" * 200
    assert len(response.headers["x-request-id"]) == 16


def test_request_id_sanitizer_rules():
    assert sanitize_request_id("  abc  ") == "abc"
    assert sanitize_request_id("") == ""
    assert sanitize_request_id(None) == ""
    assert sanitize_request_id("a" * 65) == ""
    assert sanitize_request_id("a" * 64) == "a" * 64
    assert sanitize_request_id("bad\nvalue") == ""
    assert sanitize_request_id("bad\x7fvalue") == ""


def test_error_responses_also_carry_request_id(client):
    response = client.get("/api/recipes/999999")
    assert response.status_code == 404
    assert len(response.headers["x-request-id"]) == 16


# --------------------------------------------------------------- /metrics

def test_metrics_endpoint_serves_prometheus_text(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in response.headers["content-type"]
    body = response.text
    assert "# TYPE http_requests_total counter" in body
    assert "# TYPE recognize_inference_seconds histogram" in body
    assert "# TYPE recognize_in_flight gauge" in body


def test_metrics_endpoint_is_not_counted_as_traffic(client):
    client.get("/api/health")
    client.get("/metrics")
    body = client.get("/metrics").text
    assert 'path="/api/health"' in body
    assert 'path="/metrics"' not in body


def test_cors_preflight_is_not_counted_as_traffic(client):
    """前端跨域时每个请求前都有一次预检，算进流量会让 QPS 翻倍、p50 腰斩。"""

    response = client.options(
        "/api/recognize",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert 'method="OPTIONS"' not in client.get("/metrics").text


# --------------------------------------------------------------- 业务指标

def test_recognize_records_domain_metrics(client, png_bytes, upload, clean_registry):
    client.post("/api/recognize", files=upload(png_bytes))
    assert RECOGNIZE_REQUESTS.value(outcome="degraded") == 1
    assert RECOGNIZE_INFERENCE.count() == 1
    assert RECOGNIZE_QUEUE.count() == 1
    # 请求结束后名额必须还回去，否则闸门会越用越窄
    assert RECOGNIZE_IN_FLIGHT.value() == 0
    assert 'path="/api/recognize"' in clean_registry.render()


def test_resolve_records_library_source(client, clean_registry):
    body = client.post("/api/recipes/resolve", json={"dish": "红烧肉"}).json()
    assert body["source"] == "library"
    assert RECIPE_RESOLVE.value(source="library") == 1
    assert RECIPE_CACHE.value(result="miss") == 0
    assert RECIPE_CACHE.value(result="hit") == 0


def test_resolve_records_unavailable_source(client, clean_registry):
    body = client.post("/api/recipes/resolve", json={"dish": GENERATED_DISH}).json()
    assert body["source"] == "unavailable"
    assert RECIPE_RESOLVE.value(source="unavailable") == 1
    # 没配 Key：缓存查过一次（miss），生成层记一档 skipped
    assert RECIPE_CACHE.value(result="miss") == 1
    assert GENERATOR_REQUESTS.value(outcome="skipped") == 1


def test_cache_hit_skips_the_generator(app, session, stub_generator, clean_registry):
    """菜谱记忆的价值就在这条：同一道菜第二次不再调大模型。"""

    settings = dataclasses.replace(app.state.settings, promote_generated=False)
    resolver = RecipeResolver(session, settings, stub_generator)

    first = resolver.resolve(GENERATED_DISH)
    second = resolver.resolve(GENERATED_DISH)

    assert first.source == "generated" and second.source == "generated"
    assert stub_generator.calls == [GENERATED_DISH]
    assert RECIPE_CACHE.value(result="miss") == 1
    assert RECIPE_CACHE.value(result="hit") == 1
    assert RECIPE_RESOLVE.value(source="generated") == 2


# --------------------------------------------------------------- 生成层指标

def test_record_usage_counts_tokens_and_ignores_broken_input(clean_registry):
    record_usage({"prompt_tokens": 120, "completion_tokens": 340})
    record_usage(None)
    record_usage({})
    record_usage({"prompt_tokens": "many", "completion_tokens": 0})
    assert GENERATOR_TOKENS.value(kind="input") == 120
    assert GENERATOR_TOKENS.value(kind="output") == 340


def test_generator_records_ok_outcome_and_tokens(clean_registry):
    class StubClient:
        def post(self, *args, **kwargs):
            return FakeResponse(generated_body())

    generator = OpenAICompatibleGenerator(
        "key", "https://example.invalid/v1", "demo-model", client=StubClient()
    )
    payload = generator.generate("红烧肉")
    assert payload is not None and payload.name == "红烧肉"
    assert GENERATOR_REQUESTS.value(outcome="ok") == 1
    assert GENERATOR_DURATION.count() == 1
    assert GENERATOR_TOKENS.value(kind="input") == 210
    assert GENERATOR_TOKENS.value(kind="output") == 380


def test_generator_records_error_outcome_when_the_call_fails(clean_registry):
    class BoomClient:
        def post(self, *args, **kwargs):
            raise RuntimeError("network down")

    generator = OpenAICompatibleGenerator(
        "key", "https://example.invalid/v1", "demo-model", client=BoomClient()
    )
    assert generator.generate("红烧肉") is None
    assert GENERATOR_REQUESTS.value(outcome="error") == 1
    # 失败也要记耗时：超时的那几次正是最需要看见的
    assert GENERATOR_DURATION.count() == 1
    assert GENERATOR_TOKENS.value(kind="input") == 0


def test_generator_records_invalid_outcome_for_unusable_json(clean_registry):
    class StubClient:
        def post(self, *args, **kwargs):
            return FakeResponse(
                {"choices": [{"message": {"content": '{"name": "红烧肉"}'}}]}
            )

    generator = OpenAICompatibleGenerator(
        "key", "https://example.invalid/v1", "demo-model", client=StubClient()
    )
    assert generator.generate("红烧肉") is None
    assert GENERATOR_REQUESTS.value(outcome="invalid") == 1


# --------------------------------------------------------------- 日志

def test_request_id_filter_marks_records():
    record = logging.LogRecord("demo", logging.INFO, __file__, 1, "消息", (), None)
    assert RequestIdFilter().filter(record) is True
    assert record.request_id == "-"


def test_text_formatter_takes_request_id_from_the_context():
    record = logging.LogRecord("demo", logging.INFO, __file__, 1, "识别完成", (), None)
    with request_id_context("trace123"):
        line = TextFormatter("%(levelname)s [req=%(request_id)s] %(message)s").format(
            record
        )
    assert line == "INFO [req=trace123] 识别完成"


def test_json_formatter_emits_one_object_per_record():
    record = logging.LogRecord("demo", logging.WARNING, __file__, 1, "降级 %s", ("超时",), None)
    with request_id_context("trace123"):
        payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "demo"
    assert payload["request_id"] == "trace123"
    assert payload["message"] == "降级 超时"


def test_logs_inside_a_request_carry_the_response_request_id(client, png_bytes, upload):
    lines: list = []

    class Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    sink = Sink()
    sink.setFormatter(TextFormatter())
    root = logging.getLogger()
    root.addHandler(sink)
    try:
        response = client.post("/api/recognize", files=upload(png_bytes))
    finally:
        root.removeHandler(sink)

    request_id = response.headers["x-request-id"]
    assert any(f"[req={request_id}]" in line for line in lines)


def test_configure_logging_is_idempotent_and_switchable():
    handler = configure_logging(level="INFO", log_format="json")
    configure_logging(level="INFO", log_format="json")
    ours = [
        item
        for item in logging.getLogger().handlers
        if getattr(item, "_recipe_handler", False)
    ]
    assert ours == [handler]
    assert isinstance(handler.formatter, JsonFormatter)
    try:
        configure_logging(level="INFO", log_format="text")
        assert isinstance(handler.formatter, TextFormatter)
    finally:
        configure_logging(level="INFO", log_format="text")


def test_log_stream_is_forced_to_utf8():
    """重定向到文件时 Windows 默认走 ANSI 代码页，中文日志会变成乱码。"""

    from app.core import observability

    calls = []

    class FakeStream:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    observability.ensure_utf8(FakeStream())
    assert calls == [{"encoding": "utf-8"}]


def test_log_stream_without_reconfigure_is_left_alone():
    from app.core import observability

    observability.ensure_utf8(None)
    observability.ensure_utf8(object())


def test_chatty_third_party_loggers_are_pinned_to_warning():
    """根日志放开到 INFO 是为了业务日志，别连带把 httpx 的逐请求日志也放开。"""

    configure_logging(level="INFO", log_format="text")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("app.api.routes_recognize").getEffectiveLevel() <= logging.INFO
