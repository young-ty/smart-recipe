"""菜谱与食材改动同步的契约（seed.py 里的 _seed_recipes / _seed_ingredients）。

原先的实现按「表里有菜谱就跳过」判断幂等：本机数据库已经有菜谱时，往
seed_recipes.json 补新菜谱会被静默跳过，而 seed_dish_map.json 里的新映射照常写入，
最后直接撞外键——补库当场失败，且是启动即失败。这条路径必须有测试守住。

运行：python -m pytest backend/tests/test_seed_recipes_sync.py -v
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.core.database import create_all, get_engine, init_engine, session_scope  # noqa: E402
from app.models import DishRecipeMap, Ingredient, Recipe, RecipeStep  # noqa: E402
from app.services.seed import seed_database  # noqa: E402

REAL_DATA_DIR = BACKEND_DIR / "app" / "data"
SEED_FILES = ("seed_ingredients.json", "seed_recipes.json", "seed_dish_map.json")

# 用例里新增的条目：id 要避开种子文件里的区间（食材 1~88、菜谱 101~175）
NEW_INGREDIENT = {"id": 900, "name": "测试食材", "category": "调味", "alias": None}
NEW_RECIPE = {
    "id": 900,
    "name": "测试蛋炒饭",
    "cook_time_minutes": 12,
    "difficulty": "简单",
    "rating": 4.0,
    "servings": 2,
    "description": "用例造的数据，只存在于临时目录。",
    "required": [{"id": 56, "amount": "2 碗"}],
    "optional": [{"id": 63, "amount": "少许"}],
    "steps": ["米饭打散", "鸡蛋炒散", "下米饭炒匀"],
}


@pytest.fixture()
def seeded(tmp_path):
    """一套可随意改写的种子文件 + 独立数据库。"""

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in SEED_FILES:
        shutil.copy(REAL_DATA_DIR / name, data_dir / name)

    settings = Settings(
        database_url="sqlite+pysqlite:///%s" % (tmp_path / "recipes.db").as_posix(),
        data_dir=data_dir,
        models_dir=tmp_path / "models-not-delivered",
    )
    init_engine(settings)
    create_all(get_engine())
    return settings


def _read(settings, name: str):
    return json.loads((settings.data_dir / name).read_text(encoding="utf-8"))


def _write(settings, name: str, payload) -> None:
    (settings.data_dir / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _seed(settings, force: bool = False) -> dict:
    with session_scope() as session:
        return seed_database(session, settings, force=force)


def _recipe_names(settings) -> dict:
    with session_scope() as session:
        return {row.id: row.name for row in session.scalars(select(Recipe))}


def _steps_of(settings, recipe_id: int) -> list:
    with session_scope() as session:
        return list(
            session.scalars(
                select(RecipeStep.content)
                .where(RecipeStep.recipe_id == recipe_id)
                .order_by(RecipeStep.step_no)
            )
        )


# --------------------------- 幂等 ---------------------------

def test_first_seed_writes_every_recipe(seeded):
    summary = _seed(seeded)
    assert summary["inserted"] is True
    assert summary["recipes"] == len(_read(seeded, "seed_recipes.json"))
    assert len(_recipe_names(seeded)) == summary["recipes"]


def test_second_seed_reports_no_change(seeded):
    _seed(seeded)
    again = _seed(seeded)
    assert again["inserted"] is False, "没有变化时不该报告写入"


# --------------------------- 改动同步（回归点） ---------------------------

def test_added_recipe_is_synced_without_force(seeded):
    """补一道菜 + 一条指向它的映射，重灌种子就该生效——旧实现会撞外键。"""

    _seed(seeded)
    recipes = _read(seeded, "seed_recipes.json")
    recipes.append(NEW_RECIPE)
    _write(seeded, "seed_recipes.json", recipes)

    mapping = _read(seeded, "seed_dish_map.json")
    assert "200" not in mapping, "这条用例要挑一个种子文件里没有的类别"
    mapping["200"] = {"recipe_id": 900, "alias": None}
    _write(seeded, "seed_dish_map.json", mapping)

    summary = _seed(seeded)
    assert summary["inserted"] is True, "新增菜谱应报告为一次写入"
    assert _recipe_names(seeded)[900] == "测试蛋炒饭"
    assert _steps_of(seeded, 900) == NEW_RECIPE["steps"]
    with session_scope() as session:
        row = session.get(DishRecipeMap, 200)
        assert row is not None and row.recipe_id == 900


def test_added_ingredient_is_synced_without_force(seeded):
    _seed(seeded)
    ingredients = _read(seeded, "seed_ingredients.json")
    ingredients.append(NEW_INGREDIENT)
    _write(seeded, "seed_ingredients.json", ingredients)

    _seed(seeded)
    with session_scope() as session:
        assert session.get(Ingredient, 900) is not None


def test_changed_recipe_is_rebuilt(seeded):
    """改了步骤要能同步，否则补库改了字句而接口还是老文本。"""

    _seed(seeded)
    recipes = _read(seeded, "seed_recipes.json")
    target = next(row for row in recipes if row["id"] == 101)
    target["steps"] = ["用例改过的第一步"] + target["steps"][1:]
    _write(seeded, "seed_recipes.json", recipes)

    assert _seed(seeded)["inserted"] is True
    assert _steps_of(seeded, 101)[0] == "用例改过的第一步"


def test_recipe_outside_the_seed_file_is_left_alone(seeded):
    """种子里没有的菜谱不动——可能是本机手工加的。"""

    _seed(seeded)
    recipes = _read(seeded, "seed_recipes.json")
    dropped = next(row for row in recipes if row["id"] == 149)
    _write(seeded, "seed_recipes.json", [row for row in recipes if row["id"] != 149])

    _seed(seeded)
    assert _recipe_names(seeded)[149] == dropped["name"]


def test_force_rebuilds_recipes_from_the_file(seeded):
    """force 是整表重建：种子里没有的菜谱会被清掉。"""

    _seed(seeded)
    recipes = _read(seeded, "seed_recipes.json")
    _write(seeded, "seed_recipes.json", [row for row in recipes if row["id"] != 149])

    _seed(seeded, force=True)
    assert 149 not in _recipe_names(seeded)
