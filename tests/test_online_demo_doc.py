# -*- coding: utf-8 -*-
"""在线演示站（GitHub Pages）的契约测试。

演示站是要给出去的链接，README 与设计方案里都得写清发布地址，
而且两处只能指向同一个站点：发布之后再改时只改了一处，就会出现两套口径。

顺带守住发布链路本身（工作流要在、要真的构建演示版），
至于仓库设置里把 Pages 来源选成 GitHub Actions 这一步，离线测试查不了，只在文档里留痕。

运行：python -m unittest tests.test_online_demo_doc -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / 'README.md'
DESIGN_DOC_PATH = REPO_ROOT / 'docs' / '项目设计方案.md'
WORKFLOW_PATH = REPO_ROOT / '.github' / 'workflows' / 'deploy-pages.yml'

ONLINE_DEMO_URL = 'https://young-ty.github.io/smart-recipe/'


class OnlineDemoDocContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = README_PATH.read_text(encoding='utf-8')
        cls.design = DESIGN_DOC_PATH.read_text(encoding='utf-8')

    def test_readme_points_at_the_live_site(self):
        self.assertIn(ONLINE_DEMO_URL, self.readme, 'README 未登记在线演示地址')

    def test_design_doc_records_the_published_address(self):
        """4.7 是发布口径的出处，地址要在这里查得到。"""

        self.assertIn(ONLINE_DEMO_URL, self.design, '设计方案未登记演示站发布地址')

    def test_the_two_docs_do_not_drift_apart(self):
        """两个文档里出现的 Pages 地址必须是同一个，只改一处就会被抓到。"""

        found = set(
            re.findall(
                r'https://[a-z0-9-]+\.github\.io/[^\s<>)]+', self.readme + self.design
            )
        )
        self.assertEqual(
            found, {ONLINE_DEMO_URL}, '文档里出现了别的 Pages 地址：%s' % sorted(found)
        )

    def test_pages_workflow_builds_the_demo_bundle(self):
        self.assertTrue(WORKFLOW_PATH.is_file(), '缺少演示站发布工作流：%s' % WORKFLOW_PATH)
        text = WORKFLOW_PATH.read_text(encoding='utf-8')
        self.assertIn('npm run build:demo', text, '工作流没有构建演示版')
        self.assertIn('main', text, '工作流未覆盖主分支')


if __name__ == '__main__':
    unittest.main()
