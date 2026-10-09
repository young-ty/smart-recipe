# -*- coding: utf-8 -*-
"""前端原型（prototype/index.html）的契约测试。

校验原型内嵌的种子数据、基准用例的匹配结果，以及原型是否可离线独立打开。

运行：python -m unittest tests.test_prototype -v
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_PATH = REPO_ROOT / "prototype" / "index.html"

BASELINE_SELECTED = {"番茄", "鸡蛋"}
CONFIDENCE_THRESHOLD = 0.75

REQUIRED_UI_STRINGS = [
    "拍照识别",
    "模拟 CV 模型推理",
    "开始识别",
    "重置",
    "模型: 待接入 · 食材 103 类",
    "识别到的食材",
    "点击可取消勾选",
    "推荐菜谱",
    "按匹配度排序",
]

EXTERNAL_RESOURCE_PATTERN = re.compile(
    "(?:src|href)\\s*=\\s*[\"']https?://|@import\\s+url\\(\\s*[\"']?https?://",
    flags=re.IGNORECASE,
)


def read_prototype():
    return PROTOTYPE_PATH.read_text(encoding="utf-8")


def embedded_json(html, script_id):
    pattern = '<script id="%s" type="application/json">(.*?)</script>' % re.escape(script_id)
    match = re.search(pattern, html, flags=re.DOTALL)
    if not match:
        raise AssertionError("未找到内嵌数据块：%s" % script_id)
    return json.loads(match.group(1))


def match_ratio(recipe, selected):
    required = recipe["required"]
    matched = [name for name in required if name in selected]
    return {
        "id": recipe["id"],
        "name": recipe["name"],
        "matched": len(matched),
        "total_required": len(required),
        "ratio": float(len(matched)) / len(required) if required else 0.0,
        "missing": len(required) - len(matched),
        "rating": recipe["rating"],
        "cook_time_minutes": recipe["cook_time_minutes"],
    }


def sort_key(item):
    return (
        -item["ratio"],
        -item["matched"],
        item["missing"],
        -item["rating"],
        item["cook_time_minutes"],
        item["id"],
    )


class PrototypeContractTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = read_prototype()
        cls.recipes = embedded_json(cls.html, "seed-recipes")
        cls.ingredients = embedded_json(cls.html, "seed-ingredients")

    def test_required_ui_strings_present(self):
        for text in REQUIRED_UI_STRINGS:
            self.assertIn(text, self.html, "原型缺少界面文案：%s" % text)

    def test_prototype_is_offline_standalone(self):
        self.assertEqual(
            EXTERNAL_RESOURCE_PATTERN.findall(self.html),
            [],
            "原型引用了外部资源，无法离线打开",
        )

    def test_seed_data_integrity(self):
        ingredient_names = [item["name"] for item in self.ingredients]
        self.assertEqual(len(ingredient_names), len(set(ingredient_names)), "食材字典存在重名")

        recipe_ids = [recipe["id"] for recipe in self.recipes]
        self.assertEqual(len(recipe_ids), len(set(recipe_ids)), "菜谱 id 存在重复")

        known = set(ingredient_names)
        for recipe in self.recipes:
            self.assertTrue(recipe["required"], "菜谱 %s 缺少必需食材" % recipe["name"])
            self.assertTrue(recipe["steps"], "菜谱 %s 缺少步骤" % recipe["name"])
            unknown = [name for name in recipe["required"] if name not in known]
            self.assertEqual(unknown, [], "菜谱 %s 引用了字典外的食材：%s" % (recipe["name"], unknown))
            self.assertIn(
                recipe["difficulty"],
                ("简单", "中等", "困难"),
                "菜谱 %s 的难度取值非法" % recipe["name"],
            )

    def test_baseline_case_matches_design_doc(self):
        scored = [match_ratio(recipe, BASELINE_SELECTED) for recipe in self.recipes]
        hits = [item for item in scored if item["matched"] > 0]
        ranked = sorted(hits, key=sort_key)

        self.assertEqual(len(ranked), 4, "基准用例应命中 4 道菜（设计稿：4 道菜 · 按匹配度排序）")
        self.assertEqual(ranked[0]["name"], "番茄炒蛋")
        self.assertEqual(ranked[1]["name"], "番茄蛋花汤")
        for item in ranked[:2]:
            self.assertEqual(
                (item["matched"], item["total_required"], item["ratio"]),
                (2, 2, 1.0),
            )

    def test_low_confidence_ingredient_is_unselected_by_default(self):
        detected = [
            {"name": "番茄", "confidence": 0.96},
            {"name": "鸡蛋", "confidence": 0.89},
            {"name": "青椒", "confidence": 0.72},
        ]
        selected = [item["name"] for item in detected if item["confidence"] >= CONFIDENCE_THRESHOLD]
        self.assertEqual(selected, ["番茄", "鸡蛋"])

    def test_no_placeholder_left(self):
        self.assertNotIn("PATCH-MARKER", self.html, "原型中残留占位符")


if __name__ == "__main__":
    unittest.main()
