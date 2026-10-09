# -*- coding: utf-8 -*-
"""演示启动脚本的契约测试。

一键启动/一键停止是演示当天的入口，坏掉的话代价很大（现场手敲两条命令、端口被占、忘了填 .env）。
这里把它的几个关键约定钉住：脚本要在、自检不过就不启动、.bat 要绕过执行策略、
已经起着的服务不要重复起、停止脚本别误杀别人的进程、README 要写清楚。

运行：python -m unittest tests.test_demo_launcher -v
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PS1 = REPO_ROOT / "start-demo.ps1"
BAT = REPO_ROOT / "start-demo.bat"
STOP_PS1 = REPO_ROOT / "stop-demo.ps1"
STOP_BAT = REPO_ROOT / "stop-demo.bat"
PREFLIGHT = REPO_ROOT / "backend" / "tools" / "preflight.py"
README = REPO_ROOT / "README.md"


class DemoLauncherTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ps1 = PS1.read_text(encoding="utf-8-sig")
        cls.bat = BAT.read_text(encoding="utf-8")
        cls.stop_ps1 = STOP_PS1.read_text(encoding="utf-8-sig")
        cls.stop_bat = STOP_BAT.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_launcher_and_preflight_exist(self):
        for path in (PS1, BAT, STOP_PS1, STOP_BAT, PREFLIGHT):
            self.assertTrue(path.is_file(), "缺少 %s" % path)

    def test_launcher_runs_the_preflight_and_stops_on_failure(self):
        self.assertIn("preflight.py", self.ps1, "启动前必须先自检")
        self.assertIn("$LASTEXITCODE -ne 0", self.ps1, "自检失败要停在原地")
        self.assertIn("exit 1", self.ps1)

    def test_launcher_starts_both_services(self):
        self.assertIn("uvicorn", self.ps1, "要起后端")
        self.assertIn("npm run dev", self.ps1, "要起前端")
        self.assertIn("8000", self.ps1)
        self.assertIn("5173", self.ps1)

    def test_launcher_has_a_check_only_mode(self):
        self.assertIn("-Check", self.ps1, "要能只自检不启动")
        self.assertIn("if ($Check) { exit 0 }", self.ps1)

    def test_bat_bypasses_the_execution_policy(self):
        """Windows 默认禁止跑 .ps1；.bat 是给「不想先改执行策略」的人用的。"""

        self.assertIn("-ExecutionPolicy Bypass", self.bat)
        self.assertIn("start-demo.ps1", self.bat)
        self.assertIn("%*", self.bat, "要把参数透传下去，-Check 才能用")

    def test_bats_stay_open_on_failure_for_double_click(self):
        """双击 .bat 时窗口会随脚本结束一起关掉，自检 FAIL 的提示必须停住等按键。"""

        for name, text in (("start-demo.bat", self.bat), ("stop-demo.bat", self.stop_bat)):
            self.assertIn("pause", text, "%s 失败时把窗口关掉，提示来不及看" % name)
            self.assertIn("errorlevel 1", text, "%s 应只在失败时停住" % name)

    def test_launcher_skips_services_that_are_already_running(self):
        """重复双击不该起两套：后端撞 8000，前端因为 strictPort 会直接失败。"""

        self.assertIn("Test-PageReady", self.ps1, "要先探测服务是否已经在跑")
        self.assertIn("已在运行", self.ps1, "已在运行时要明确跳过，而不是硬起")
        self.assertIn("8000", self.ps1)
        self.assertIn("5173", self.ps1)

    def test_stop_script_only_targets_our_own_processes(self):
        """端口上可能是别人的程序，停止脚本不能无脑杀。"""

        for keyword in ("8000", "5173", "uvicorn", "vite", "CommandLine"):
            self.assertIn(keyword, self.stop_ps1, "停止脚本缺少判断依据：%s" % keyword)
        self.assertIn("IsOurs", self.stop_ps1, "要区分本项目与非本项目的进程")
        self.assertIn("非本项目", self.stop_ps1, "遇到别人的进程要提示而不是结束")
        # 默认先确认，-Force 才不打扰
        self.assertIn("Read-Host", self.stop_ps1)
        self.assertIn("-Force", self.stop_ps1)
        self.assertIn("-ListOnly", self.stop_ps1)

    def test_stop_bat_bypasses_the_execution_policy(self):
        self.assertIn("-ExecutionPolicy Bypass", self.stop_bat)
        self.assertIn("stop-demo.ps1", self.stop_bat)
        self.assertIn("-Force", self.stop_bat, "双击 .bat 时不该再弹确认")
        self.assertIn("%*", self.stop_bat)

    def test_readme_documents_the_demo_entry(self):
        for keyword in ("start-demo.bat", "start-demo.ps1 -Check", "preflight.py"):
            self.assertIn(keyword, self.readme, "README 未说明演示入口：%s" % keyword)

    def test_readme_documents_the_stop_entry(self):
        for keyword in ("stop-demo.bat", "stop-demo.ps1 -ListOnly", "strictPort"):
            self.assertIn(keyword, self.readme, "README 未说明收尾方式：%s" % keyword)


if __name__ == "__main__":
    unittest.main()
