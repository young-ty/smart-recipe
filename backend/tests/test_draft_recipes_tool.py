"""菜谱起草工具（backend/tools/draft_recipes.py）的测试。

这个脚本会往菜谱库写数据，所以边界必须测死：
不联网的 --plan 一次请求都不发；生成失败不留半截草案；
未经人工审校、缺 rating、食材没映射上 id 的条目，--merge 一律拒绝。
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import tools.draft_recipes as tool  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.services.generator import GeneratedRecipePayload  # noqa: E402

INGREDIENTS = [
    {"id": 1, "name": "番茄", "category": "蔬菜", "alias": "西红柿,蕃茄"},
    {"id": 2, "name": "鸡蛋", "category": "蛋类", "alias": "蛋,鸡子"},
    {"id": 3, "name": "大蒜", "category": "调味", "alias": "蒜,蒜瓣"},
    {"id": 4, "name": "豆腐", "category": "豆制品", "alias": None},
]

LABELS = {"1": "家常豆腐", "2": "煎豆腐", "50": "西红柿炒鸡蛋"}
MAPPING = {"50": {"recipe_id": 101, "alias": "西红柿炒鸡蛋"}}
RECIPES = [{"id": 101, "name": "番茄炒蛋", "rating": 4.8}]

PAYLOAD = GeneratedRecipePayload(
    name="家常豆腐",
    cook_time_minutes=20,
    difficulty="简单",
    servings=2,
    description="豆腐先煎定型再烧，不容易碎。",
    required=[{"name": "豆腐", "amount": "400 克"}, {"name": "蒜", "amount": "2 瓣"}],
    optional=[{"name": "西红柿", "amount": "1 个"}],
    steps=["豆腐切厚片煎至两面金黄", "蒜末下锅小火爆香", "下豆腐与调料烧至入味"],
)


def _draft(**overrides) -> dict:
    draft = tool.payload_to_draft(
        1, "家常豆腐", 150, PAYLOAD, tool.build_ingredient_index(INGREDIENTS)
    )
    draft.update(overrides)
    return draft


# --------------------------- 食材映射 ---------------------------

def test_ingredient_index_covers_names_and_aliases():
    index = tool.build_ingredient_index(INGREDIENTS)
    assert index["番茄"] == index["西红柿"] == index["蕃茄"] == 1
    assert index["蒜"] == 3 and index["大蒜"] == 3
    assert tool.match_ingredient(" 豆腐 ", index) == 4


def test_unknown_ingredient_is_not_guessed():
    index = tool.build_ingredient_index(INGREDIENTS)
    assert tool.match_ingredient("松茸", index) is None
    assert tool.match_ingredient("", index) is None


def test_draft_records_unmatched_ingredients():
    # 用 model_validate 过一遍：model_copy(update=...) 不做校验，会塞进裸 dict
    payload = GeneratedRecipePayload.model_validate(
        {**PAYLOAD.model_dump(), "optional": [{"name": "松茸", "amount": "少许"}]}
    )
    draft = tool.payload_to_draft(1, "家常豆腐", 150, payload, tool.build_ingredient_index(INGREDIENTS))
    assert draft["unmatched_ingredients"] == ["松茸"]
    assert draft["recipe"]["optional"][0]["ingredient_id"] is None


# --------------------------- 草案形状 ---------------------------

def test_draft_starts_unapproved_without_rating():
    draft = _draft()
    assert draft["approved"] is False, "草案默认未审校，这是人工那一步的痕迹"
    assert draft["rating"] is None, "生成层不给评分，必须人工补"
    assert draft["unmatched_ingredients"] == []
    assert draft["recipe"]["required"][1]["ingredient_id"] == 3


def test_draft_one_returns_none_instead_of_faking_a_draft():
    class _Broken:
        configured = True
        model = "broken"

        def generate(self, dish):
            return None

    assert tool.draft_one(_Broken(), 1, "家常豆腐", 150, {}) is None


def test_seed_recipe_shape_matches_the_library():
    real = json.loads((BACKEND_DIR / "app" / "data" / "seed_recipes.json").read_text(encoding="utf-8"))[0]
    converted = tool.draft_to_seed_recipe(_draft(rating=4.5))
    assert set(converted) == set(real), "草案转出来的字段必须与现有菜谱一致"
    assert set(converted["required"][0]) == set(real["required"][0])


# --------------------------- 合并门槛 ---------------------------

def test_merge_refuses_unapproved_draft():
    with pytest.raises(tool.DraftError, match="approved"):
        tool.validate_for_merge(_draft())


def test_merge_refuses_draft_without_rating():
    with pytest.raises(tool.DraftError, match="rating"):
        tool.validate_for_merge(_draft(approved=True))


def test_merge_refuses_draft_with_unmatched_ingredients():
    with pytest.raises(tool.DraftError, match="松茸"):
        tool.validate_for_merge(_draft(approved=True, rating=4.5, unmatched_ingredients=["松茸"]))


def test_merge_refuses_missing_ingredient_id():
    draft = _draft(approved=True, rating=4.5)
    draft["recipe"]["required"][0]["ingredient_id"] = None
    with pytest.raises(tool.DraftError, match="ingredient_id"):
        tool.validate_for_merge(draft)


def test_merge_accepts_a_reviewed_draft():
    tool.validate_for_merge(_draft(approved=True, rating=4.5))


# --------------------------- 合并行为 ---------------------------

def test_merge_only_takes_approved_drafts():
    drafts = {"drafts": [_draft(approved=True, rating=4.5), _draft(approved=False, class_index=2, class_name="煎豆腐")]}
    recipes, mapping, skipped = tool.merge_drafts(drafts, RECIPES, MAPPING)
    assert [row["id"] for row in recipes] == [101, 150]
    assert mapping["1"] == {"recipe_id": 150, "alias": None}, "菜名与类别名相同，不需要 alias"
    assert "2" not in mapping
    assert len(skipped) == 1 and "未审校" in skipped[0]


def test_merge_records_alias_when_the_names_differ():
    draft = _draft(approved=True, rating=4.5, class_name="家常烧豆腐")
    _, mapping, _ = tool.merge_drafts({"drafts": [draft]}, RECIPES, MAPPING)
    assert mapping["1"] == {"recipe_id": 150, "alias": "家常烧豆腐"}


def test_merge_keeps_the_mapping_sorted_by_index():
    drafts = {"drafts": [
        _draft(approved=True, rating=4.5, class_index=30, class_name="麻辣豆腐"),
        _draft(approved=True, rating=4.5, class_index=2, class_name="煎豆腐", recipe_id=151),
    ]}
    _, mapping, _ = tool.merge_drafts(drafts, RECIPES, MAPPING)
    assert list(mapping) == ["2", "30", "50"]


def test_merge_rejects_duplicate_recipe_id():
    with pytest.raises(tool.DraftError, match="已存在"):
        tool.merge_drafts({"drafts": [_draft(approved=True, rating=4.5, recipe_id=101)]}, RECIPES, MAPPING)


# --------------------------- 选取与入口 ---------------------------

def test_blank_classes_only_lists_unregistered_ones():
    assert tool.blank_classes(LABELS, MAPPING) == [(1, "家常豆腐"), (2, "煎豆腐")]


def test_select_respects_limit_indices_and_existing_drafts():
    blank = [(1, "家常豆腐"), (2, "煎豆腐"), (3, "豆腐花")]
    drafts = {"drafts": [_draft(class_index=1, class_name="家常豆腐")]}

    assert tool._select(blank, drafts, Namespace(indices="", limit=5, force=False)) == [(2, "煎豆腐"), (3, "豆腐花")]
    assert tool._select(blank, drafts, Namespace(indices="", limit=1, force=False)) == [(2, "煎豆腐")]
    assert tool._select(blank, drafts, Namespace(indices="3", limit=5, force=False)) == [(3, "豆腐花")]
    assert len(tool._select(blank, drafts, Namespace(indices="", limit=5, force=True))) == 3


def test_select_rejects_indices_that_are_not_blank():
    with pytest.raises(tool.DraftError):
        tool._select([(1, "家常豆腐")], {"drafts": []}, Namespace(indices="50", limit=5, force=False))


def test_main_without_write_never_touches_the_generator(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(tool, "LABELS_PATH", _json(tmp_path, "labels", LABELS))
    monkeypatch.setattr(tool, "DISH_MAP_PATH", _json(tmp_path, "map", MAPPING))
    monkeypatch.setattr(tool, "RECIPES_PATH", _json(tmp_path, "recipes", RECIPES))
    monkeypatch.setattr(tool, "DRAFTS_PATH", tmp_path / "drafts.json")

    def boom(settings):  # pragma: no cover - 被调用即失败
        raise AssertionError("--plan 不该装配生成层")

    monkeypatch.setattr(tool, "build_generator", boom)
    assert tool.main([]) == 0
    out = capsys.readouterr().out
    assert "留白类别 2 个" in out and "家常豆腐" in out


def test_main_write_then_merge_end_to_end(monkeypatch, capsys, tmp_path):
    labels_path = _json(tmp_path, "labels", LABELS)
    map_path = _json(tmp_path, "map", MAPPING)
    recipes_path = _json(tmp_path, "recipes", RECIPES)
    drafts_path = tmp_path / "drafts" / "recipe_drafts.json"
    monkeypatch.setattr(tool, "LABELS_PATH", labels_path)
    monkeypatch.setattr(tool, "DISH_MAP_PATH", map_path)
    monkeypatch.setattr(tool, "RECIPES_PATH", recipes_path)
    monkeypatch.setattr(tool, "DRAFTS_PATH", drafts_path)
    monkeypatch.setattr(tool, "INGREDIENTS_PATH", _json(tmp_path, "ing", INGREDIENTS))
    monkeypatch.setattr(tool, "get_settings", lambda: Settings(generator_api_key="k"))
    monkeypatch.setattr(tool, "build_generator", lambda settings: _Stub())

    assert tool.main(["--write", "--limit", "1"]) == 0
    written = json.loads(drafts_path.read_text(encoding="utf-8"))
    assert len(written["drafts"]) == 1
    assert written["drafts"][0]["approved"] is False

    # 未审校 → 合并不进来
    assert tool.main(["--merge"]) == 0
    assert json.loads(recipes_path.read_text(encoding="utf-8")) == RECIPES

    # 人工审校后 → 合并成功，两个文件同时更新
    reviewed = json.loads(drafts_path.read_text(encoding="utf-8"))
    reviewed["drafts"][0]["approved"] = True
    reviewed["drafts"][0]["rating"] = 4.6
    drafts_path.write_text(json.dumps(reviewed, ensure_ascii=False), encoding="utf-8")

    assert tool.main(["--merge"]) == 0
    merged_recipes = json.loads(recipes_path.read_text(encoding="utf-8"))
    merged_map = json.loads(map_path.read_text(encoding="utf-8"))
    assert [row["id"] for row in merged_recipes] == [101, 102], "新 id 从现有最大值往后接"
    assert merged_map["1"] == {"recipe_id": 102, "alias": None}, "菜名与类别名相同，不需要 alias"
    assert capsys.readouterr().out.count("已合并") >= 1


class _Stub:
    configured = True
    model = "stub"

    def generate(self, dish):
        return PAYLOAD


def test_write_json_creates_the_parent_directory(tmp_path):
    """草案目录默认不存在——真跑一次才发现这里会崩，测试要盯住。"""

    target = tmp_path / "drafts" / "nested" / "recipe_drafts.json"
    tool.write_json(target, {"drafts": []})
    assert json.loads(target.read_text(encoding="utf-8")) == {"drafts": []}


def test_drafts_directory_is_gitignored():
    """草案是中间产物，不该进版本库（审校后合进 seed 数据）。"""

    lines = [
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    ]
    assert "drafts/" in lines


def _json(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / ("%s.json" % name)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
