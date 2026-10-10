"""模型凭据探活（设计方案 7.8）。

生成主链路把任何异常都收敛成「不可用」，探活的价值就在于把原因分出来：
Key 错了、地址错了、模型名错了、还是根本连不上。这里逐类钉住那句话。
"""

from __future__ import annotations

import httpx
import pytest

from app.api import deps
from app.core.config import Settings
from app.services.generator import (
    OpenAICompatibleGenerator,
    UnconfiguredGenerator,
    build_generator,
)

KEY = "X-LLM-Api-Key"
BASE_URL = "X-LLM-Base-Url"
MODEL = "X-LLM-Model"


def _settings_with_key(key: str) -> Settings:
    return Settings(generator_api_key=key)


def _generator(handler) -> OpenAICompatibleGenerator:
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=1.0)
    return OpenAICompatibleGenerator("secret-key", "https://example.test/v1", "m", client=client)


def _generator_answering(status: int, body: str = "{}") -> OpenAICompatibleGenerator:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(status, text=body)

    return _generator(handler)


def test_probe_passes_on_a_normal_response():
    outcome = _generator_answering(200, '{"choices":[{"message":{"content":"pong"}}]}').probe()

    assert outcome.ok is True
    assert "m" in outcome.detail


def test_probe_body_stays_minimal():
    """探活只验证鉴权与地址：一次 max_tokens=1 的调用，且不带任何用户内容。"""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"choices": []})

    _generator(handler).probe()

    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["auth"] == "Bearer secret-key"
    assert captured["max_tokens"] == 1
    assert captured["model"] == "m"
    assert [item["role"] for item in captured["messages"]] == ["user"]


@pytest.mark.parametrize("status", [401, 403])
def test_probe_blames_the_key_on_auth_errors(status: int):
    outcome = _generator_answering(status).probe()

    assert outcome.ok is False
    assert "Key 无效" in outcome.detail


def test_probe_blames_the_base_url_on_404():
    outcome = _generator_answering(404).probe()

    assert outcome.ok is False
    assert "服务地址不对" in outcome.detail


def test_probe_blames_the_quota_on_429():
    outcome = _generator_answering(429).probe()

    assert outcome.ok is False
    assert "限流" in outcome.detail


def test_probe_blames_the_model_name_on_400():
    outcome = _generator_answering(400, '{"error":{"message":"model not found"}}').probe()

    assert outcome.ok is False
    assert "模型名可能不对" in outcome.detail
    assert "model not found" in outcome.detail


def test_probe_reports_a_long_body_shortened():
    outcome = _generator_answering(500, "x" * 500).probe()

    assert outcome.ok is False
    assert len(outcome.detail) < 200, "响应正文过长会把卡片撑破"


def test_probe_reports_a_connection_failure():
    def dead(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        raise httpx.ConnectError("连不上")

    outcome = _generator(dead).probe()

    assert outcome.ok is False
    assert "连不上模型服务" in outcome.detail


def test_unconfigured_generator_says_why():
    outcome = UnconfiguredGenerator().probe()

    assert outcome.ok is False
    assert "模型 Key" in outcome.detail
    assert "库内菜谱照常取" in outcome.detail


def test_build_generator_exposes_probe_on_both_implementations():
    """两种实现都要有 probe：探活接口不该因为服务端没配 Key 就 500。"""

    assert hasattr(build_generator(_settings_with_key("")), "probe")
    assert hasattr(build_generator(_settings_with_key("server-key")), "probe")


def test_probe_endpoint_without_any_key_still_answers(client):
    response = client.post("/api/llm/probe")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["configured"] is False
    assert body["model"] == "unset"
    assert "模型 Key" in body["detail"]


def test_probe_endpoint_uses_the_key_from_the_request_header(client, monkeypatch, stub_generator):
    captured: dict = {}

    def fake_build(settings, **kwargs):
        captured.update(kwargs)
        return stub_generator

    monkeypatch.setattr(deps, "build_generator", fake_build)
    response = client.post("/api/llm/probe", headers={KEY: "sk-user-key", MODEL: "deepseek-chat"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["model"] == "stub-generator"
    assert captured == {"api_key": "sk-user-key", "base_url": None, "model": "deepseek-chat"}


def test_probe_endpoint_rejects_a_plain_http_base_url(client):
    """明文把 Key 发到公网是明确要挡掉的情况，探活接口同样不能放行。"""

    response = client.post(
        "/api/llm/probe",
        headers={KEY: "sk-user", BASE_URL: "http://api.example.com/v1"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == 40001
