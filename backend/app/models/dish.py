"""成品菜方向的 ORM 模型：类别-菜谱承接表与生成结果缓存（设计方案 6.2）。

`dish_recipe_map` 只维护「模型类别下标 → 菜谱」的承接关系，**不存类别名称**：
名称以训练产出的类别清单为准（顺序必须与训练一致）。`recipe_id` 为 NULL 的行
即 7.4 节生成分支的输入集合——缺口显式记录，不静默丢弃。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DishRecipeMap(Base):
    __tablename__ = "dish_recipe_map"
    __table_args__ = (Index("ix_dish_recipe_map_recipe_id", "recipe_id"),)

    class_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True
    )
    alias: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<DishRecipeMap {self.class_index} -> {self.recipe_id}>"


class GeneratedRecipe(Base):
    """大模型生成结果的缓存（FR-13）。键为 sha1(菜名 + 提示词版本)。

    同一道菜重复查询稳定命中同一行，不因大模型的随机性抖动。
    """

    __tablename__ = "generated_recipes"
    __table_args__ = (Index("ix_generated_recipes_dish_prompt", "dish", "prompt_version"),)

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    dish: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<GeneratedRecipe {self.dish}>"
