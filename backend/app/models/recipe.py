"""菜谱主表、菜谱-食材关联、菜谱步骤（设计方案 6.2）。"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .ingredient import Ingredient


class Recipe(Base):
    __tablename__ = "recipes"
    __table_args__ = (Index("ix_recipes_difficulty_cook_time", "difficulty", "cook_time_minutes"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    cook_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(8), nullable=False)
    # 生成后入库的菜谱没有评分（模型编的分数没有意义），所以这里允许为空
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    servings: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # seed = 人工整理并入库的种子数据；generated = 大模型生成后自动入库（设计方案 7.5）
    # server_default 与迁移 DDL 的 DEFAULT 'seed' 对齐，裸 SQL 插入也不能漏掉来源标记
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="seed", server_default="seed"
    )

    # lazy="selectin" 一次性预加载关联，消除推荐路径上的 N+1 查询（设计方案 7.6）
    ingredients: Mapped[List["RecipeIngredient"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    steps: Mapped[List["RecipeStep"]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeStep.step_no",
        lazy="selectin",
    )

    @property
    def required_ingredients(self) -> List[Ingredient]:
        return [link.ingredient for link in self.ingredients if link.is_required]

    @property
    def optional_ingredients(self) -> List[Ingredient]:
        return [link.ingredient for link in self.ingredients if not link.is_required]

    @property
    def step_contents(self) -> List[str]:
        return [step.content for step in self.steps]

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Recipe {self.id} {self.name}>"


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (Index("ix_recipe_ingredients_ingredient_id", "ingredient_id"),)

    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), primary_key=True)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    amount: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    recipe: Mapped["Recipe"] = relationship(back_populates="ingredients")
    ingredient: Mapped["Ingredient"] = relationship(lazy="joined")


class RecipeStep(Base):
    __tablename__ = "recipe_steps"

    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    step_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(String(255), nullable=False)

    recipe: Mapped["Recipe"] = relationship(back_populates="steps")