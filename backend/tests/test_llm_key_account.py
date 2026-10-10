# -*- coding: utf-8 -*-
"""账号级模型 Key 的接口契约（设计方案 5.6.5、7.8）。

要看清三件事：明文只进不出（库里是密文、接口只回掩码），未登录一律拿不到，
以及存进账号的 Key 真的会被生成链路用上、且让位给请求头里那份临时的。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.models import User

REGISTER = {"email": "cook@example.com", "password": "abcd1234"}
# 占位值：形如 sk-<32 位十六进制> 的字符串会被仓库的密钥扫描当成真 Key 拦下
KEY = "sk-local-demo-key-abcdef"
# 承接表里显式登记为无库内菜谱的类别，解析必然走生成分支
UNCOVERED_DISH = "土豆肉末拌面"
HEADER = "X-LLM-Api-Key"


def login(client: TestClient, **overrides) -> dict:
    response = client.post("/api/auth/register", json={**REGISTER, **overrides})
    assert response.status_code == 201
    return response.json()["user"]


def sign_in(client: TestClient) -> dict:
    """已有账号时走登录：注册过的邮箱再注册会被 409 挡住。"""

    response = client.post("/api/auth/login", json=REGISTER)
    assert response.status_code == 200
    return response.json()["user"]


def store(client: TestClient, **payload):
    return client.put("/api/llm/key", json={"api_key": KEY, **payload})


# ------------------------------------------------------------------ 鉴权

@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/api/llm/key", {}),
        ("put", "/api/llm/key", {"json": {"api_key": KEY}}),
        ("delete", "/api/llm/key", {}),
    ],
)
def test_key_endpoints_need_a_session(client: TestClient, method: str, path: str, kwargs: dict):
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == 40101


# --------------------------------------------------------- 存取与明文边界

def test_account_has_no_key_until_one_is_stored(client: TestClient):
    login(client)

    body = client.get("/api/llm/key").json()

    assert body == {
        "configured": False,
        "masked": "",
        "base_url": None,
        "model": None,
        "updated_at": None,
    }


def test_stored_key_comes_back_masked_only(client: TestClient):
    login(client)

    stored = store(client, base_url="https://api.deepseek.com/v1/", model="deepseek-flash")

    assert stored.status_code == 200
    body = stored.json()
    assert body["configured"] is True
    assert body["masked"] == KEY[:4] + "••••" + KEY[-4:]
    assert body["base_url"] == "https://api.deepseek.com/v1", "地址末尾的斜杠要归一化掉"
    assert body["model"] == "deepseek-flash"
    assert body["updated_at"] is not None
    assert KEY not in stored.text, "接口任何时候都不回明文"


def test_the_database_never_holds_the_plaintext(client: TestClient, session):
    user_email = login(client)["email"]
    store(client)

    row = session.query(User).filter(User.email == user_email).one()

    assert row.llm_api_key_enc
    assert KEY not in row.llm_api_key_enc
    assert deps._secret_box_for(client.app.state.settings).decrypt(row.llm_api_key_enc) == KEY


def test_storing_again_replaces_the_previous_key(client: TestClient):
    login(client)
    store(client)

    again = client.put("/api/llm/key", json={"api_key": "sk-second-key-0123456789abcdef"})

    assert again.json()["masked"] == "sk-s••••cdef"
    assert again.json()["base_url"] is None, "没传的字段按清空处理，不保留上一把的地址"


def test_deleting_forgets_the_key(client: TestClient, session):
    login(client)
    store(client)

    response = client.delete("/api/llm/key")

    assert response.status_code == 204
    assert client.get("/api/llm/key").json()["configured"] is False
    assert session.query(User).one().llm_api_key_enc is None


# ------------------------------------------------------------------ 校验

@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": ""},
        {"api_key": KEY, "base_url": "http://api.example.com/v1"},
        {"api_key": KEY, "base_url": "https://example.com/" + "a" * 200},
        {"api_key": KEY, "model": "m" * 65},
        {"api_key": "k" * 201},
    ],
)
def test_bad_payloads_are_rejected(client: TestClient, payload: dict):
    login(client)

    response = client.put("/api/llm/key", json=payload)

    assert response.status_code in (400, 422)
    assert client.get("/api/llm/key").json()["configured"] is False


@pytest.mark.parametrize("base_url", ["http://localhost:8000/v1", "http://127.0.0.1:11434/v1"])
def test_local_gateways_are_allowed(client: TestClient, base_url: str):
    login(client)

    assert store(client, base_url=base_url).status_code == 200


def test_an_unreadable_record_is_reported_as_not_configured(client: TestClient, session, settings):
    """主密钥换过、或数据被改坏时，接口说不出来就当没配，不该 500。"""

    login(client)
    store(client)
    row = session.query(User).one()
    row.llm_api_key_enc = "v1$c2FsdA==$c2FsdA==$AAAA$AAAA"
    session.commit()

    assert client.get("/api/llm/key").json()["configured"] is False


# --------------------------------------------------------- 生成链路怎么用它

def test_resolve_uses_the_key_stored_on_the_account(
    client: TestClient, monkeypatch, stub_generator
):
    captured: dict = {}

    def fake_build(settings, **kwargs):
        captured.update(kwargs)
        return stub_generator

    monkeypatch.setattr(deps, "build_generator", fake_build)
    login(client)
    store(client, model="deepseek-flash")

    response = client.post("/api/recipes/resolve", json={"dish": UNCOVERED_DISH, "index": 157})

    assert response.status_code == 200
    assert response.json()["source"] == "generated"
    assert captured == {"api_key": KEY, "base_url": None, "model": "deepseek-flash"}


def test_the_request_header_beats_the_stored_key(client: TestClient, monkeypatch, stub_generator):
    captured: dict = {}

    def fake_build(settings, **kwargs):
        captured.update(kwargs)
        return stub_generator

    monkeypatch.setattr(deps, "build_generator", fake_build)
    login(client)
    store(client)

    client.post(
        "/api/recipes/resolve",
        json={"dish": UNCOVERED_DISH, "index": 157},
        headers={HEADER: "sk-temporary-key-0123456789abcdef"},
    )

    assert captured["api_key"] == "sk-temporary-key-0123456789abcdef"


def test_logging_out_leaves_the_stored_key_on_the_account(client: TestClient):
    """Key 存在账号上，与会话无关：退出再登录，它还在。"""

    login(client)
    store(client)
    client.post("/api/auth/logout")

    sign_in(client)

    assert client.get("/api/llm/key").json()["masked"] == KEY[:4] + "••••" + KEY[-4:]


def test_probe_reports_the_stored_key(client: TestClient, monkeypatch, stub_generator):
    monkeypatch.setattr(deps, "_generator_for", lambda settings: stub_generator)
    monkeypatch.setattr(
        deps, "build_generator", lambda settings, **kwargs: stub_generator
    )
    login(client)
    store(client)

    body = client.post("/api/llm/probe").json()

    assert body["ok"] is True
    assert body["configured"] is True
