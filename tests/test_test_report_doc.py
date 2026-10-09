# -*- coding: utf-8 -*-
"""测试报告（docs/测试报告.md）的契约测试。

报告里的数字是验收结论的出处，所以这里守三件事：
引用的文件与命令必须真实存在、结论必须满足 NFR-01 / NFR-06 的标准、
80% 这条线必须在配置里真的能拦住（否则报告只是自我声明）。

运行：python -m unittest tests.test_test_report_doc -v
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "docs" / "测试报告.md"

EXPECTED_SECTIONS = [
    "1. 结论摘要",
    "2. 测试分层与规模",
    "3. 覆盖率（NFR-06）",
    "4. 性能压测（NFR-01）",
    "5. 验收标准逐条对照（设计方案 10.3）",
    "6. 已知缺口与后续",
]

PLACEHOLDER_PATTERNS = ["PATCH-MARKER", "TODO", "FIXME", "XXX", "<待补充>"]

# 报告点名引用的文件：任何一个被删掉或改名，报告就不再可复现
REFERENCED_PATHS = [
    "docs/项目设计方案.md",
    "docs/生成式推荐接入方案.md",
    "README.md",
    "backend/tools/perf_probe.py",
    "backend/tools/coverage_report.py",
    "backend/tests/test_perf_probe_tool.py",
    "frontend/vite.config.ts",
    ".coveragerc",
]


def read_report():
    return REPORT_PATH.read_text(encoding="utf-8")


class TestReportContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read_report()

    def test_report_exists_and_has_sections_in_order(self):
        self.assertTrue(REPORT_PATH.is_file(), "测试报告缺失：%s" % REPORT_PATH)
        headings = re.findall(r"^## +(.+?) *$", self.text, flags=re.MULTILINE)
        positions = []
        for expected in EXPECTED_SECTIONS:
            self.assertIn(expected, headings, "测试报告缺少章节：%s" % expected)
            positions.append(headings.index(expected))
        self.assertEqual(positions, sorted(positions), "章节顺序与预期不一致")

    def test_no_placeholder_left(self):
        for pattern in PLACEHOLDER_PATTERNS:
            self.assertNotIn(pattern, self.text, "报告中残留占位符：%s" % pattern)

    def test_referenced_files_exist(self):
        # 命令里写的是 Windows 反斜杠路径，两种写法都算登记过
        backslash = chr(92)
        for relative in REFERENCED_PATHS:
            windows_form = relative.replace("/", backslash)
            self.assertTrue(
                relative in self.text or windows_form in self.text,
                "报告中未登记：%s" % relative,
            )
            self.assertTrue(
                (REPO_ROOT / relative).exists(), "报告引用了不存在的文件：%s" % relative
            )

    def test_summary_numbers_meet_the_acceptance_criteria(self):
        """摘要表里的数字必须自己站得住，改文案时不能把超标值写进去。"""

        budgets = [float(value) for value in re.findall(r"P95 \*\*([\d.]+) ms\*\*", self.text)]
        self.assertEqual(len(budgets), 2, "摘要表应同时给出识别与检索的 P95")
        self.assertLessEqual(budgets[0], 500.0, "识别 P95 超出 NFR-01 的 500 ms")
        self.assertLessEqual(budgets[1], 200.0, "检索 P95 超出 NFR-01 的 200 ms")

        percents = [float(value) for value in re.findall(r"\*\*([\d.]+)%\*\*", self.text)]
        self.assertTrue(percents, "摘要表应给出覆盖率百分比")
        self.assertGreaterEqual(min(percents), 80.0, "覆盖率低于 NFR-06 的 80%")

    def test_skipped_tests_are_explained(self):
        """跳过的用例必须写明原因，不能让人把「跳过」读成「通过」。"""

        self.assertIn("跳过", self.text)
        self.assertIn("RECIPE_LIVE_TESTS", self.text, "未说明大模型实测用例怎么手动跑")

    def test_coverage_thresholds_are_actually_enforced(self):
        """报告声明 80% 是线，配置里就得真有一条线。"""

        coveragerc = (REPO_ROOT / ".coveragerc").read_text(encoding="utf-8")
        self.assertIn("fail_under = 80", coveragerc, "后端覆盖率没有设 fail_under")

        vite_config = (REPO_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("thresholds", vite_config, "前端覆盖率没有设 thresholds")
        self.assertIn("lines: 80", vite_config)

    def test_report_commands_exist_in_package_scripts(self):
        """报告让人跑 npm run test:coverage，这个脚本就必须真的在。"""

        package = json.loads(
            (REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
        )
        self.assertIn("test:coverage", package["scripts"])
        self.assertIn("npm run test:coverage", self.text)

    def test_frontend_coverage_number_matches_the_last_run(self):
        """报告里的前端行覆盖必须与最近一次 test:coverage 的产物一致（没有产物就跳过）。"""

        summary_path = (
            REPO_ROOT / ".codex-build" / "coverage-frontend" / "coverage-summary.json"
        )
        if not summary_path.is_file():
            self.skipTest("尚未生成前端覆盖率产物，跳过口径核对")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        lines = summary["total"]["lines"]["pct"]
        self.assertIn(
            "行 **%s%%**" % lines,
            self.text,
            "报告里的前端行覆盖与最近一次实测不一致，需要同步 docs/测试报告.md",
        )


if __name__ == "__main__":
    unittest.main()
