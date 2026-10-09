r"""生成层真实调用验收：**默认整文件跳过**，只在显式打开开关时联网跑。

打桩测试已经覆盖了「超时 / 非 2xx / 结构不符一律降级」，但『这把 Key 到底能不能用、
供应商返回的结构对不对、模型 id 写没写对』只有真调一次才知道——打桩永远会说 OK。
这类用例不该污染 CI（离线、无额度、会消耗调用），所以用两道闸门控制：

1. `RECIPE_LIVE_TESTS=1`（显式同意联网）；
2. `RECIPE_GENERATOR_API_KEY` 非空（有可用凭据）。

运行（PowerShell，在仓库根目录）：

    $env:RECIPE_LIVE_TESTS="1"
    .\.venv\Scripts\python.exe -m pytest backend/tests/test_generator_live.py -v

脚本与接口都不回显 Key，这里同样不打印任何响应原文。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.generator import build_generator  # noqa: E402
from tools.check_generator import DEFAULT_DISH, fetch_model_ids  # noqa: E402

LIVE_FLAG = "RECIPE_LIVE_TESTS"


def _live_settings(**overrides) -> Settings:
    """真实凭据 + 隔离资源的配置：库里用临时目录，避免加载 335 MB 权重拖慢验收。"""

    settings = Settings(**overrides)
    if os.getenv(LIVE_FLAG) != "1":
        pytest.skip("未打开 %s=1，跳过真实调用" % LIVE_FLAG)
    if not settings.generator_configured:
        pytest.skip("未配置 RECIPE_GENERATOR_API_KEY，跳过真实调用")
    return settings


def test_provider_lists_the_configured_model_id():
    """模型 id 必须出现在服务端返回的列表里，否则是写错了名字。"""

    settings = _live_settings()
    model_ids = fetch_model_ids(settings)
    assert model_ids, "服务端返回了空的模型列表"
    assert settings.generator_model in model_ids, (
        "配置的模型 %s 不在服务端列表 %s 里" % (settings.generator_model, model_ids)
    )


def test_live_generation_passes_the_structured_contract():
    settings = _live_settings()
    payload = build_generator(settings).generate(DEFAULT_DISH)
    assert payload is not None, "真实调用失败或未通过结构校验"
    assert payload.name
    assert payload.cook_time_minutes >= 1
    assert payload.difficulty in ("简单", "中等", "困难")
    assert len(payload.steps) >= 3, "步骤太少，照着做不到"


def test_live_resolve_uses_the_generation_branch_and_caches(tmp_path):
    """端到端：库外菜 → source=generated；同一道菜再问一次由库内直接回答。

    生成成功会落库（v2.7），所以第二次的 source 是 library 而不是再生成一次。
    要验证的正是这件事：同一道菜只花一次额度，之后结果稳定、不再调用大模型。
    """

    settings = _live_settings(
        database_url="sqlite+pysqlite:///%s" % (tmp_path / "live.db").as_posix(),
        models_dir=tmp_path / "models-not-delivered",
    )
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health").json()
        assert health["generator"]["configured"] is True
        assert health["generator"]["model"] == settings.generator_model

        request = {"dish": DEFAULT_DISH, "index": 131}
        first = client.post("/api/recipes/resolve", json=request)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["source"] == "generated", "库外菜应走生成分支，实际 %s" % body["source"]
        assert body["degraded"] is False
        assert body["recipe"]["steps"], "生成的菜谱必须有步骤"

        second = client.post("/api/recipes/resolve", json=request).json()
        assert second["source"] == "library", "第一次生成就落了库，第二次直接命中库内"
        assert second["recipe"]["name"] == body["recipe"]["name"]
        assert second["recipe"]["id"] == body["recipe"]["id"]

    from app.core.database import get_session_factory
    from app.models.dish import GeneratedRecipe

    with get_session_factory()() as session:
        assert session.query(GeneratedRecipe).count() == 1, "生成结果必须落缓存，且只落一条"
