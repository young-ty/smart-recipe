"""账号、会话与账号级菜谱记忆（设计方案 6.5）。

三张表各放各的东西：`users` 只存密码哈希，`user_sessions` 只存会话令牌的摘要，
`user_memories` 存用户同步上来的菜谱副本。

大模型 Key 有两个去处，由用户自己选：不登录时留在他的浏览器里，请求时随
`X-LLM-Api-Key` 头带过来、用完即弃；登录后可以存进 `users`，那是加密后才落库的
（见 `core/crypto.py`），接口只回掩码，明文只在调用生成层的那一刻出现在内存里。
两种情况服务端都不预置自己的 Key，也就不替任何人承担额度（见 7.8）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 账号级模型 Key（FR-16）：值由 core/crypto.py 加密后落库，用的时候才解。
    # 明文不进库、不进日志、不回给前端：接口只回掩码（见 7.8）。
    llm_api_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_base_url: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    llm_model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    llm_key_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<User {self.id} {self.email}>"


class UserSession(Base):
    """登录会话。令牌本身只发给客户端，库里存 sha256 摘要。"""

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    # 会话过期时间；读取时不回写，避免每个请求都产生一次 UPDATE
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class UserMemory(Base):
    """账号级菜谱记忆（FR-15 的服务端副本）。

    键是（账号, 菜名）：同一道菜只留一条，整表覆盖式同步（见 5.6.4）。
    `remembered_at_ms` 沿用前端的 epoch 毫秒，同步时不必做时区换算。
    """

    __tablename__ = "user_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "dish", name="uq_user_memories_user_dish"),
        Index("ix_user_memories_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    dish: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    recipe_json: Mapped[str] = mapped_column(Text, nullable=False)
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remembered_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
