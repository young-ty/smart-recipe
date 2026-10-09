# -*- coding: utf-8 -*-
"""成品菜识别原型（prototype/dish.html）的契约测试。

这个原型拿模型的真实输出当演示数据，好处是所见即所得，代价是它特别容易过期——
换了模型或改了预处理，页面上的置信度就变成编的了。所以这里守三件事：

1. 页面里内嵌的那份数据，必须还能被 prototype/build_dish_prototype.py 原样重算出来；
2. 重算出来的候选和置信度，必须和 models/dish.onnx 的真实输出对得上；
3. 类别清单已交付（models/dish_labels.json），页面上的菜名必须与清单逐项一致。

权重或运行环境缺失时自动跳过，不阻塞其他人跑测试。

运行：python -m unittest tests.test_dish_prototype -v
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE_PATH = REPO_ROOT / "prototype" / "dish.html"
MODEL_PATH = REPO_ROOT / "models" / "dish.onnx"
LABELS_PATH = REPO_ROOT / "models" / "dish_labels.json"
MANIFEST_PATH = REPO_ROOT / "models" / "dish.manifest.json"
SEED_RECIPES_PATH = REPO_ROOT / "backend" / "app" / "data" / "seed_recipes.json"
BUILDER_MODULE = "prototype.build_dish_prototype"

REQUIRED_UI_STRINGS = [
    "拍照识别",
    "开始识别",
    "重置",
    "识别结果",
    "点击切换候选",
    "菜谱与做法",
    "视频教程",
]

EXTERNAL_RESOURCE_PATTERN = re.compile(
    "(?:src|href)\\s*=\\s*[\"']https?://|@import\\s+url\\(\\s*[\"']?https?://",
    flags=re.IGNORECASE,
)

PLACEHOLDER_PATTERNS = ["PATCH-MARKER", "TODO", "FIXME", "XXX"]

CONFIDENCE_TOLERANCE = 0.002

# 单张推理耗时是现场测的，每次都不一样，比对时先抹掉
LATENCY_PATTERN = re.compile(r'("latencyMs":\s*)\d+')


def read_prototype():
    return PROTOTYPE_PATH.read_text(encoding="utf-8")


def embedded_json(html, script_id):
    pattern = '<script id="%s" type="application/json">(.*?)</script>' % re.escape(script_id)
    match = re.search(pattern, html, flags=re.DOTALL)
    if not match:
        raise AssertionError("未找到内嵌数据块：%s" % script_id)
    return json.loads(match.group(1))


def softmax_topk(logits, k=5):
    import numpy as np

    shifted = np.exp(logits - logits.max())
    probs = shifted / shifted.sum()
    return [(int(index), float(probs[index])) for index in probs.argsort()[::-1][:k]]


class DishPrototypeContractTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = read_prototype()
        cls.demo = embedded_json(cls.html, "demo-dishes")

    def test_required_ui_strings_present(self):
        for text in REQUIRED_UI_STRINGS:
            self.assertIn(text, self.html, "原型缺少界面文案：%s" % text)

    def test_prototype_is_offline_standalone(self):
        self.assertEqual(
            EXTERNAL_RESOURCE_PATTERN.findall(self.html),
            [],
            "原型引用了外部资源，无法离线打开",
        )

    def test_banner_cites_the_delivered_class_list(self):
        """类别清单已交付：页面要说明名字的来源，且不能再留占位标记。"""

        self.assertIn("dish_labels.json", self.html, "页面上没有说明类别名的来源")
        self.assertIn("import_labels.py", self.html, "页面上没有说明清单是怎么转出来的")
        self.assertNotIn("待确认", self.html, "清单已交付，不应再有「待确认」字样")
        self.assertNotIn("类别 #", self.html.split('id="demo-dishes"')[1].split("</script>")[0],
                         "内嵌数据里不应再有「类别 #N」占位名")

    def test_class_names_match_the_delivered_labels(self):
        """页面里的每个类别名都必须等于清单里的那一条，不许自己编。"""

        if not LABELS_PATH.is_file():
            self.skipTest("类别清单缺失：%s" % LABELS_PATH)
        labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))

        self.assertEqual(len(self.demo["labels"]), len(labels), "页面内嵌的类别数与清单不一致")
        self.assertEqual(self.demo["labels"], labels, "页面内嵌的类别名与清单不一致")

        for photo in self.demo["photos"]:
            for hit in photo["topk"]:
                self.assertEqual(
                    self.demo["labels"][str(hit["index"])],
                    labels[str(hit["index"])],
                    "%s 的候选 #%d 名称与清单不一致" % (photo["id"], hit["index"]),
                )

    def test_video_link_is_generated_by_template(self):
        """视频链接由模板拼接，页面里不允许出现写死的 http 链接。"""

        self.assertIn("search.bilibili.com", self.html, "缺少视频搜索链接模板")
        self.assertIn("?keyword=", self.html, "视频链接没有按关键字拼接")

    def test_demo_data_shape(self):
        photos = self.demo["photos"]
        self.assertEqual(len(photos), 4, "演示图应为 4 张")

        for photo in photos:
            topk = photo["topk"]
            self.assertEqual(len(topk), 5, "%s 的候选数应为 5" % photo["id"])
            confidences = [hit["confidence"] for hit in topk]
            self.assertEqual(
                confidences, sorted(confidences, reverse=True), "%s 的候选未按置信度降序" % photo["id"]
            )
            for hit in topk:
                self.assertTrue(0 <= hit["index"] < self.demo["model"]["classes"], "类别下标越界")
                self.assertTrue(0 < hit["confidence"] <= 1, "置信度不在 (0, 1] 内")

            resolved = photo["resolved"]
            self.assertTrue(resolved["required"], "%s 的菜谱缺少主料" % photo["id"])
            self.assertTrue(resolved["steps"], "%s 的菜谱缺少步骤" % photo["id"])
            self.assertIn(
                resolved["difficulty"], ("简单", "中等", "困难"), "%s 的难度取值非法" % photo["id"]
            )
            self.assertTrue(photo["file"].startswith("../demo/"), "演示图应取自 demo/ 目录")

    def test_library_recipe_is_not_made_up(self):
        """能对上菜谱库的那道菜，必须真的等于种子数据里的那条记录。"""

        seed = json.loads(SEED_RECIPES_PATH.read_text(encoding="utf-8"))
        by_id = {item["id"]: item for item in seed}

        resolved = self.demo["photos"][0]["resolved"]
        self.assertTrue(resolved["inLibrary"], "红烧肉演示图应命中菜谱库")
        source = by_id[resolved["recipeId"]]

        self.assertEqual(resolved["name"], source["name"])
        self.assertEqual(resolved["steps"], source["steps"])
        self.assertEqual(resolved["cookTimeMinutes"], source["cook_time_minutes"])
        self.assertEqual(resolved["difficulty"], source["difficulty"])
        self.assertEqual(resolved["rating"], source["rating"])
        self.assertEqual(len(resolved["required"]), len(source["required"]))

        for photo in self.demo["photos"][1:]:
            self.assertFalse(photo["resolved"]["inLibrary"], "库外的菜不该声称命中菜谱库")
            self.assertIn(
                "FR-13", photo["resolved"]["source"], "库外的菜应注明正式前端由大模型生成"
            )
            self.assertNotIn(
                "将来",
                photo["resolved"]["source"],
                "FR-13 已在正式前端落地，原型不该再写成「将来生成」",
            )

    def test_banner_matches_the_current_library_scope(self):
        """横幅曾长期写「库内 49 道菜、库外将来由 FR-13 生成」，两处都已过期。"""

        self.assertIn("seed_recipes.json", self.html, "横幅未说明库内菜谱的出处")
        self.assertNotIn("49 道", self.html, "菜谱库已扩到 75 道，横幅不该还写 49 道")
        self.assertNotIn("将来", self.html, "库里没有的菜已能由 FR-13 生成，不该再写「将来」")

    @unittest.skipUnless(MODEL_PATH.is_file(), "未转换 models/dish.onnx，跳过实模型比对")
    def test_topk_matches_real_model_output(self):
        """页面上的候选与置信度必须来自 models/dish.onnx 的真实输出。"""

        from prototype.preprocess import create_session, load_image, manifest

        preprocess = manifest()["preprocess"]
        session = create_session(MODEL_PATH)

        for photo in self.demo["photos"]:
            image_path = (PROTOTYPE_PATH.parent / photo["file"]).resolve()
            self.assertTrue(image_path.is_file(), "演示图缺失：%s" % image_path)

            batch = load_image(image_path, preprocess["mean"], preprocess["std"])
            logits = session.run(None, {"input": batch})[0][0]

            expected = softmax_topk(logits)
            actual = [(hit["index"], hit["confidence"]) for hit in photo["topk"]]

            self.assertEqual(
                [index for index, _ in actual],
                [index for index, _ in expected],
                "%s 的候选顺序与模型输出不一致" % photo["id"],
            )
            for (_, shown), (_, real) in zip(actual, expected):
                self.assertAlmostEqual(
                    shown,
                    real,
                    delta=CONFIDENCE_TOLERANCE,
                    msg="%s 的置信度与模型输出不一致（页面 %.4f / 模型 %.4f）"
                    % (photo["id"], shown, real),
                )

    @unittest.skipUnless(MODEL_PATH.is_file(), "未转换 models/dish.onnx，跳过重新生成比对")
    def test_committed_html_matches_generator(self):
        """页面必须还能由生成器原样重算出来，否则它就是一份会过期的快照。"""

        import importlib

        builder = importlib.import_module(BUILDER_MODULE)
        rendered = builder.render_dish_html(builder.build_demo_data())

        self.assertEqual(
            LATENCY_PATTERN.sub(r"\1<ms>", rendered),
            LATENCY_PATTERN.sub(r"\1<ms>", self.html),
            "prototype/dish.html 与生成器输出不一致，请重跑 "
            "python -m prototype.build_dish_prototype",
        )

    def test_no_placeholder_left(self):
        for pattern in PLACEHOLDER_PATTERNS:
            self.assertNotIn(pattern, self.html, "原型中残留占位符：%s" % pattern)


if __name__ == "__main__":
    unittest.main()