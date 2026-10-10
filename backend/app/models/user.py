"""账号、会话与账号级菜谱记忆（设计方案 6.5）。

三张表各放各的东西：`users` 只存密码哈希，`user_sessions` 只存会话令牌的摘要，
`user_memories` 存用户同步上来的菜谱副本。

大模型 Key **不进这三张表**：用户自己填的 Key 留在他自己的浏览器里，请求时随
`X-LLM-Api-Key` 头带过来，服务端用完即弃——服务端不持有别人的密钥，
也就不需要密钥加密存储与轮换（见 7.8）。
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
