# -*- coding: utf-8 -*-
"""模型接入说明文档的契约测试。

校验 docs/模型接入说明.md 与 models/dish.manifest.json 里实际记录的信息一致，
防止文档里的权重信息（文件名、体积、SHA-256、架构、类别数、预处理参数）
随权重换代而失效，或占位符「待填」被遗留到文档里。

运行：python -m unittest tests.test_model_handoff_doc -v
"""

import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "模型接入说明.md"
MANIFEST_PATH = REPO_ROOT / "models" / "dish.manifest.json"
SOURCE_WEIGHTS = REPO_ROOT / "models" / "best.pt"

PLACEHOLDER_PATTERNS = ["待填", "TODO", "FIXME", "XXX", "<待补充>"]


class ModelHandoffDocContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC_PATH.read_text(encoding="utf-8")
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_doc_exists_and_readable(self):
        self.assertTrue(DOC_PATH.is_file(), "模型接入说明缺失：%s" % DOC_PATH)
        self.assertGreater(len(self.text.strip()), 1000, "文档内容过短，疑似未完成")

    def test_no_placeholder_left(self):
        for pattern in PLACEHOLDER_PATTERNS:
            self.assertNotIn(pattern, self.text, "文档中残留占位符：%s" % pattern)

    def test_doc_declares_source_checkpoint(self):
        """原始权重的文件名与 SHA-256 必须与 manifest 一致。"""

        self.assertIn(self.manifest["source_file"], self.text, "文档未记录原始权重文件名")
        self.assertIn(
            self.manifest["source_sha256"], self.text, "文档未记录或记录了错误的原始权重 SHA-256"
        )

    @unittest.skipUnless(SOURCE_WEIGHTS.is_file(), "原始 checkpoint 不在本机，跳过体积比对")
    def test_doc_source_size_matches_actual_checkpoint(self):
        """文档写的原始权重体积必须等于本机文件的实际大小。"""

        size = SOURCE_WEIGHTS.stat().st_size
        self.assertIn(
            "{:,}".format(size),
            self.text,
            "文档未按实际文件记录原始权重体积（%d 字节）" % size,
        )

    def test_doc_declares_onnx_artifact(self):
        """转换产物的文件名、体积与校验和都必须落在文档里。"""

        self.assertIn(self.manifest["onnx_file"], self.text, "文档未记录转换产物文件名")
        self.assertIn(
            self.manifest["onnx_sha256"], self.text, "文档未记录或记录了错误的 ONNX SHA-256"
        )
        self.assertIn(
            "{:,}".format(self.manifest["onnx_size_bytes"]),
            self.text,
            "文档未按 manifest 记录 ONNX 体积（%d 字节）" % self.manifest["onnx_size_bytes"],
        )

    def test_doc_declares_arch_and_class_count(self):
        self.assertIn(self.manifest["arch"], self.text, "文档未记录模型架构")
        self.assertIn(str(self.manifest["num_classes"]), self.text, "文档未记录类别数")

    def test_doc_declares_preprocess_facts(self):
        """预处理关键项必须与 manifest 的 preprocess 字段一致。"""

        preprocess = self.manifest["preprocess"]
        for keyword in (
            preprocess["interpolation"],
            preprocess["layout"],
            preprocess["color_order"],
            str(preprocess["input_size"][-1]),
            str(preprocess["crop_pct"]),
        ):
            self.assertIn(keyword, self.text, "文档未记录预处理参数：%s" % keyword)
        for value in preprocess["mean"] + preprocess["std"]:
            self.assertIn(str(value), self.text, "文档未记录归一化参数：%s" % value)

    def test_doc_documents_crop_rounding_traps(self):
        """裁剪起点的取整方式曾经让置信度漂移，这个坑必须写在文档里。"""

        for keyword in ("int()", "round", "crop_pct"):
            self.assertIn(keyword, self.text, "文档未说明预处理对齐细节：%s" % keyword)

    def test_doc_quantifies_recipe_coverage_gap(self):
        """类别数与菜谱库的覆盖缺口是本期最关键的约束，必须给出数字。"""

        for keyword in ("208", "49", "承接"):
            self.assertIn(keyword, self.text, "文档未量化类别与菜谱库的覆盖情况：%s" % keyword)

    def test_doc_records_superseded_weight(self):
        """旧方向的权重已经退役，不能让人误以为它还在链路上。"""

        self.assertIn("FoodInsSeg", self.text, "文档未记录被取代的旧方向权重")
        self.assertIn("取代", self.text, "文档未说明旧方向权重已被取代")

    def test_doc_keeps_outstanding_deliverables_visible(self):
        """仍未收到的资产必须仍然标注为待提供，不能悄悄变成已完成。"""

        self.assertIn("样例", self.text, "文档未列出「样例」资产")
        self.assertIn("指标报告", self.text, "文档未列出「指标报告」资产")
        self.assertIn("待提供", self.text, "未收到的资产应标注为待提供")
        self.assertIn("labels.json", self.text, "文档未说明类别清单仍待提供")


if __name__ == "__main__":
    unittest.main()
