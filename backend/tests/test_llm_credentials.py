"""用户自带大模型 Key 的契约（设计方案 7.8）。

覆盖两件事：请求头怎么解析与校验（只认 https 或本机 http、长度上限、空值视为没带），
以及生成层到底用了谁的 Key——请求头里的优先于账号里存的，服务端不预置 Key。
"""

from __future__ import annotations

import pytest
from fastapi import Request

from app.api import deps
from app.core.config import Settings
from app.core.errors import ApiError
from app.services.generator import (
    OpenAICompatibleGenerator,
    UnconfiguredGenerator,
    build_generator,
)

KEY = "X-LLM-Api-Key"
BASE_URL = "X-LLM-Base-Url"
MODEL = "X-LLM-Model"
# 承接表里显式登记为无库内菜谱的类别，解析必然走生成分支
UNCOVERED_DISH = "土豆肉末拌面"


def make_request(headers: dict) -> Request:
    raw = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw})


def test_no_header_means_use_the_server_configuration():
    assert deps.llm_credentials_from_request(make_request({})) is None
    assert deps.llm_credentials_from_request(make_request({KEY: "   "})) is None


def test_header_key_is_read_with_optional_base_url_and_model():
    credentials = deps.llm_credentials_from_request(
        make_request({KEY: " sk-user-key ", BASE_URL: "https://api.deepseek.com/v1/", MODEL: " deepseek-chat "})
    )

    assert credentials is not None
    assert credentials.api_key == "sk-user-key"
    assert credentials.base_url == "https://api.deepseek.com/v1", "地址末尾的斜杠归一化掉"
    assert credentials.model == "deepseek-chat"


def test_base_url_accepts_https_and_local_http_only():
    for allowed in ("https://api.deepseek.com/v1", "http://localhost:8000/v1", "http://127.0.0.1:11434/v1"):
        assert deps.llm_credentials_from_request(make_request({KEY: "k", BASE_URL: allowed})) is not None

    with pytest.raises(ApiError) as failure:
        deps.llm_credentials_from_request(make_request({KEY: "k", BASE_URL: "http://api.example.com/v1"}))
    assert failure.value.code == 40001


@pytest.mark.parametrize(
    "headers",
    [
        {KEY: "k" * 201},
        {KEY: "k", BASE_URL: "https://example.com/" + "a" * 200},
        {KEY: "k", MODEL: "m" * 65},
    ],
)
def test_oversized_credentials_are_rejected(headers):
    with pytest.raises(ApiError):
        deps.llm_credentials_from_request(make_request(headers))


def test_build_generator_prefers_the_explicit_credentials():
    settings = Settings(generator_api_key="server-key", generator_model="server-model")

    generator = build_generator(settings, api_key="user-key", model="user-model")

    assert isinstance(generator, OpenAICompatibleGenerator)
    assert generator._api_key == "user-key"
    assert generator.model == "user-model"


def test_build_generator_never_falls_back_to_the_configured_key():
    """服务端不替谁掏额度：配置里那份 Key 只给本地工具，请求链路一律不认它。"""

    settings = Settings(generator_api_key="server-key")

    assert isinstance(build_generator(settings), UnconfiguredGenerator)


def test_build_generator_without_any_key_stays_unconfigured():
    assert isinstance(build_generator(Settings(generator_api_key="")), UnconfiguredGenerator)
    assert isinstance(
        build_generator(Settings(generator_api_key=""), api_key="  "), UnconfiguredGenerator
    )


def test_bad_base_url_in_a_header_is_a_request_error_not_a_500(client):
    response = client.post(
        "/api/recipes/resolve",
        json={"dish": UNCOVERED_DISH, "index": 157},
        headers={KEY: "sk-user", BASE_URL: "http://api.example.com/v1"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == 40001


def test_resolve_uses_the_key_from_the_header(client, monkeypatch, stub_generator):
    """请求头带了 Key 就必须走它，服务端 `.env` 配没配都不影响这次调用。"""

    captured: dict = {}

    def fake_build(settings, **kwargs):
        captured.update(kwargs)
        return stub_generator

    monkeypatch.setattr(deps, "build_generator", fake_build)
    response = client.post(
        "/api/recipes/resolve",
        json={"dish": UNCOVERED_DISH, "index": 157},
        headers={KEY: "sk-user-key", MODEL: "deepseek-chat"},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "generated"
    assert captured == {"api_key": "sk-user-key", "base_url": None, "model": "deepseek-chat"}
    assert stub_generator.calls, "生成层确实被调用了"


def test_resolve_without_any_credentials_uses_the_cached_generator(client, monkeypatch, stub_generator):
    """没有凭据时不能现造生成层：那条路径要继续走缓存的那个实现。"""

    monkeypatch.setattr(deps, "_generator_for", lambda settings: stub_generator)
    response = client.post("/api/recipes/resolve", json={"dish": UNCOVERED_DISH, "index": 157})

    assert response.status_code == 200
    assert response.json()["source"] == "generated"
