"""账号、会话与账号级记忆的契约测试（设计方案 5.6、6.5）。

覆盖三件事：口令与令牌怎么存（至少不含明文）、会话的生命周期（登录 / 过期 / 退出 /
限流），以及记忆整表同步的排序、去重、截断与账号隔离。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import routes_accounts
from app.core.security import (
    PBKDF2_ITERATIONS,
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)
from app.models import User, UserSession
from app.services.accounts import LoginThrottle

REGISTER = {"email": "cook@example.com", "password": "abcd1234"}
COOKIE = "recipe_session"


@pytest.fixture(autouse=True)
def _reset_throttle():
    """限流是进程内计数：用例之间必须清零，否则先跑的失败会传染后面。"""

    routes_accounts._throttle.reset()
    yield
    routes_accounts._throttle.reset()


def register(client: TestClient, **overrides):
    return client.post("/api/auth/register", json={**REGISTER, **overrides})


def entry(dish: str, at: int, favorite: bool = False, source: str = "library") -> dict:
    return {
        "dish": dish,
        "name": dish,
        "source": source,
        "recipe": {"name": dish},
        "at": at,
        "favorite": favorite,
    }


# --------------------------------------------------------------- 口令与令牌

def test_password_hash_round_trip_and_failure_modes():
    stored = hash_password("abcd1234")

    assert stored.startswith("pbkdf2_sha256$")
    assert str(PBKDF2_ITERATIONS) in stored, "迭代次数要写进字符串，方便以后调高"
    assert verify_password("abcd1234", stored) is True
    assert verify_password("abcd1235", stored) is False


@pytest.mark.parametrize("broken", ["", "明文口令", "md5$1$c2FsdA==$AAAA", "pbkdf2_sha256$0$c2FsdA==$AAAA"])
def test_verify_password_rejects_broken_records(broken):
    assert verify_password("abcd1234", broken) is False


def test_same_password_gets_a_fresh_salt_each_time():
    assert hash_password("abcd1234") != hash_password("abcd1234")


def test_session_token_is_stored_as_a_digest():
    first, second = new_session_token(), new_session_token()

    assert first != second
    assert len(first) >= 40, "令牌要有足够熵"
    digest = hash_session_token(first)
    assert len(digest) == 64
    assert first not in digest


# ------------------------------------------------------------------- 注册

def test_register_normalizes_email_and_fills_display_name(client):
    response = register(client, email="  Cook@Example.COM ")

    assert response.status_code == 201
    user = response.json()["user"]
    assert user["email"] == "cook@example.com"
    assert user["display_name"] == "cook", "没填显示名就用邮箱前缀"
    assert "password" not in response.text


def test_register_keeps_custom_display_name(client):
    assert register(client, display_name="小杨").json()["user"]["display_name"] == "小杨"


def test_register_rejects_duplicate_email(client):
    assert register(client).status_code == 201

    again = register(client, email="COOK@example.com")

    assert again.status_code == 409
    assert again.json()["error"]["code"] == 40901


def test_register_rejects_short_password_and_malformed_email(client):
    assert register(client, password="short").status_code == 400
    assert register(client, email="not-an-email").status_code == 400


def test_password_never_lands_in_the_database_in_clear(client, session):
    register(client)

    user = session.scalar(select(User))
    assert user is not None
    assert user.password_hash.startswith("pbkdf2_sha256$")
    assert "abcd1234" not in user.password_hash


def test_session_row_keeps_only_the_digest(client, session):
    register(client)
    token = client.cookies[COOKIE]

    rows = session.scalars(select(UserSession)).all()
    assert len(rows) == 1
    assert rows[0].token_hash == hash_session_token(token)
    assert token not in rows[0].token_hash


# --------------------------------------------------------------- 登录与会话

def test_login_issues_httponly_cookie_and_me_returns_the_user(client):
    register(client)
    client.post("/api/auth/logout")

    response = client.post(
        "/api/auth/login", json={"email": "COOK@example.com", "password": "abcd1234"}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "cook@example.com"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie, "令牌不能被前端脚本读到"
    assert "samesite=lax" in cookie
    assert client.get("/api/auth/me").json()["user"]["id"] == response.json()["user"]["id"]


def test_login_failure_does_not_reveal_whether_the_account_exists(client):
    register(client)

    wrong_password = client.post(
        "/api/auth/login", json={"email": "cook@example.com", "password": "wrong12345"}
    )
    unknown_account = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "abcd1234"}
    )

    assert wrong_password.status_code == unknown_account.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_account.json()["error"]["message"]


def test_anonymous_me_is_null_instead_of_an_error(client):
    """未登录不是错误：前端靠这个响应区分「没登录」和「后端没这套接口」。"""

    assert client.get("/api/auth/me").json() == {"user": None}


def test_logout_kills_only_the_current_session(client, app):
    register(client)
    phone = TestClient(app)
    phone.post("/api/auth/login", json=REGISTER)

    assert client.post("/api/auth/logout").status_code == 204

    assert client.get("/api/auth/me").json() == {"user": None}
    assert phone.get("/api/auth/me").json()["user"] is not None


def test_expired_session_is_rejected_and_recycled(client, session):
    register(client)
    digest = hash_session_token(client.cookies[COOKIE])
    row = session.scalar(select(UserSession).where(UserSession.token_hash == digest))
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    session.commit()

    assert client.get("/api/auth/me").json() == {"user": None}
    assert session.scalar(select(UserSession).where(UserSession.token_hash == digest)) is None


def test_forged_cookie_is_ignored(client):
    client.cookies.set(COOKIE, new_session_token())

    assert client.get("/api/auth/me").json() == {"user": None}


def test_login_throttle_locks_repeated_failures(client):
    register(client)
    for _ in range(5):
        attempt = client.post(
            "/api/auth/login", json={"email": "cook@example.com", "password": "wrong12345"}
        )
        assert attempt.status_code == 401

    blocked = client.post("/api/auth/login", json=REGISTER)

    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == 42901


def test_login_throttle_is_a_sliding_window():
    clock = [0.0]
    throttle = LoginThrottle(max_failures=2, lock_seconds=60, clock=lambda: clock[0])

    assert throttle.retry_after("cook@example.com") == 0.0
    throttle.record_failure("cook@example.com")
    throttle.record_failure("cook@example.com")
    assert throttle.retry_after("cook@example.com") > 0
    assert throttle.retry_after("other@example.com") == 0.0, "限流只锁出问题的那个邮箱"

    clock[0] = 61.0
    assert throttle.retry_after("cook@example.com") == 0.0, "窗口过去后自己解开"
    throttle.clear("cook@example.com")
    assert throttle.retry_after("cook@example.com") == 0.0


# ------------------------------------------------------------- 账号级记忆

def test_memories_require_login(client):
    assert client.get("/api/memories").status_code == 401
    assert client.put("/api/memories", json={"entries": []}).status_code == 401


def test_memories_round_trip_keeps_favourites_first(client):
    register(client)
    payload = {
        "entries": [entry("红烧肉", 10), entry("麻婆豆腐", 20), entry("番茄蛋花汤", 5, favorite=True)]
    }

    assert client.put("/api/memories", json=payload).status_code == 200

    entries = client.get("/api/memories").json()["entries"]
    assert [item["dish"] for item in entries] == ["番茄蛋花汤", "麻婆豆腐", "红烧肉"]
    assert entries[0]["favorite"] is True
    assert entries[0]["recipe"] == {"name": "番茄蛋花汤"}


def test_memories_sync_replaces_the_whole_table(client):
    register(client)
    client.put("/api/memories", json={"entries": [entry("红烧肉", 10)]})

    body = client.put("/api/memories", json={"entries": [entry("麻婆豆腐", 20)]}).json()

    assert [item["dish"] for item in body["entries"]] == ["麻婆豆腐"]
    assert [item["dish"] for item in client.get("/api/memories").json()["entries"]] == ["麻婆豆腐"]


def test_memories_dedupe_by_dish_keeping_the_last_one(client):
    register(client)
    payload = {"entries": [entry("红烧肉", 10), entry("红烧肉", 30, favorite=True)]}

    entries = client.put("/api/memories", json=payload).json()["entries"]

    assert len(entries) == 1
    assert entries[0]["at"] == 30
    assert entries[0]["favorite"] is True


def test_memories_are_truncated_to_the_configured_limit(client, settings):
    register(client)
    limit = settings.memory_sync_limit
    payload = {"entries": [entry(f"菜{index}", index) for index in range(limit + 3)]}

    entries = client.put("/api/memories", json=payload).json()["entries"]

    assert len(entries) == limit
    assert entries[0]["dish"] == f"菜{limit + 2}", "截断后留下的是最近的那一批"


def test_memories_are_isolated_between_accounts(client, app):
    register(client)
    client.put("/api/memories", json={"entries": [entry("红烧肉", 10)]})
    other = TestClient(app)
    other.post("/api/auth/register", json={"email": "other@example.com", "password": "abcd1234"})

    assert other.get("/api/memories").json()["entries"] == []
    assert len(client.get("/api/memories").json()["entries"]) == 1


def test_memory_entry_validation(client):
    register(client)

    bad_source = {"entries": [{**entry("红烧肉", 10), "source": "unknown"}]}
    assert client.put("/api/memories", json=bad_source).status_code == 400

    over_limit = {"entries": [entry(f"菜{index}", index) for index in range(201)]}
    assert client.put("/api/memories", json=over_limit).status_code == 400

    empty_dish = {"entries": [{**entry("红烧肉", 10), "dish": ""}]}
    assert client.put("/api/memories", json=empty_dish).status_code == 400
