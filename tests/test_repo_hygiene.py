# -*- coding: utf-8 -*-
"""仓库卫生测试：体积大的模型文件不能进版本库，散落的训练产物要有固定去处。

早期版本里 `ONNX/best.onnx` 有 335 MB、`ONNX.zip` 有 326 MB（这两个文件已在
`models/dish.onnx` 归档校验通过后删除，但规则要留着），一旦被 `git add` 进来，仓库就没法用了。
这里用 .gitignore 的规则本身作为契约：权重（`*.onnx` / `*.pt`）与压缩包（`*.zip`）永远走 Release / 网盘分发。

第二组契约盯的是目录布局：训练脚本与结果图统一放在 `training/` 下，
本地空白模板放在 `templates/`（两者体积大或不属项目代码，都不进版本库）。
原先散在根目录的训练脚本、微信图片名与 `1/` 目录在这里被挡住，避免下次又被放回根目录。

第三组契约盯的是仓库门面：LICENSE 与 CI 工作流要真在仓库里而不是只写在 README 上，
被跟踪的文件里不允许出现学号与组别编号，也不允许出现校内过程痕迹。

运行：python -m unittest tests.test_repo_hygiene -v
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = REPO_ROOT / ".gitignore"

# 交付物里出现过、且必须被忽略的扩展名
REQUIRED_IGNORES = ["*.onnx", "*.pt", "*.zip"]

# 本机跑覆盖率 / 压测产生的中间产物：能被复现，不该进版本库
LOCAL_BUILD_ARTIFACTS = [".coverage", "coverage/", ".codex-build/"]

# docs/ 下偶尔会放进录制视频与打包材料，规则要挡住，免得顺手 git add -A 传上去
LOCAL_RECORDING_ARTIFACTS = ["docs/*.mp4", "docs/*.rar"]

# 个人简历含手机号、邮箱与证件照，只在本机投递用；这个仓库是公开的，规则必须挡住
PERSONAL_DOC_ARTIFACTS = ["简历-*.docx", "简历-*.pdf"]

# 训练侧代码与结果图的固定去处：根目录只放项目级入口，训练产物一律进 training/
TRAINING_CODE = [
    "training/train.py",
    "training/pilot.py",
    "training/figures",
    "training/convnext-vs-resnet50d.html",
]

# 退役位置：旧方向的训练输出曾经直接放在根目录下，规则要挡住，免得下次又被放回去
RETIRED_PATHS = ["1"]

# 旧方向的训练输出目录：移出版本库之后不要再放回来
RETIRED_TRAINING_OUTPUT = "training/yolo-legacy"

# 仓库门面：许可与持续集成要真在仓库里，不能只是 README 里的一句话
LICENSE_PATH = REPO_ROOT / "LICENSE"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CI_REQUIRED_COMMANDS = ["pytest backend/tests", "pytest tests", "npm test"]

# 公开仓库对外讲的是个人项目：他人姓名、学号、组别编号与校内过程痕迹都不该出现在被跟踪的文件里。
# 待挡的字符串用码位写出来，守卫清单本身才不会把内容带进仓库。
PRIVATE_IDENTIFIERS = [
    "\u5434\u653f\u8c26",
    "\u6bdb\u70e8\u8d85",
    "\u0032\u0033\u0032\u0031\u0031\u0038\u0038\u0030",
    "\u7b2c 5 \u7ec4",
    "\u7b2c5\u7ec4",
    "\u8bfe\u7a0b",
    "\u7b54\u8fa9",
    "\u4e2d\u671f\u62a5\u544a",
    "\u5b9e\u8df5\u62a5\u544a",
    "\u5c0f\u7ec4",
    "\u56e2\u961f",
    "\u4ea4\u4f5c\u4e1a",
]

# 只扫文本类文件，权重与图片跳过；空扩展名留给 LICENSE 这类无后缀文件
TEXT_SUFFIXES = {
    "",
    ".css",
    ".csv",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".webmanifest",
    ".yaml",
    ".yml",
}


class RepoHygieneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = [
            line.strip()
            for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_gitignore_keeps_bulky_handover_artifacts_out(self):
        for pattern in REQUIRED_IGNORES:
            self.assertIn(
                pattern,
                self.lines,
                "权重与压缩包必须继续被忽略，否则 335 MB 的 ONNX / 326 MB 的 zip 会被误提交",
            )

    def test_gitignore_keeps_local_build_artifacts_out(self):
        """覆盖率与压测产物是可复现的中间件，进来只会把仓库撑大。"""

        for pattern in LOCAL_BUILD_ARTIFACTS:
            self.assertIn(
                pattern, self.lines, "本机构建产物必须被忽略：%s" % pattern
            )

    def test_gitignore_keeps_local_recording_artifacts_out(self):
        """录制视频与打包材料留在本地，仓库对外只给代码与项目文档。"""

        for pattern in LOCAL_RECORDING_ARTIFACTS:
            self.assertIn(pattern, self.lines, "本地录制产物必须被忽略：%s" % pattern)

    def test_gitignore_keeps_personal_resumes_out(self):
        """简历带手机号与证件照，公开仓库里一旦出现就收不回来。"""

        for pattern in PERSONAL_DOC_ARTIFACTS:
            self.assertIn(pattern, self.lines, "个人简历必须被忽略：%s" % pattern)

    def test_gitignore_keeps_the_delivered_label_sheet_in(self):
        """类别清单是纯文本、且被文档引用，不能被任何忽略规则扫掉。"""

        ignored = [pattern for pattern in self.lines if pattern.endswith(".csv")]
        self.assertEqual(ignored, [], "类别清单 CSV 不应被忽略：%s" % ignored)

    def test_training_side_lives_under_one_directory(self):
        """训练脚本与结果图集中在 training/：根目录只留项目级入口，便于交接与查证。"""

        for relative in TRAINING_CODE:
            self.assertTrue(
                (REPO_ROOT / relative).exists(),
                "训练侧文件不在约定位置：%s" % relative,
            )
        figures = sorted((REPO_ROOT / "training" / "figures").glob("*.png"))
        self.assertEqual(len(figures), 4, "报告引用的四张结果图应留在 training/figures/")

    def test_training_scripts_are_not_back_in_the_root(self):
        """根目录不允许再出现训练脚本、实验 HTML 与微信默认图片名。"""

        for name in ("train.py", "pilot.py", "convnext-vs-resnet50d.html"):
            self.assertFalse(
                (REPO_ROOT / name).exists(), "训练文件又放回根目录了：%s" % name
            )
        strays = [
            path.name
            for path in REPO_ROOT.glob("*.png")
            if path.name.startswith("微信图片")
        ]
        self.assertEqual(strays, [], "根目录残留了未命名的实验截图：%s" % strays)

    def test_retired_locations_are_gone_and_still_ignored(self):
        """退役位置的规则要跟着文件一起搬走，否则下次又会有人往那儿放东西。"""

        for name in RETIRED_PATHS:
            self.assertFalse((REPO_ROOT / name).exists(), "退役路径又出现了：%s" % name)

        self.assertIn("templates/", self.lines, "空白模板目录应整目录忽略")
        stale = [
            pattern
            for pattern in self.lines
            if pattern.startswith(("1/", "training/yolo-legacy"))
        ]
        self.assertEqual(stale, [], "旧位置的忽略规则未清理：%s" % stale)

    def test_retired_training_output_stays_out(self):
        """旧方向的训练输出已移出版本库，不要再放回来。"""

        self.assertFalse(
            (REPO_ROOT / RETIRED_TRAINING_OUTPUT).exists(),
            "旧方向的训练输出又回到仓库：%s" % RETIRED_TRAINING_OUTPUT,
        )

    def test_license_file_matches_the_readme_claim(self):
        """README 说了以 MIT 授权，就得真有这份正文，否则是空头承诺。"""

        self.assertTrue(LICENSE_PATH.is_file(), "缺少许可文件：%s" % LICENSE_PATH)
        text = LICENSE_PATH.read_text(encoding="utf-8")
        self.assertIn("MIT License", text, "LICENSE 不是 MIT 正文")
        self.assertIn("Copyright (c)", text, "LICENSE 缺少版权行")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("LICENSE", readme, "README 没有指向许可文件")

    def test_tracked_files_carry_no_private_or_course_traces(self):
        """对外口径是个人项目：他人姓名、学号、组别编号与校内过程痕迹都不得出现在被跟踪的文件里。"""

        tracked = (
            subprocess.run(
                ["git", "ls-files", "-z"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                check=True,
            )
            .stdout.decode("utf-8", "replace")
            .split("\0")
        )
        offenders = {}
        for relative in tracked:
            if not relative:
                continue
            path = REPO_ROOT / relative
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in PRIVATE_IDENTIFIERS:
                if token in text:
                    escaped = token.encode("unicode_escape").decode("ascii")
                    offenders.setdefault(relative, []).append(escaped)
        self.assertEqual(offenders, {}, "被跟踪的文件里出现了不该公开的标识：%s" % offenders)

    def test_ci_workflow_covers_all_three_suites(self):
        """门面之外 CI 要真跑测试：后端、仓库契约、前端三套缺一不可。"""

        self.assertTrue(CI_WORKFLOW.is_file(), "缺少 CI 工作流：%s" % CI_WORKFLOW)
        text = CI_WORKFLOW.read_text(encoding="utf-8")
        for command in CI_REQUIRED_COMMANDS:
            self.assertIn(command, text, "CI 未覆盖的命令：%s" % command)
        self.assertIn("runs-on: ubuntu-latest", text, "CI 没写运行环境")

if __name__ == "__main__":
    unittest.main()
