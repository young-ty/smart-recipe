"""生成层自检脚本（backend/tools/check_generator.py）的测试。

脚本本身不参与运行时链路，但它是「Key 到底有没有接上」的唯一离线判据：
默认模式必须**一次网络请求都不发**，且任何输出、任何异常信息都不许带出 Key。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import tools.check_generator as tool  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.services.generator import GeneratedRecipePayload  # noqa: E402

REPO_ROOT = BACKEND_DIR.parent
# 故意写成不像真 Key 的样子（真 Key 形如 sk-<32 位十六进制>）：
# test_env_file.py 里有一条全仓库扫描，任何文件出现那个形状就算泄漏，这里不能破这个例。
FAKE_KEY = "sk-test-only-not-a-real-key-0000000000"

PAYLOAD = GeneratedRecipePayload(
    name="蛋包饭",
    cook_time_minutes=25,
    difficulty="简单",
    servings=2,
    required=[{"name": "鸡蛋", "amount": "3 个"}, {"name": "米饭", "amount": "1 碗"}],
    optional=[{"name": "番茄酱", "amount": "适量"}],
    steps=["鸡蛋打散摊成蛋皮", "隔夜饭下锅炒散调味", "包进蛋皮后淋番茄酱"],
)


def _configured() -> Settings:
    return Settings(
        generator_api_key=FAKE_KEY,
        generator_base_url="https://example.test/v1",
        generator_model="m",
    )


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=1.0)


# --------------------------- 脱敏 ---------------------------

def test_mask_secret_never_returns_the_key():
    masked = tool.mask_secret(FAKE_KEY)
    assert FAKE_KEY not in masked
    assert FAKE_KEY[8:] not in masked  # 中段更不能出现
    assert "未配置" == tool.mask_secret("")


def test_describe_reports_configuration_without_the_key():
    info = tool.describe(_configured())
    assert info["configured"] is True
    assert info["base_url"] == "https://example.test/v1"
    assert info["model"] == "m"
    assert FAKE_KEY not in json.dumps(info, ensure_ascii=False)


def test_describe_marks_missing_key():
    info = tool.describe(Settings(generator_api_key=""))
    assert info["configured"] is False
    assert info["api_key"] == "未配置"


# --------------------------- 模型列表 ---------------------------

def test_fetch_model_ids_reads_ids_and_sends_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]})

    ids = tool.fetch_model_ids(_configured(), client=_mock_client(handler))
    assert ids == ["deepseek-chat", "deepseek-reasoner"]
    assert seen["url"] == "https://example.test/v1/models"
    assert seen["auth"] == "Bearer %s" % FAKE_KEY


def test_fetch_model_ids_tolerates_items_without_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "m"}, {}, {"object": "model"}]})

    assert tool.fetch_model_ids(_configured(), client=_mock_client(handler)) == ["m"]


def test_fetch_model_ids_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(httpx.HTTPStatusError):
        tool.fetch_model_ids(_configured(), client=_mock_client(handler))


# --------------------------- 真实调用 ---------------------------

def test_run_live_without_key_is_a_failure_not_an_exception():
    ok, detail = tool.run_live(Settings(generator_api_key=""), "蛋包饭")
    assert ok is False
    assert "API Key" in detail


def test_run_live_reports_structured_result(monkeypatch):
    monkeypatch.setattr(tool, "build_generator", lambda *args, **kwargs: _stub_generator())
    ok, detail = tool.run_live(_configured(), "蛋包饭")
    assert ok is True
    assert "蛋包饭" in detail and "3 步" in detail


def test_run_live_actually_calls_the_configured_endpoint(monkeypatch):
    """自检脚本必须真的走生成层的调用路径，不能自己另写一套 HTTP。"""

    from app.services.generator import OpenAICompatibleGenerator

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        content = PAYLOAD.model_dump_json()
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(
        tool,
        "build_generator",
        lambda settings, **kwargs: OpenAICompatibleGenerator(
            settings.generator_api_key,
            settings.generator_base_url,
            settings.generator_model,
            client=_mock_client(handler),
        ),
    )
    ok, detail = tool.run_live(_configured(), "蛋包饭")
    assert ok is True
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer %s" % FAKE_KEY
    assert FAKE_KEY not in detail


def _stub_generator():
    class _Stub:
        configured = True
        model = "stub"

        def generate(self, dish: str):
            return PAYLOAD

    return _Stub()


def test_run_live_reports_failure_when_generation_degrades(monkeypatch):
    class _Degraded:
        configured = True
        model = "stub"

        def generate(self, dish: str):
            return None

    monkeypatch.setattr(tool, "build_generator", lambda *args, **kwargs: _Degraded())
    ok, detail = tool.run_live(_configured(), "蛋包饭")
    assert ok is False
    assert "失败" in detail or "未通过" in detail


# --------------------------- 命令行入口 ---------------------------

def test_default_mode_sends_no_request(monkeypatch, capsys):
    monkeypatch.setattr(tool, "get_settings", _configured)

    def boom(*args, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("默认模式不应联网")

    monkeypatch.setattr(tool, "fetch_model_ids", boom)
    monkeypatch.setattr(httpx, "Client", boom)
    assert tool.main([]) == 0
    out = capsys.readouterr().out
    assert "配置自检" in out
    assert FAKE_KEY not in out


def test_main_exits_1_without_key(monkeypatch, capsys):
    monkeypatch.setattr(tool, "get_settings", lambda: Settings(generator_api_key=""))
    assert tool.main([]) == 1
    assert "RECIPE_GENERATOR_API_KEY" in capsys.readouterr().out


def test_main_lists_models_and_can_run_live(monkeypatch, capsys):
    monkeypatch.setattr(tool, "get_settings", _configured)
    monkeypatch.setattr(tool, "fetch_model_ids", lambda settings: ["deepseek-chat"])
    monkeypatch.setattr(tool, "run_live", lambda settings, dish: (True, "蛋包饭 · 25 分钟"))
    assert tool.main(["--models", "--live"]) == 0
    out = capsys.readouterr().out
    assert "deepseek-chat" in out
    assert FAKE_KEY not in out


def test_main_returns_1_when_live_generation_fails(monkeypatch, capsys):
    monkeypatch.setattr(tool, "get_settings", _configured)
    monkeypatch.setattr(tool, "run_live", lambda settings, dish: (False, "超时"))
    assert tool.main(["--live"]) == 1
    assert "失败" in capsys.readouterr().out


def test_default_dish_is_one_the_library_cannot_serve():
    """默认菜名必须是库外菜：库内菜会命中菜谱库，压根不会走生成分支。"""

    labels = json.loads((REPO_ROOT / "models" / "dish_labels.json").read_text(encoding="utf-8"))
    seed_map = json.loads(
        (BACKEND_DIR / "app" / "data" / "seed_dish_map.json").read_text(encoding="utf-8")
    )
    index = next((k for k, name in labels.items() if name == tool.DEFAULT_DISH), None)
    assert index is not None, "默认菜名 %s 不在类别清单里" % tool.DEFAULT_DISH
    entry = seed_map.get(str(index))
    assert entry is not None, "默认菜名 %s（类别 #%s）不在类别承接表里" % (tool.DEFAULT_DISH, index)
    assert entry.get("recipe_id") is None, "默认菜名应选库外菜（recipe_id 为 null）"
