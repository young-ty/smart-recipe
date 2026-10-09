# -*- coding: utf-8 -*-
"""演示图归档的契约测试。

demo/dishes/ 里的图会直接用于演示，所以图名、README 里的登记与承接表三者必须对得上：
多一张没登记的、少一张登记过的、或者把库里没有承接的菜写成「库内命中」，现场都会翻车。
第二批豆腐系列只作留档、不进原型快照与 fixture，但登记口径与第一批相同。

运行：python -m unittest tests.test_demo_assets -v
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DISHES_DIR = REPO_ROOT / "demo" / "dishes"
DEMO_README = REPO_ROOT / "demo" / "README.md"
DISH_MAP = REPO_ROOT / "backend" / "app" / "data" / "seed_dish_map.json"
LABELS = REPO_ROOT / "models" / "dish_labels.json"

# | `dishes/dish-mapo-tofu.jpg` | 麻婆豆腐（豆腐块、红油、葱花） | #0 麻婆豆腐 87.0% | 库内命中 |
ROW = re.compile(
    r"^\|\s*`dishes/(?P<file>[\w.\-]+)`\s*\|(?P<content>[^|]*)\|(?P<top1>[^|]*)\|(?P<source>[^|]*)\|",
    re.MULTILINE,
)
TOP1 = re.compile(r"#(?P<index>\d+)\s*(?P<name>[^\s|]+)\s*(?P<pct>\d+(?:\.\d+)?)%")


class DemoAssetContractTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.readme = DEMO_README.read_text(encoding="utf-8")
        cls.rows = ROW.findall(cls.readme)
        cls.dish_map = json.loads(DISH_MAP.read_text(encoding="utf-8"))

    def test_readme_lists_every_photo(self):
        listed = {row[0] for row in self.rows}
        on_disk = {path.name for path in DISHES_DIR.glob("*.jpg")}
        self.assertTrue(listed, "README 里没解析到任何演示图登记行")
        self.assertEqual(
            sorted(listed - on_disk), [], "README 登记的图在 demo/dishes/ 里找不到"
        )
        self.assertEqual(
            sorted(on_disk - listed), [], "demo/dishes/ 里有图没登记进 demo/README.md"
        )

    def test_each_row_records_a_real_top1(self):
        for row in self.rows:
            self.assertIsNotNone(
                TOP1.search(row[2]),
                "登记行没写实测 top-1（模型给的类别与置信度）：%s" % row[0],
            )

    def test_top1_name_matches_the_delivered_label_list(self):
        """top-1 一列写的是模型判出的类别，下标与菜名要对得上交付的标签清单。

        写反过一次：把画面里的菜名（虾仁炒青笋、土豆肉末拌面）填在下标后面，
        而 #100 / #156 实际是莴笋肉丝与担担面，登记行读起来就变成模型判对了。
        """

        labels = json.loads(LABELS.read_text(encoding="utf-8"))
        for row in self.rows:
            hit = TOP1.search(row[2])
            self.assertIsNotNone(hit, "登记行没写实测 top-1：%s" % row[0])
            index, name = int(hit.group("index")), hit.group("name")
            self.assertEqual(
                labels[str(index)],
                name,
                "#%d 的菜名与 models/dish_labels.json 对不上：登记行写的是 %s" % (index, name),
            )

    def test_recipe_source_matches_the_handover_table(self):
        for row in self.rows:
            hit = TOP1.search(row[2])
            self.assertIsNotNone(hit, "登记行没写实测 top-1：%s" % row[0])
            index, source = int(hit.group("index")), row[3]
            entry = self.dish_map.get(str(index))
            recipe_id = entry.get("recipe_id") if isinstance(entry, dict) else None
            if recipe_id is not None:
                self.assertIn(
                    "库内命中", source, "#%d 有承接菜谱，来源栏应写库内命中" % index
                )
            else:
                self.assertIn(
                    "生成", source, "#%d 没有承接菜谱，来源栏应写生成分支" % index
                )
                self.assertNotIn(
                    "库内命中", source, "#%d 没有承接菜谱，不能写成库内命中" % index
                )

    def test_second_batch_is_marked_as_archive_only(self):
        """第二批只留档，README 要写明它不进原型快照与 fixture。"""

        self.assertIn("第二批", self.readme, "README 未说明第二批演示图的定位")
        self.assertIn(
            "不进原型快照", self.readme, "README 未写明第二批不进原型快照与 fixture"
        )


if __name__ == "__main__":
    unittest.main()
