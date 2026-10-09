# -*- coding: utf-8 -*-
"""设计方案文档的契约测试。

校验 docs/项目设计方案.md 的章节完整性与需求编号一致性，
防止后续修改文档时出现章节丢失、占位符残留或需求编号悬空引用。

运行：python -m unittest tests.test_design_doc -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "项目设计方案.md"

EXPECTED_SECTIONS = [
    "修订记录",
    "目录",
    "1. 项目概述",
    "2. 需求分析",
    "3. 总体设计",
    "4. 前端设计",
    "5. 后端设计",
    "6. 数据设计",
    "7. 核心算法设计",
    "8. 项目结构",
    "9. 开发计划",
    "10. 测试与验收方案",
    "11. 风险与对策",
    "12. 附录",
]

PLACEHOLDER_PATTERNS = ["PATCH-MARKER", "TODO", "FIXME", "XXX", "<待补充>"]


def read_doc():
    return DOC_PATH.read_text(encoding="utf-8")


def headings(text):
    return re.findall(r"^## +(.+?) *$", text, flags=re.MULTILINE)


def section_body(text, heading_prefix):
    """返回指定二级章节的正文（到下一个二级标题为止）。"""
    pattern = r"^## +" + re.escape(heading_prefix) + r"[^\n]*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


class DesignDocContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read_doc()

    def test_doc_exists_and_readable(self):
        self.assertTrue(DOC_PATH.is_file(), "设计方案文档缺失：%s" % DOC_PATH)
        self.assertGreater(len(self.text.strip()), 1000, "文档内容过短，疑似未完成")

    def test_required_sections_present_in_order(self):
        actual = headings(self.text)
        positions = []
        for expected in EXPECTED_SECTIONS:
            self.assertIn(expected, actual, "缺少章节：%s" % expected)
            positions.append(actual.index(expected))
        self.assertEqual(positions, sorted(positions), "章节顺序与目录不一致")

    def test_no_placeholder_left(self):
        for pattern in PLACEHOLDER_PATTERNS:
            self.assertNotIn(
                pattern, self.text, "文档中残留占位符：%s" % pattern
            )

    def test_requirement_ids_are_defined(self):
        body = section_body(self.text, "2. 需求分析")
        self.assertTrue(body, "未找到「2. 需求分析」章节正文")
        defined = set(re.findall(r"\b(?:FR|NFR)-\d+\b", body))
        referenced = set(re.findall(r"\b(?:FR|NFR)-\d+\b", self.text))
        self.assertTrue(defined, "需求分析章节未定义任何需求编号")
        undefined = sorted(referenced - defined)
        self.assertEqual(undefined, [], "存在未定义的需求编号引用：%s" % undefined)

    def test_acceptance_table_covers_p0_requirements(self):
        body = section_body(self.text, "10. 测试与验收方案")
        self.assertTrue(body, "未找到「10. 测试与验收方案」章节正文")
        acceptance = set(re.findall(r"\bFR-\d+\b", body))
        self.assertEqual(
            sorted(set(["FR-01", "FR-02", "FR-03", "FR-04", "FR-05",
                        "FR-06", "FR-07", "FR-08", "FR-09"]) - acceptance),
            [],
            "验收标准未覆盖全部 P0 功能需求",
        )

    def test_code_fences_balanced(self):
        fences = len(re.findall(r"^```", self.text, flags=re.MULTILINE))
        self.assertEqual(fences % 2, 0, "代码块标记（```）数量为奇数，存在未闭合代码块")

    def test_training_ownership_is_documented(self):
        """训练与系统侧同属一个项目，概述里不能出现「训练方 / 组外」这类切开的说辞。"""

        body = section_body(self.text, "1. 项目概述")
        self.assertTrue(body, "未找到「1. 项目概述」章节正文")
        self.assertIn("接入", body, "项目概述未说明本项目负责模型接入")
        self.assertNotIn("组外", body, "项目概述把模型训练写成了组外承担")
        self.assertNotIn("训练方", body, "项目概述仍把训练与系统侧切成两方")

    def test_model_integration_contract_is_documented(self):
        body = section_body(self.text, "7. 核心算法设计")
        self.assertTrue(body, "未找到「7. 核心算法设计」章节正文")
        for heading in ("识别能力边界与类别体系", "模型接入契约", "识别服务化与降级",
                        "菜谱检索与生成的分流", "生成式菜谱生成与结构化校验"):
            self.assertIn(heading, body, "第 7 章缺少小节：%s" % heading)
        for keyword in ("208", "resnet50d", "ONNX", "labels.json", "crop_pct", "降级"):
            self.assertIn(keyword, body, "第 7 章缺少模型接入相关设计：%s" % keyword)

    def test_direction_is_dish_recognition(self):
        """v2.0 的方向切换必须写明：识别对象是成品菜，且给出取舍理由。"""

        overview = section_body(self.text, "1. 项目概述")
        self.assertIn("成品菜", overview, "项目概述未说明识别对象是成品菜")
        self.assertIn("### 1.5 方向取舍说明", self.text,
                      "缺少方向取舍说明小节：换方向的理由必须可追溯")
        match = re.search(
            r"^### 1\.5 [^\n]*\n(.*?)(?=^### |^## |\Z)",
            self.text,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match, "未找到「1.5 方向取舍说明」正文")
        tradeoff = match.group(1)
        for keyword in ("需求分析", "实现难度", "公开数据集"):
            self.assertIn(keyword, tradeoff, "取舍说明未覆盖评估角度：%s" % keyword)

    def test_positioning_lands_on_the_capture_scenario(self):
        """定位落在使用场景上：吃到的菜先拍照记录、再一键扒谱，而不是只讲「识别一张图」。"""

        overview = section_body(self.text, "1. 项目概述")
        for keyword in ("拍照记录", "一键扒谱", "复刻"):
            self.assertIn(keyword, overview, "项目概述未写清使用场景：%s" % keyword)

        scenario = re.search(
            r"^\*\*主场景（Happy Path）\*\* *\n(.*?)(?=^### |^## |\Z)",
            self.text,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(scenario, "未找到「主场景（Happy Path）」说明")
        self.assertIn("拍一张照片", scenario.group(1), "主场景不是从「先拍一张」开始")

    def test_retired_interfaces_are_marked(self):
        """退役的接口与映射表要显式标注，否则会被当成漏实现。"""

        for keyword in ("/api/recommendations", "ingredient_mapping.json", "退役"):
            self.assertIn(keyword, self.text, "未标注退役的组成部分：%s" % keyword)

    def test_upload_compression_is_documented(self):
        """上传前压缩是 FR-01 的一部分，阈值与实现文件必须在设计方案里查得到。"""

        requirements = section_body(self.text, "2. 需求分析")
        self.assertIn("1.5 MB", requirements, "FR-01 未写明上传前压缩的触发阈值")

        body = section_body(self.text, "4. 前端设计")
        self.assertTrue(body, "未找到「4. 前端设计」章节正文")
        for keyword in ("上传前压缩", "1.5 MB", "1600", "25 MB", "image-compress.ts"):
            self.assertIn(keyword, body, "第 4 章缺少上传前压缩的设计：%s" % keyword)

if __name__ == "__main__":
    unittest.main()
