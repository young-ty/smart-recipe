# -*- coding: utf-8 -*-
"""仓库根目录 .env 的加载契约（大模型 API Key 就放这里）。

设计取舍见 `backend/app/core/env.py`：不引额外依赖、真实环境变量优先、只记名字不记值。
最后两条同时是**防泄漏**检查：.env 必须被 gitignore，仓库里不允许出现真的 Key。

运行：python -m pytest backend/tests/test_env_file.py -v
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core import env as env_module  # noqa: E402
from app.core.config import REPO_ROOT as CONFIG_REPO_ROOT  # noqa: E402
from app.core.config import Settings  # noqa: E402

REPO_ROOT = BACKEND_DIR.parent

# DeepSeek / OpenAI 风格的真实 Key 形如 sk-<32 位十六进制>
SECRET_PATTERN = re.compile(r"sk-[0-9a-fA-F]{16,}")

# 大文件与第三方目录：不可能放配置，扫它们只是浪费时间
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    "ONNX", "models", "training", "dist", "coverage",
}

# 本地配置文件本身要跳过：它们已经被 gitignore，本就不该进仓库
SKIP_FILES = {".env", ".env.local"}


def test_parse_env_supports_the_common_subset():
    pairs = env_module.parse_env(
        "\n".join([
            "# 注释行",
            "",
            "PLAIN=value",
            "  SPACED  =  spaced value  ",
            "export EXPORTED=1",
            'QUOTED="with space"',
            "SINGLE='single'",
            "WITH_EQUALS=a=b=c",
            "BROKEN_LINE",
            "=no_key",
        ])
    )
    assert pairs == {
        "PLAIN": "value",
        "SPACED": "spaced value",
        "EXPORTED": "1",
        "QUOTED": "with space",
        "SINGLE": "single",
        "WITH_EQUALS": "a=b=c",
    }


def test_load_env_file_fills_gaps_without_clobbering(monkeypatch, tmp_path):
    """真实环境变量优先：.env 只补空缺，方便部署时用系统变量覆盖本地文件。"""

    path = tmp_path / "sample.env"
    path.write_text("RECIPE_TEST_FILL=from-file\nRECIPE_TEST_KEEP=from-file\n", encoding="utf-8")
    monkeypatch.setenv("RECIPE_TEST_KEEP", "from-env")

    loaded = env_module.load_env_file(path)

    assert os.environ["RECIPE_TEST_FILL"] == "from-file"
    assert os.environ["RECIPE_TEST_KEEP"] == "from-env", "不该被 .env 覆盖"
    assert loaded == ["RECIPE_TEST_FILL"], "只应报告真正生效的变量"
    monkeypatch.delenv("RECIPE_TEST_FILL", raising=False)


def test_load_env_file_can_override_when_asked(monkeypatch, tmp_path):
    path = tmp_path / "sample.env"
    path.write_text("RECIPE_TEST_KEEP=from-file\n", encoding="utf-8")
    monkeypatch.setenv("RECIPE_TEST_KEEP", "from-env")

    loaded = env_module.load_env_file(path, override=True)

    assert os.environ["RECIPE_TEST_KEEP"] == "from-file"
    assert loaded == ["RECIPE_TEST_KEEP"]


def test_load_env_file_reports_names_never_values(tmp_path):
    """返回值只含变量名：日志里可以打它，不会把 Key 打出去。"""

    path = tmp_path / "sample.env"
    path.write_text("RECIPE_TEST_SECRET=sk-should-never-be-returned\n", encoding="utf-8")

    loaded = env_module.load_env_file(path)

    assert loaded == ["RECIPE_TEST_SECRET"]
    assert "sk-should-never-be-returned" not in " ".join(loaded)
    os.environ.pop("RECIPE_TEST_SECRET", None)


def test_load_env_file_tolerates_missing_and_broken_files(tmp_path):
    assert env_module.load_env_file(tmp_path / "not-there.env") == []

    broken = tmp_path / "broken.env"
    broken.write_bytes(b"\xff\xfe not utf-8")
    assert env_module.load_env_file(broken) == []


def test_default_paths_point_at_the_repo_root():
    """配置文件固定放在仓库根目录，且与 config.py 认定的根目录一致。"""

    assert env_module.REPO_ROOT == CONFIG_REPO_ROOT == REPO_ROOT
    assert env_module.DEFAULT_FILES == (".env", ".env.local")


def test_env_files_are_gitignored():
    lines = [line.strip() for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    for name in (".env", ".env.local"):
        assert name in lines, f"{name} 必须被忽略，否则 API Key 会被提交"


def test_env_example_carries_no_real_key():
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert SECRET_PATTERN.findall(example) == [], ".env.example 里不许出现真的 Key"
    assert "RECIPE_GENERATOR_API_KEY" in example, "示例文件要列出生成层需要的变量"


def test_env_example_matches_the_code_defaults(monkeypatch):
    """示例文件与 config.py 的默认值不许各写一套，否则照抄示例就连不上。"""

    example = env_module.parse_env((REPO_ROOT / ".env.example").read_text(encoding="utf-8"))
    monkeypatch.delenv("RECIPE_GENERATOR_MODEL", raising=False)
    monkeypatch.delenv("RECIPE_GENERATOR_BASE_URL", raising=False)
    monkeypatch.delenv("RECIPE_GENERATOR_TIMEOUT_SECONDS", raising=False)

    settings = Settings()

    assert example["RECIPE_GENERATOR_MODEL"] == settings.generator_model
    assert example["RECIPE_GENERATOR_BASE_URL"] == settings.generator_base_url
    assert (
        float(example["RECIPE_GENERATOR_TIMEOUT_SECONDS"])
        == settings.generator_timeout_seconds
    ), "示例里的超时值要与 config.py 的默认值一致，否则照抄示例就回到擦边超时"


def test_generator_timeout_is_configurable_without_code_change(monkeypatch):
    """超时要能靠环境变量调：模型变慢时不用改代码就能放开。

    背景：真实生成一份完整菜谱要 3~7 秒，早期写死的 6 秒会把正常生成误判成
    「生成服务不可用」，演示时表现为库外菜偶尔不出菜谱。
    """

    monkeypatch.delenv("RECIPE_GENERATOR_TIMEOUT_SECONDS", raising=False)
    assert Settings().generator_timeout_seconds >= 15.0, "默认超时太紧，慢一点的生成会被误判为不可用"

    monkeypatch.setenv("RECIPE_GENERATOR_TIMEOUT_SECONDS", "45")
    assert Settings().generator_timeout_seconds == 45.0

    monkeypatch.setenv("RECIPE_GENERATOR_TIMEOUT_SECONDS", "8.5")
    assert Settings().generator_timeout_seconds == 8.5


def test_env_example_key_is_left_empty():
    """示例文件里的 Key 必须是空值：留个占位符容易被误认为可以直接用。"""

    example = env_module.parse_env((REPO_ROOT / ".env.example").read_text(encoding="utf-8"))
    assert example["RECIPE_GENERATOR_API_KEY"] == ""


def test_readme_tells_the_user_how_to_configure_and_self_check():
    """文档契约：三个变量与自检脚本的入口必须在 README 里写清楚。"""

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for name in (
        "RECIPE_GENERATOR_API_KEY",
        "RECIPE_GENERATOR_BASE_URL",
        "RECIPE_GENERATOR_MODEL",
    ):
        assert name in readme, "README 未说明环境变量 %s" % name
    assert "tools.check_generator" in readme, "README 未给出生成层自检脚本的用法"
    assert "/api/health" in readme, "README 未提运行期自检入口"


def test_no_secret_leaked_into_repository_files():
    """兜底扫描：仓库里任何文本文件都不该出现形如 sk-<32 位> 的 Key。"""

    offenders = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".onnx", ".pt", ".pptx", ".db", ".jpg", ".png", ".webp"}:
            continue
        if path.name in SKIP_FILES:
            continue
        if SKIP_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if SECRET_PATTERN.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == [], "这些文件里出现了疑似真实 Key：%s" % offenders


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
