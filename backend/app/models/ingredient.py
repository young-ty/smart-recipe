"""食材字典表（设计方案 6.2）。"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    alias: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Ingredient {self.id} {self.name}>"