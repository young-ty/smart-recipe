"""食材字典的数据契约。

字典是生成菜谱的准入名单：大模型写的主料挂不上字典，这道菜就按生成失败处理，
用户看到的是降级提示。所以字典本身要守住三条——名字不重复、别名不指错人、
分类取值统一；否则「挂不上」会被误判成「没这道菜」。

运行：python -m pytest backend/tests/test_ingredient_dictionary.py -v
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.recipe_resolver import ingredient_key, match_ingredient  # noqa: E402

DATA_FILE = BACKEND_DIR / "app" / "data" / "seed_ingredients.json"
CATEGORIES = {
    "蔬菜",
    "肉类",
    "水产",
    "蛋类",
    "豆制品",
    "菌菇",
    "干货",
    "主食",
    "调味",
    "乳制品",
    "水果",
}


@pytest.fixture(scope="module")
def dictionary() -> list:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def test_every_entry_has_the_same_shape(dictionary) -> None:
    for row in dictionary:
        assert set(row) == {"id", "name", "category", "alias"}, row
        assert isinstance(row["id"], int) and row["id"] > 0, row
        assert row["name"] and row["name"].strip() == row["name"], row
        assert row["category"] in CATEGORIES, row


def test_ids_and_names_are_unique(dictionary) -> None:
    ids = [row["id"] for row in dictionary]
    names = [row["name"] for row in dictionary]
    assert len(set(ids)) == len(ids), "食材 id 重复"
    assert len(set(names)) == len(names), "食材标准名重复"
    assert sorted(ids) == list(range(1, len(ids) + 1)), "id 应当从 1 起连续编号"


def test_aliases_do_not_collide(dictionary) -> None:
    """别名既不能内部撞车，也不能撞上别样食材的标准名——否则同一串字会指两样东西。"""

    name_to_id = {row["name"]: row["id"] for row in dictionary}
    aliases = [
        alias.strip()
        for row in dictionary
        for alias in (row["alias"] or "").split(",")
        if alias.strip()
    ]
    assert not [alias for alias, count in Counter(aliases).items() if count > 1], "别名重复"
    shadowed = [
        (row["name"], alias.strip(), name_to_id[alias.strip()])
        for row in dictionary
        for alias in (row["alias"] or "").split(",")
        if alias.strip() in name_to_id and name_to_id[alias.strip()] != row["id"]
    ]
    assert shadowed == [], "别名与其它食材的标准名冲突：%s" % shadowed


def test_common_main_ingredients_match_the_dictionary(dictionary) -> None:
    """208 类里的高频主料要能落到字典上，否则整道菜会被判生成失败、只能返回降级提示。

    这里走的是解析器真正用的匹配函数（含别名与子串兜底），不是简单比对标准名。
    """

    index: dict = {}
    for row in dictionary:
        index.setdefault(ingredient_key(row["name"]), row["id"])
        for alias in (row["alias"] or "").split(","):
            if alias.strip():
                index.setdefault(ingredient_key(alias), row["id"])

    written_by_model = (
        "豆腐",
        "土豆",
        "茄子",
        "白菜",
        "五花肉",
        "猪里脊肉",
        "鸡翅",
        "虾仁",
        "鲈鱼",
        "干黄豆",
        "清水（打浆用）",
        "粉丝",
        "黑木耳",
        "排骨",
        "银耳",
        "酸菜",
        "糯米粉",
    )
    missing = [name for name in written_by_model if match_ingredient(name, index) is None]
    assert missing == [], "常用主料挂不上字典：%s" % missing
