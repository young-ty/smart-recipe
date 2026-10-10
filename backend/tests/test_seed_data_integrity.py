"""三个手写种子文件的结构完整性契约。

补库出错的方式很具体，而且在别处都看不出来：

1. `seed_dish_map.json` 里重复的键会被 JSON 解析器静默吞掉（后写的胜出，前一条无声消失）。
   这类重复会**降低**覆盖率，报表上看不出异常，只有对着原始文本数键才发现得了；
2. 菜谱引用的食材 id 写错，要到灌种子时才撞外键；
3. 两道菜谱同名时，库内检索只会命中其中一道，另一道等于没写；
4. 承接表里的类别下标写错，会在类别清单里对不上号。

运行：python -m pytest backend/tests/test_seed_data_integrity.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DATA_DIR = BACKEND_DIR / "app" / "data"
LABELS_PATH = REPO_ROOT / "models" / "dish_labels.json"


def read_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


class _Pairs:
    """原样保留键的顺序与重复项；普通 dict 会把重复键静默吞掉。"""

    def __init__(self, pairs):
        self.pairs = pairs


def read_map_keys() -> list:
    """按原始文本取承接表的键：重复的键一个都不丢。"""

    raw = (DATA_DIR / "seed_dish_map.json").read_text(encoding="utf-8")
    top = json.loads(raw, object_pairs_hook=_Pairs)
    return [key for key, _ in top.pairs]


@pytest.fixture(scope="module")
def ingredients():
    return read_json("seed_ingredients.json")


@pytest.fixture(scope="module")
def recipes():
    return read_json("seed_recipes.json")


@pytest.fixture(scope="module")
def dish_map():
    return read_json("seed_dish_map.json")


def test_dish_map_has_no_duplicate_keys():
    keys = read_map_keys()

    duplicates = sorted({key for key in keys if keys.count(key) > 1}, key=int)
    assert duplicates == [], "承接表里有重复的类别键，后写的会顶掉前一条：%s" % duplicates


def test_dish_map_keys_are_real_class_indexes(dish_map):
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))

    unknown = sorted((int(key) for key in dish_map if key not in labels), key=int)
    assert unknown == [], "承接表登记了类别清单里没有的下标：%s" % unknown


def test_dish_map_recipe_references_exist(dish_map, recipes):
    known = {row["id"] for row in recipes}

    missing = sorted(
        {entry["recipe_id"] for entry in dish_map.values() if entry.get("recipe_id") is not None}
        - known
    )
    assert missing == [], "承接表指向了不存在的菜谱 id：%s" % missing


def test_dish_map_entries_declare_both_fields(dish_map):
    for key, entry in dish_map.items():
        assert set(entry) == {"recipe_id", "alias"}, "类别 %s 的字段不是 recipe_id + alias" % key


def test_recipe_names_are_unique(recipes):
    names = [row["name"] for row in recipes]

    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], "种子菜谱里有同名菜，库内检索只能命中一道：%s" % duplicates


def test_recipe_ids_are_unique(recipes):
    ids = [row["id"] for row in recipes]

    assert len(ids) == len(set(ids)), "菜谱 id 重复"


def test_recipe_ingredient_ids_exist(recipes, ingredients):
    known = {row["id"] for row in ingredients}

    unknown = []
    for recipe in recipes:
        for line in recipe["required"] + recipe["optional"]:
            if line["id"] not in known:
                unknown.append((recipe["name"], line["id"]))
    assert unknown == [], "菜谱引用了字典里没有的食材：%s" % unknown


def test_every_recipe_has_steps_and_a_required_ingredient(recipes):
    empty = [row["name"] for row in recipes if not row["steps"] or not row["required"]]

    assert empty == [], "菜谱缺步骤或没有主料：%s" % empty
