"""覆盖率报表工具（backend/tools/coverage_report.py）的测试。

报表是补覆盖率时的唯一依据，所以三件事必须锁死：
分区口径不能混（可承接 / 显式缺口 / 留白）、别名候选不能乱配、脚本必须**只读**。
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

import tools.coverage_report as report  # noqa: E402

LABELS = {"0": "麻婆豆腐", "1": "家常豆腐", "2": "西红柿炒鸡蛋", "3": "炒青菜"}
MAPPING = {"0": {"recipe_id": 106, "alias": None}, "1": {"recipe_id": None, "alias": None}}
RECIPES = [
    {"id": 106, "name": "麻婆豆腐"},
    {"id": 101, "name": "番茄炒蛋"},
    {"id": 124, "name": "清炒青菜"},
    {"id": 149, "name": "豆芽炒粉丝"},
]


# --------------------------- 归一化与相似度 ---------------------------

def test_normalize_folds_known_synonyms():
    """同义写法与同义食材名都折到标准写法，且折叠幂等。

    这张表与运行时召回共用（`app.services.dish_text`）：报表说「这个留白能靠别名补」，
    线上就得真能召回到，否则报表只是好看。
    """

    assert report.normalize("西红柿炒鸡蛋") == "番茄炒蛋"
    assert report.normalize("蒜茸粉丝蒸虾") == "蒜蓉粉丝蒸虾"
    assert report.normalize("薯仔焖牛腩") == "土豆焖牛腩"
    assert report.normalize(report.normalize("西红柿炒鸡蛋")) == "番茄炒蛋"


def test_similarity_ranks_near_names_above_unrelated_ones():
    near = report.similarity(report.normalize("土豆炖牛腩"), report.normalize("土豆炖牛肉"))
    far = report.similarity(report.normalize("土豆炖牛腩"), report.normalize("绿豆糕"))
    assert near > 0.7
    assert far < 0.2


# --------------------------- 三分区口径 ---------------------------

def test_split_classes_separates_the_three_partitions():
    groups = report.split_classes(LABELS, MAPPING)
    assert [row[:2] for row in groups["available"]] == [(0, "麻婆豆腐")]
    assert [row[:2] for row in groups["declared_gaps"]] == [(1, "家常豆腐")]
    assert [row[:2] for row in groups["blank"]] == [(2, "西红柿炒鸡蛋"), (3, "炒青菜")]
    assert sum(len(groups[key]) for key in ("available", "declared_gaps", "blank")) == len(LABELS)


def test_declared_gap_is_not_counted_as_served():
    """显式缺口是「已知没有菜谱」，不能算进可承接，否则覆盖率虚高。"""

    built = report.build_report(LABELS, MAPPING, RECIPES)
    assert built["served"] == 1
    assert built["coverage_of_all"] == pytest.approx(1 / 4)
    assert built["coverage_of_registered"] == pytest.approx(1 / 2)


def test_idle_recipes_exclude_pointed_ones():
    idle = report.idle_recipes(RECIPES, MAPPING)
    assert [row["name"] for row in idle] == ["番茄炒蛋", "清炒青菜", "豆芽炒粉丝"]


# --------------------------- 别名候选 ---------------------------

def test_alias_candidates_pick_synonyms_and_substrings():
    groups = report.split_classes(LABELS, MAPPING)
    found = report.alias_candidates(groups["blank"], report.idle_recipes(RECIPES, MAPPING))
    by_index = {index: matches for index, _, matches in found}

    assert [name for name, _ in by_index[2]] == ["番茄炒蛋"], "西红柿炒鸡蛋 应配上 番茄炒蛋"
    assert [name for name, _ in by_index[3]] == ["清炒青菜"], "炒青菜 是 清炒青菜 的子串"


def test_alias_candidates_skip_unrelated_names():
    found = report.alias_candidates([(7, "绿豆糕")], report.idle_recipes(RECIPES, MAPPING))
    assert found == [], "完全不相干的菜名不该被配上"



def test_alias_candidate_scores_are_sorted_desc():
    groups = report.split_classes(LABELS, MAPPING)
    for _, _, matches in report.alias_candidates(groups["blank"], report.idle_recipes(RECIPES, MAPPING)):
        scores = [score for _, score in matches]
        assert scores == sorted(scores, reverse=True)


# --------------------------- 渲染与入口 ---------------------------

def test_render_states_every_partition():
    text = report.render(report.build_report(LABELS, MAPPING, RECIPES))
    for keyword in ("可承接", "显式缺口", "留白", "闲置菜谱", "别名候选"):
        assert keyword in text, "报表缺少分区：%s" % keyword
    assert "只读" in text


def test_main_json_is_machine_readable(tmp_path, capsys):
    sources = {}
    for name, payload in (("labels", LABELS), ("map", MAPPING), ("recipes", RECIPES)):
        sources[name] = tmp_path / ("%s.json" % name)
        sources[name].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    code = report.main([
        "--json",
        "--labels", str(sources["labels"]),
        "--map", str(sources["map"]),
        "--recipes", str(sources["recipes"]),
    ])

    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["total_classes"] == 4 and body["served"] == 1


# --------------------------- 真实数据契约 ---------------------------

def test_real_repository_partitions_add_up():
    built = report.build_report(
        report.load_json(report.DEFAULT_LABELS),
        report.load_json(report.DEFAULT_MAP),
        report.load_json(report.DEFAULT_RECIPES),
    )
    assert built["total_classes"] == 208
    assert (
        built["served"] + len(built["declared_gaps"]) + len(built["blank"])
        == built["total_classes"]
    )


def test_real_coverage_does_not_regress():
    """补库时这个数字只该涨。跌了说明有映射被误删。"""

    built = report.build_report(
        report.load_json(report.DEFAULT_LABELS),
        report.load_json(report.DEFAULT_MAP),
        report.load_json(report.DEFAULT_RECIPES),
    )
    # 2026-09-22 补 26 道菜谱 + 1 条别名后是 50 类；2026-10-10 再补 23 道到 73 类
    assert built["served"] >= 73, "可承接类别数比已交付的 73 类还少"
    assert built["coverage_of_registered"] >= 0.8


def test_report_never_writes_to_the_data_files():
    """报表是依据，不能自己改数据。"""

    paths = [report.DEFAULT_LABELS, report.DEFAULT_MAP, report.DEFAULT_RECIPES]
    before = [(path, path.read_bytes()) for path in paths]
    report.build_report(*[report.load_json(path) for path in paths])
    for path, blob in before:
        assert path.read_bytes() == blob, "%s 被报表改动了" % path.name
