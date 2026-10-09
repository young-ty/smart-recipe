# -*- coding: utf-8 -*-
"""生成式推荐接入方案的契约测试。

校验 docs/生成式推荐接入方案.md 把几件容易在实现时被悄悄丢掉的事写死了：
大模型只做生成层、输出必须结构化校验、视频链接由后端拼接而不采信模型直出的 URL、
无 API Key 时必须降级且契约不变。同时确认方案引用的需求编号真的在
docs/项目设计方案.md 第 2 章里有定义，避免出现悬空引用。

运行：python -m unittest tests.test_llm_plan_doc -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "生成式推荐接入方案.md"
DESIGN_DOC_PATH = REPO_ROOT / "docs" / "项目设计方案.md"

PLACEHOLDER_PATTERNS = ["待填", "TODO", "FIXME", "XXX", "<待补充>"]

# 方案覆盖的需求编号（必须同时在设计方案第 2 章里有定义）
SCOPED_REQUIREMENTS = ("FR-13", "FR-14")


class LlmPlanDocContractTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = DOC_PATH.read_text(encoding="utf-8")
        cls.design_text = DESIGN_DOC_PATH.read_text(encoding="utf-8")

    def test_doc_exists_and_readable(self):
        self.assertTrue(DOC_PATH.is_file(), "方案文档缺失：%s" % DOC_PATH)
        self.assertGreater(len(self.text.strip()), 1500, "文档内容过短，疑似未完成")

    def test_no_placeholder_left(self):
        for pattern in PLACEHOLDER_PATTERNS:
            self.assertNotIn(pattern, self.text, "文档中残留占位符：%s" % pattern)

    def test_scoped_requirements_are_defined_in_design_doc(self):
        """方案引用的需求编号必须在设计方案第 2 章有定义，否则是悬空引用。"""
        section = re.search(
            r"^### 2\.1 功能需求.*?(?=^### )",
            self.design_text,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(section, "设计方案中未找到「2.1 功能需求」章节")
        for req in SCOPED_REQUIREMENTS:
            self.assertIn(
                req, self.text, "方案未声明覆盖需求 %s" % req
            )
            self.assertIn(
                req, section.group(0), "需求 %s 未在设计方案第 2 章定义" % req
            )

    def test_llm_stays_out_of_candidate_selection(self):
        """候选与排序必须仍由检索决定，否则推荐列表会在刷新间抖动。"""
        for keyword in ("生成层", "检索", "候选"):
            self.assertIn(keyword, self.text, "方案未限定大模型的职责边界：%s" % keyword)
        self.assertIn(
            "recipe_resolver.py", self.text, "方案未指明菜谱检索作为库内候选来源"
        )

    def test_structured_output_is_validated(self):
        for keyword in ("Pydantic", "JSON", "校验"):
            self.assertIn(keyword, self.text, "方案缺少结构化输出约束：%s" % keyword)

    def test_video_url_is_built_by_backend(self):
        """链接必须由后端拼接，模型直出的 URL 一律判不合格。"""
        self.assertIn("search.bilibili.com", self.text, "方案未给出视频链接模板")
        self.assertIn("拼接", self.text, "方案未说明链接由后端拼接")
        self.assertRegex(
            self.text,
            r"(不采信|判.{0,4}不合格|直接降级)",
            "方案未说明如何处理模型直出的 URL",
        )

    def test_degradation_is_specified(self):
        """无 Key、超时、校验失败都必须降级到种子数据，且接口契约不变。"""
        for keyword in ("未配置 API Key", "超时", "degraded", "seed", "接口契约不能变"):
            self.assertIn(keyword, self.text, "方案缺少降级约定：%s" % keyword)

    def test_cache_key_is_versioned(self):
        for keyword in ("缓存", "prompt_version", "sha1"):
            self.assertIn(keyword, self.text, "方案缺少缓存约定：%s" % keyword)

    def test_existing_latency_budget_is_preserved(self):
        """列表页不调用这条接口，NFR-01 的毫秒级指标才不会被拖垮。"""
        self.assertIn("NFR-01", self.text, "方案未声明对既有性能指标的影响")
        self.assertIn(
            "不调用", self.text, "方案未声明推荐列表页不调用该接口"
        )
        self.assertIn("8 s", self.text, "方案未给出这条接口自身的耗时预算")

    def test_privacy_constraints_are_stated(self):
        for keyword in ("不发送用户上传的照片", "环境变量", ".gitignore"):
            self.assertIn(keyword, self.text, "方案缺少隐私约束：%s" % keyword)

    def test_acceptance_criteria_are_decidable(self):
        """验收标准必须可判定，否则无法在交付时判断是否达标。"""
        for tag in ("A1", "A2", "A3", "A4", "A5"):
            self.assertIn(tag, self.text, "方案缺少验收项：%s" % tag)
        for keyword in ("degraded=true", "95%"):
            self.assertIn(keyword, self.text, "验收标准缺少可判定的门槛：%s" % keyword)

    def test_open_questions_and_cut_order_are_listed(self):
        self.assertIn("待确认项", self.text, "方案未列出待确认项")
        self.assertIn("裁剪", self.text, "方案未给出时间不足时的裁剪顺序")


if __name__ == "__main__":
    unittest.main()