"""账号、会话与账号级记忆的业务逻辑（设计方案 6.5、5.6）。

口令与令牌的哈希在 `core/security.py`，这里只管规则与落库：
邮箱大小写归一化后唯一、密码长度区间、显示名兜底取邮箱前缀、
登录失败按邮箱限流、记忆整表覆盖式同步。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.crypto import SecretBox, mask_secret
from ..core.errors import bad_request, conflict, too_many_requests, unauthorized
from ..core.security import (
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)
from ..models import User, UserMemory, UserSession
from .generator import LlmCredentials

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 255
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_DISPLAY_NAME_LENGTH = 32
FALLBACK_DISPLAY_NAME = "厨友"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_email(raw: Optional[str]) -> str:
    return (raw or "").strip().lower()


def validate_email(email: str) -> None:
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.match(email):
        raise bad_request("邮箱格式不正确", {"field": "email"})


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH or len(password) > MAX_PASSWORD_LENGTH:
        raise bad_request(
            f"密码长度需要 {MIN_PASSWORD_LENGTH} 到 {MAX_PASSWORD_LENGTH} 位",
            {"field": "password"},
        )


def fallback_display_name(email: str) -> str:
    """没填显示名就用邮箱前缀：比让用户先想一个名字省事。"""

    prefix = email.split("@")[0].strip()
    return prefix[:MAX_DISPLAY_NAME_LENGTH] or FALLBACK_DISPLAY_NAME


def register(
    session: Session,
    settings: Settings,
    *,
    email: str,
    password: str,
    display_name: Optional[str] = None,
) -> User:
    """注册账号。邮箱已存在时报冲突（不告诉调用方密码对不对，避免当探测接口用）。"""

    normalized = normalize_email(email)
    validate_email(normalized)
    validate_password(password)
    existing = session.scalar(select(User).where(User.email == normalized))
    if existing is not None:
        raise conflict("该邮箱已注册，直接登录即可")
    name = (display_name or "").strip()[:MAX_DISPLAY_NAME_LENGTH] or fallback_display_name(
        normalized
    )
    user = User(email=normalized, password_hash=hash_password(password), display_name=name)
    session.add(user)
    session.flush()
    logger.info("新账号注册：id=%s", user.id)
    return user


def authenticate(session: Session, *, email: str, password: str) -> User:
    """校验口令。账号不存在与口令错误返回同一句话，不泄露邮箱是否注册过。"""

    normalized = normalize_email(email)
    user = session.scalar(select(User).where(User.email == normalized))
    if user is None or not verify_password(password, user.password_hash):
        raise unauthorized("邮箱或密码不正确")
    user.last_login_at = _utcnow()
    session.flush()
    return user


def start_session(session: Session, settings: Settings, user: User) -> str:
    """开一个会话，返回给客户端的明文令牌（库里只留下它的摘要）。"""

    token = new_session_token()
    now = _utcnow()
    session.add(
        UserSession(
            token_hash=hash_session_token(token),
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(days=settings.session_ttl_days),
        )
    )
    session.flush()
    return token


def end_session(session: Session, token: Optional[str]) -> None:
    """退出登录：删掉这一条会话，其他设备上的会话不受影响。"""

    if not token:
        return
    session.execute(
        delete(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    session.flush()


def user_for_token(session: Session, token: Optional[str]) -> Optional[User]:
    """按会话令牌取账号。令牌不认识、已过期、账号已删都返回 None。"""

    if not token:
        return None
    row = session.scalar(
        select(UserSession).where(UserSession.token_hash == hash_session_token(token))
    )
    if row is None:
        return None
    if row.expires_at <= _utcnow():
        # 过期会话顺手清掉：不需要定时任务，用到的时候自然回收
        session.delete(row)
        session.flush()
        return None
    user = session.get(User, row.user_id)
    if user is None:
        session.delete(row)
        session.flush()
        return None
    return user


def purge_expired_sessions(session: Session, *, now: Optional[datetime] = None) -> int:
    """清掉所有过期会话，返回删除条数（启动时跑一次即可）。"""

    moment = now or _utcnow()
    rows = session.scalars(select(UserSession).where(UserSession.expires_at <= moment)).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


class LoginThrottle:
    """登录失败限流：进程内的滑动窗口计数。

    只挡「同一个邮箱被反复试密码」这一种情况，重启即清零、也不跨进程共享——
    单实例部署够用，多实例要换成共享存储（见 11 章风险表）。
    """

    def __init__(
        self,
        max_failures: int = 5,
        lock_seconds: float = 300.0,
        clock=time.monotonic,
    ) -> None:
        self.max_failures = max(1, int(max_failures))
        self.lock_seconds = float(lock_seconds)
        self._clock = clock
        self._failures: Dict[str, List[float]] = {}

    def _recent(self, key: str, now: float) -> List[float]:
        stamps = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.lock_seconds]
        if stamps:
            self._failures[key] = stamps
        else:
            self._failures.pop(key, None)
        return stamps

    def retry_after(self, key: str) -> float:
        """还能不能再试：0 表示可以，正数表示还要等多少秒。"""

        now = self._clock()
        stamps = self._recent(key, now)
        if len(stamps) < self.max_failures:
            return 0.0
        return max(0.0, self.lock_seconds - (now - min(stamps)))

    def record_failure(self, key: str) -> None:
        now = self._clock()
        self._failures.setdefault(key, []).append(now)
        self._recent(key, now)

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)

    def reset(self) -> None:
        self._failures.clear()


@dataclass(frozen=True)
class MemoryRecord:
    """一条记忆的领域对象。`at` 沿用前端的 epoch 毫秒。"""

    dish: str
    name: str
    source: str
    recipe: dict
    at: int
    favorite: bool


def order_records(records: Sequence[MemoryRecord], limit: int) -> List[MemoryRecord]:
    """收藏在前，其余按时间倒序；与前端 `orderMemory` 保持同一条规则。"""

    ordered = sorted(records, key=lambda item: (not item.favorite, -item.at))
    return ordered[:limit] if limit > 0 else ordered


def _to_record(row: UserMemory) -> MemoryRecord:
    try:
        recipe = json.loads(row.recipe_json)
    except ValueError:  # 落库内容被改坏时不当成致命错误，读成空菜谱
        logger.warning("记忆 %s 的菜谱 JSON 解析失败，按空菜谱读出", row.id)
        recipe = {}
    return MemoryRecord(
        dish=row.dish,
        name=row.name,
        source=row.source,
        recipe=recipe if isinstance(recipe, dict) else {},
        at=int(row.remembered_at_ms),
        favorite=bool(row.favorite),
    )


def list_memories(session: Session, user: User, limit: int) -> List[MemoryRecord]:
    rows = session.scalars(select(UserMemory).where(UserMemory.user_id == user.id)).all()
    return order_records([_to_record(row) for row in rows], limit)


def replace_memories(
    session: Session,
    user: User,
    records: Sequence[MemoryRecord],
    limit: int,
) -> List[MemoryRecord]:
    """整表覆盖：先按菜名去重（同名留最后提交的那条），再排序截断后落库。

    合并策略在前端（它同时握着本机与服务端两份数据），服务端只保证
    「收下来的这批就是这个账号的全部记忆」，避免两边各留一半。
    """

    deduped: Dict[str, MemoryRecord] = {}
    for record in records:
        deduped[record.dish] = record
    kept = order_records(list(deduped.values()), limit)

    session.execute(delete(UserMemory).where(UserMemory.user_id == user.id))
    session.flush()
    now = _utcnow()
    for record in kept:
        session.add(
            UserMemory(
                user_id=user.id,
                dish=record.dish,
                name=record.name,
                source=record.source,
                recipe_json=json.dumps(record.recipe, ensure_ascii=False),
                favorite=record.favorite,
                remembered_at_ms=record.at,
                updated_at=now,
            )
        )
    session.flush()
    return kept


# 本机地址是显式例外：本地起一个兼容 OpenAI 协议的网关（llama.cpp / vLLM）时用得上
LOCAL_BASE_URL_PREFIXES = ("http://localhost", "http://127.0.0.1", "http://[::1]")
MAX_BASE_URL_LENGTH = 200
MAX_MODEL_LENGTH = 64


@dataclass(frozen=True)
class LlmKeyView:
    """对外的账号级 Key 视图：只有掩码，没有明文。"""

    configured: bool
    masked: str
    base_url: Optional[str]
    model: Optional[str]
    updated_at: Optional[datetime]


def check_base_url(base_url: Optional[str]) -> Optional[str]:
    """只接受 https，或本机 http：明文把 Key 发到外网是明确要挡掉的情况。"""

    value = (base_url or "").strip().rstrip("/")
    if not value:
        return None
    if len(value) > MAX_BASE_URL_LENGTH:
        raise bad_request("模型地址过长")
    if not (value.startswith("https://") or value.startswith(LOCAL_BASE_URL_PREFIXES)):
        raise bad_request("模型地址只接受 https:// 或本机的 http://localhost / http://127.0.0.1")
    return value


def normalize_model(model: Optional[str]) -> Optional[str]:
    """模型名留空表示用服务端配的默认值；超长直接挡回，不留给调用方去撞。"""

    value = (model or "").strip()
    if not value:
        return None
    if len(value) > MAX_MODEL_LENGTH:
        raise bad_request("模型名过长")
    return value


def store_llm_key(
    session: Session,
    user: User,
    *,
    api_key: str,
    base_url: Optional[str],
    model: Optional[str],
    box: SecretBox,
) -> LlmKeyView:
    """把 Key 加密后记到账号上。同一账号只有一份，再存就是覆盖。"""

    key = (api_key or "").strip()
    if not key:
        raise bad_request("请填模型 Key")
    user.llm_api_key_enc = box.encrypt(key)
    user.llm_base_url = check_base_url(base_url)
    user.llm_model = normalize_model(model)
    user.llm_key_updated_at = _utcnow()
    session.flush()
    return LlmKeyView(True, mask_secret(key), user.llm_base_url, user.llm_model, user.llm_key_updated_at)


def clear_llm_key(session: Session, user: User) -> None:
    user.llm_api_key_enc = None
    user.llm_base_url = None
    user.llm_model = None
    user.llm_key_updated_at = None
    session.flush()


def llm_key_view(user: User, box: SecretBox) -> LlmKeyView:
    """账号里那份 Key 的现状。解不出来就当没配：换过主密钥、库被动过，都不该 500。"""

    if not user.llm_api_key_enc:
        return LlmKeyView(False, "", None, None, None)
    plain = box.decrypt(user.llm_api_key_enc)
    if plain is None:
        logger.warning("账号 %s 的模型 Key 解不开（主密钥换过或数据被改过）", user.id)
        return LlmKeyView(False, "", None, None, None)
    return LlmKeyView(
        True, mask_secret(plain), user.llm_base_url, user.llm_model, user.llm_key_updated_at
    )


def llm_credentials(user: Optional[User], box: SecretBox) -> Optional[LlmCredentials]:
    """账号里存着的凭据；没登录、没存过、解不开都返回 None。"""

    if user is None or not user.llm_api_key_enc:
        return None
    plain = box.decrypt(user.llm_api_key_enc)
    if plain is None:
        logger.warning("账号 %s 的模型 Key 解不开，本次不提供生成凭据", user.id)
        return None
    return LlmCredentials(api_key=plain, base_url=user.llm_base_url, model=user.llm_model)
