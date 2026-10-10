"""种子数据加载与入库（设计方案 6.4）。

数据文件位于 ``app/data``，与代码同版本管理；入库幂等，可重复调用。
三部分都按**条目**判定是否需要写入，而不是「表里有数据就跳过」：补库时改的正是这些
文件，按存量判断会让新增内容永远进不了已有数据库——承接表引用新菜谱时还会直接撞外键。
已有条目只在内容真的变了（签名不同）时才重建关联行，重复灌入仍是无写入。
v2.0 起多了一张「类别 → 菜谱」承接表，它与菜谱库分开判定是否已入库，
避免老库（已有菜谱但还没有承接表）升级后漏掉承接关系。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings, get_settings
from ..models import DishRecipeMap, Ingredient, Recipe, RecipeIngredient, RecipeStep

SEED_INGREDIENTS_FILE = "seed_ingredients.json"
SEED_RECIPES_FILE = "seed_recipes.json"
SEED_DISH_MAP_FILE = "seed_dish_map.json"

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> object:
    if not path.exists():
        raise FileNotFoundError(f"种子数据文件缺失：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_seed_ingredients(settings: Optional[Settings] = None) -> List[dict]:
    settings = settings or get_settings()
    return list(_read_json(settings.data_dir / SEED_INGREDIENTS_FILE))


def load_seed_recipes(settings: Optional[Settings] = None) -> List[dict]:
    settings = settings or get_settings()
    return list(_read_json(settings.data_dir / SEED_RECIPES_FILE))


def load_seed_dish_map(settings: Optional[Settings] = None) -> dict:
    """类别下标 → 菜谱的承接关系。recipe_id 为 null 的行即生成分支的输入集合。"""

    settings = settings or get_settings()
    return dict(_read_json(settings.dish_map_path))


def _fill_recipe(recipe: Recipe, item: dict) -> None:
    """把主辅料与有序步骤写进菜谱；先清空，delete-orphan 会顺带删掉旧行。"""

    recipe.ingredients.clear()
    recipe.steps.clear()
    for entry in item.get("required", []):
        recipe.ingredients.append(
            RecipeIngredient(
                ingredient_id=entry["id"],
                is_required=True,
                amount=entry.get("amount"),
            )
        )
    for entry in item.get("optional", []):
        recipe.ingredients.append(
            RecipeIngredient(
                ingredient_id=entry["id"],
                is_required=False,
                amount=entry.get("amount"),
            )
        )
    for index, content in enumerate(item["steps"], start=1):
        recipe.steps.append(RecipeStep(step_no=index, content=content))


def _build_recipe(item: dict) -> Recipe:
    """按种子条目建一条完整菜谱。"""

    recipe = Recipe(
        id=item["id"],
        name=item["name"],
        cook_time_minutes=item["cook_time_minutes"],
        difficulty=item["difficulty"],
        rating=item["rating"],
        servings=item.get("servings", 2),
        description=item.get("description"),
        image_url=item.get("image_url"),
        origin="seed",
    )
    _fill_recipe(recipe, item)
    return recipe


def _recipe_signature(item: dict) -> tuple:
    """种子条目 → 可比较的签名，用来判断这道菜是否需要重建。

    主辅料先排序再比对：表里没有声明顺序，不排序会误判成「每天都在变」。
    """

    lines = [
        (entry["id"], True, entry.get("amount")) for entry in item.get("required", [])
    ] + [
        (entry["id"], False, entry.get("amount")) for entry in item.get("optional", [])
    ]
    return (
        item["name"],
        item["cook_time_minutes"],
        item["difficulty"],
        item["rating"],
        item.get("servings", 2),
        item.get("description"),
        item.get("image_url"),
        "seed",
        tuple(sorted(lines)),
        tuple(item["steps"]),
    )


def _stored_signature(recipe: Recipe) -> tuple:
    """库内那条菜谱的签名，与 _recipe_signature 同口径。"""

    lines = [
        (link.ingredient_id, link.is_required, link.amount) for link in recipe.ingredients
    ]
    return (
        recipe.name,
        recipe.cook_time_minutes,
        recipe.difficulty,
        recipe.rating,
        recipe.servings,
        recipe.description,
        recipe.image_url,
        recipe.origin,
        tuple(sorted(lines)),
        tuple(step.content for step in recipe.steps),
    )


def _apply_recipe(recipe: Recipe, item: dict) -> None:
    """按种子条目改写已有菜谱（标量字段 + 主辅料 + 步骤）。"""

    recipe.name = item["name"]
    recipe.cook_time_minutes = item["cook_time_minutes"]
    recipe.difficulty = item["difficulty"]
    recipe.rating = item["rating"]
    recipe.servings = item.get("servings", 2)
    recipe.description = item.get("description")
    recipe.image_url = item.get("image_url")
    recipe.origin = "seed"
    _fill_recipe(recipe, item)


def _seed_ingredients(session: Session, settings: Settings) -> bool:
    """食材字典逐条 upsert：补库常常要加新食材，老库也得能补进去。"""

    existing = {row.id: row for row in session.scalars(select(Ingredient))}
    changed = False
    for item in load_seed_ingredients(settings):
        values = (item["name"], item["category"], item.get("alias"))
        row = existing.get(item["id"])
        if row is None:
            session.add(
                Ingredient(
                    id=item["id"], name=values[0], category=values[1], alias=values[2]
                )
            )
            changed = True
        elif (row.name, row.category, row.alias) != values:
            row.name, row.category, row.alias = values
            changed = True
    session.flush()
    return changed


def _drop_superseded(session: Session, clash: Recipe, incoming_id: int) -> List[DishRecipeMap]:
    """清掉与种子同名的那条旧菜谱，给种子内容腾出菜名。

    菜名上有唯一约束，撞名的只可能是运行期生成后落库的菜（id ≥ 10000，种子号段是
    101 起）。同一道菜留两条还会让库内检索只命中其中一条，所以删旧留新。

    返回认领过它的类别行，交给调用方在插入新菜谱之后改指向：删除与插入必须在
    两次 flush 里完成——同一张表里 SQLAlchemy 会把插入排在删除之前，挤在一次
    flush 里会撞 name 的唯一约束；而先改指向又会撞上「菜谱还不存在」的外键。
    """

    claimed = list(
        session.scalars(select(DishRecipeMap).where(DishRecipeMap.recipe_id == clash.id))
    )
    logger.warning(
        "菜名「%s」库里已有一条（id=%s，origin=%s），由种子内容（id=%s）接管",
        clash.name,
        clash.id,
        clash.origin,
        incoming_id,
    )
    session.delete(clash)
    session.flush()
    return claimed


def _seed_recipes(session: Session, settings: Settings, force: bool) -> tuple:
    """菜谱逐条 upsert，返回 (库内菜谱数, 本次是否有变化)。

    与承接表同理，**不能**按「已经有菜谱了就跳过」判断幂等：补库时改的正是这个文件，
    按存量判断会让新菜谱永远进不了已有数据库（承接表引用它时还会直接撞外键）。
    已有菜谱只在内容真的变了（签名不同）时重建主辅料与步骤，重复灌入仍是无写入。
    种子里没有的菜谱不动——可能是本机手工加的；force 才整表重建。
    """

    if force:
        session.query(RecipeStep).delete()
        session.query(RecipeIngredient).delete()
        session.query(DishRecipeMap).delete()
        session.query(Recipe).delete()
        session.query(Ingredient).delete()
        session.flush()

    changed = _seed_ingredients(session, settings)

    existing = {row.id: row for row in session.scalars(select(Recipe))}
    # 菜名有唯一约束，按 id 查到「没有」不等于能插进去：运行期生成落库的菜可能
    # 与这次的种子同名，所以再按菜名查一遍
    by_name = {row.name: row for row in existing.values()}
    for item in load_seed_recipes(settings):
        row = existing.get(item["id"])
        if row is not None:
            if _stored_signature(row) == _recipe_signature(item):
                continue
            _apply_recipe(row, item)
            changed = True
            continue

        superseded = by_name.pop(item["name"], None)
        claimed = (
            _drop_superseded(session, superseded, item["id"]) if superseded is not None else []
        )
        if superseded is not None:
            existing.pop(superseded.id, None)
        session.add(_build_recipe(item))
        session.flush()
        for entry in claimed:
            entry.recipe_id = item["id"]
        changed = True
    session.flush()
    total = session.scalar(select(func.count()).select_from(Recipe)) or 0
    return total, changed


def _dish_map_changes(session: Session, seed: dict) -> bool:
    """现有承接表与种子文件是否有差异（新增或改值都算）。"""

    rows = {
        row.class_index: (row.recipe_id, row.alias)
        for row in session.scalars(select(DishRecipeMap))
    }
    if len(rows) != len(seed):
        return True
    for raw_index, entry in seed.items():
        entry = dict(entry or {})
        expected = (entry.get("recipe_id"), entry.get("alias"))
        if rows.get(int(raw_index)) != expected:
            return True
    return False


def _seed_dish_map(session: Session, settings: Settings, force: bool) -> tuple:
    """逐行 upsert，返回 (种子行数, 本次是否有变化)。

    这里**不能**按「行数够了就跳过」来判断幂等：补覆盖率时改的恰恰是这个文件，
    按行数判断会让改动永远同步不进已有数据库（只有删库或 force 才生效）。
    seed 里没有的行不动——可能是本机手工加的；force 才整表重建。
    """

    seed = load_seed_dish_map(settings)
    if force:
        session.query(DishRecipeMap).delete()
        session.flush()
        changed = bool(seed)
    else:
        changed = _dish_map_changes(session, seed)
    for raw_index, entry in seed.items():
        entry = dict(entry or {})
        session.merge(
            DishRecipeMap(
                class_index=int(raw_index),
                recipe_id=entry.get("recipe_id"),
                alias=entry.get("alias"),
            )
        )
    session.flush()
    return len(seed), changed


def seed_database(
    session: Session,
    settings: Optional[Settings] = None,
    force: bool = False,
) -> dict:
    """把种子数据写入数据库；两部分各自幂等。"""

    settings = settings or get_settings()
    recipes, inserted_recipes = _seed_recipes(session, settings, force)
    dish_map, inserted_map = _seed_dish_map(session, settings, force)
    return {
        "ingredients": session.scalar(select(func.count()).select_from(Ingredient)) or 0,
        "recipes": recipes,
        "dish_map": dish_map,
        "inserted": inserted_recipes or inserted_map,
    }
