"""启动预热测试（设计方案 7.3）。

不预热时，模型加载的开销落在第一个用户请求上，而且发生在依赖解析阶段——
接口返回的 elapsed_ms 里看不到。所以这里锁住：应用建起来时就把识别器准备好，
关掉预热时则明确跳过。

用例里显式写 warm_up_on_start，不看环境变量：`Settings` 的默认值会读
RECIPE_WARMUP，本机设了这个变量的话测试就得跟着飘。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import main as app_main  # noqa: E402
from app.core.config import Settings  # noqa: E402


def settings_with(settings: Settings, **overrides) -> Settings:
    """在夹具配置上覆盖几个字段，其余（临时库、空模型目录）保持不变。"""

    values = {
        "database_url": settings.database_url,
        "models_dir": settings.models_dir,
        "generator_api_key": "",
    }
    values.update(overrides)
    return Settings(**values)


def spy_on_recognizer(monkeypatch):
    calls = []
    real = app_main.get_recognizer

    def spy(settings):
        calls.append(settings)
        return real(settings)

    monkeypatch.setattr(app_main, "get_recognizer", spy)
    return calls


def test_app_warms_up_the_recognizer_on_startup(settings, monkeypatch):
    calls = spy_on_recognizer(monkeypatch)
    app_main.create_app(settings_with(settings, warm_up_on_start=True))
    assert len(calls) == 1


def test_warm_up_can_be_switched_off(settings, monkeypatch):
    calls = spy_on_recognizer(monkeypatch)
    app_main.create_app(settings_with(settings, warm_up_on_start=False))
    assert calls == []


def test_warm_up_survives_a_missing_model(settings):
    """模型没交付时预热也不能让应用建不起来：降级为模拟识别是正常路径。"""

    app = app_main.create_app(settings_with(settings, warm_up_on_start=True))
    recognizer = app_main.get_recognizer(app.state.settings)
    assert recognizer.degraded is True
    assert recognizer.primary is None
    assert recognizer.providers == ()
