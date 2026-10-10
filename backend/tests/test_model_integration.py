"""模型权重接入验收测试（设计方案 7.2「接入验收口径」）。

需要 models/dish.onnx，缺失时整体跳过——这样没有权重的环境仍然能跑通其余测试。

最关键的几条：
- 4 张演示图的 top-1 与 prototype/dish.html 页面上的数字逐张一致（容差 0.002），
   而页面截图就是静态演示站上的那两张，所以这里同时锁住了「演示数字与实际输出一致」；
- 单张推理耗时满足 NFR-01 的 500 ms 预算；
- 接口层端到端：真实照片 → 候选 → 按类别下标取到库内菜谱。
"""

from __future__ import annotations

import json
import re
import statistics
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.recognizer import OnnxDishRecognizer, build_recognizer

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = REPO_ROOT / "models"
LABELS_PATH = MODELS_DIR / "dish_labels.json"
WEIGHTS = MODELS_DIR / "dish.onnx"
PROTOTYPE_PATH = REPO_ROOT / "prototype" / "dish.html"
DEMO_DIR = REPO_ROOT / "demo" / "dishes"

pytestmark = pytest.mark.skipif(not WEIGHTS.exists(), reason="尚未产出 models/dish.onnx")

LATENCY_BUDGET_MS = 500  # NFR-01：单张推理 P95（CPU + ONNX Runtime）
CONFIDENCE_TOLERANCE = 0.002  # 设计方案 7.2「一致性」验收口径

# 能给出库内菜谱的类别数（精确同名 + 别名映射），口径见 test_model_assets 与 seed_dish_map.json
COVERED_CLASS_COUNT = 73

# 设计方案 2.4 的基准用例：4 张示例图的期望 top-1（第二代 convnext 权重的实测值）
DEMO_FIXTURE = {
    "dish-braised-pork": (77, 0.885),
    "dish-omurice": (131, 0.709),
    "dish-shrimp-greens": (100, 0.643),
    "dish-noodle-potato": (156, 0.459),
}

_DEMO_SCRIPT = re.compile(
    r'<script id="demo-dishes" type="application/json">(.*?)</script>', re.DOTALL
)


@pytest.fixture(scope="module")
def recognizer():
    return build_recognizer(Settings())


def _photo(stem: str) -> bytes:
    return (DEMO_DIR / f"{stem}.jpg").read_bytes()


def _prototype_topk() -> dict:
    """读 prototype/dish.html 里内嵌的真实模型输出，作为页面基准。"""

    match = _DEMO_SCRIPT.search(PROTOTYPE_PATH.read_text(encoding="utf-8"))
    assert match, "原型页里找不到内嵌的演示数据"
    data = json.loads(match.group(1))
    return {
        Path(photo["file"]).stem: photo["topk"] for photo in data["photos"]
    }


# --------------------------- 加载与元数据 ---------------------------

def test_real_model_is_loaded_and_reported(recognizer):
    assert isinstance(recognizer.primary, OnnxDishRecognizer)
    assert recognizer.degraded is False
    assert recognizer.model_version == "dish-convnext-base-208-onnx"
    assert recognizer.class_count == 208
    assert recognizer.labels_available is True, "类别清单已交付（models/dish_labels.json）"
    assert recognizer.labels.resolve(77) == ("红烧肉", False)
    # 真实推理必须报出执行提供程序：装了 GPU 版运行时也可能静默回落到 CPU，
    # 所以这里锁的是「有 CPU 兜底、结果不是空的」，而不是断言一定跑在 GPU 上
    assert "CPUExecutionProvider" in recognizer.providers


def test_health_reports_real_model(app_client):
    body = app_client.get("/api/health").json()
    assert body["model"]["loaded"] is True
    assert body["model"]["degraded"] is False
    assert body["model"]["class_count"] == 208
    assert body["model"]["version"] == "dish-convnext-base-208-onnx"
    assert body["model"]["providers"], "真实模型就位时不该是空列表"


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{(tmp_path_factory.mktemp('db') / 'real.db').as_posix()}",
        models_dir=MODELS_DIR,
    )
    with TestClient(create_app(settings)) as client:
        yield client


# --------------------------- 一致性回归 ---------------------------

@pytest.mark.parametrize("stem", sorted(DEMO_FIXTURE))
def test_demo_photo_top1_matches_the_documented_fixture(recognizer, stem):
    expected_index, expected_confidence = DEMO_FIXTURE[stem]
    top1 = recognizer.recognize(_photo(stem)).candidates[0]
    assert top1.index == expected_index
    assert abs(top1.confidence - expected_confidence) <= CONFIDENCE_TOLERANCE


@pytest.mark.parametrize("stem", sorted(DEMO_FIXTURE))
def test_demo_photo_matches_the_prototype_page(recognizer, stem):
    """接口算出来的 top-5 必须和 prototype/dish.html 上显示的一致。

    名次允许在「置信度并列」的条目之间互换：dish-braised-pork 的第 4、5 名分别是
    0.003 与 0.003，换执行提供程序（CPU 与 CUDA 的 logits 有 1e-3 量级浮点差异）
    就足以让这两个位次对调。页面上的数字没有跑偏，跑偏的是并列项的先后。
    """

    prototype = {item["index"]: item["confidence"] for item in _prototype_topk()[stem]}
    candidates = recognizer.recognize(_photo(stem)).candidates
    assert candidates[0].index == next(iter(prototype))
    assert {item.index for item in candidates} == set(prototype)
    for item in candidates:
        assert abs(item.confidence - prototype[item.index]) <= CONFIDENCE_TOLERANCE
    # 排名顺序按页面基准复核：差距大于容差的两个名次不许颠倒，并列的可以互换
    for position, item in enumerate(candidates[:-1]):
        following = candidates[position + 1]
        assert (
            prototype[item.index] - prototype[following.index] >= -CONFIDENCE_TOLERANCE
        )


def test_candidates_are_top5_descending_and_named_from_the_class_list(recognizer):
    labels = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    result = recognizer.recognize(_photo("dish-braised-pork"))

    assert len(result.candidates) == 5
    confidences = [item.confidence for item in result.candidates]
    assert confidences == sorted(confidences, reverse=True)
    assert all(0.0 <= value <= 1.0 for value in confidences)

    # 清单已交付：每个候选都是正式类别名，不再有占位名
    for item in result.candidates:
        assert item.provisional_name is False
        assert item.name == labels[str(item.index)]
    assert result.candidates[0].name == "红烧肉"
    # 第二名是「梅菜扣肉」：近义的红烧类，正说明界面必须展示 top-5 而不是只报一个答案
    assert result.candidates[1].name == labels["83"] == "梅菜扣肉"


# --------------------------- 性能 ---------------------------

def test_single_inference_is_within_budget(recognizer):
    photo = _photo("dish-braised-pork")
    recognizer.recognize(photo)  # warmup
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        recognizer.recognize(photo)
        samples.append((time.perf_counter() - started) * 1000.0)
    median = statistics.median(samples)
    assert median <= LATENCY_BUDGET_MS, f"单张中位耗时 {median:.0f} ms 超出预算"


# --------------------------- 接口层端到端 ---------------------------

def test_end_to_end_recognize_then_resolve(app_client):
    response = app_client.post(
        "/api/recognize",
        files={"file": ("dish.jpg", _photo("dish-braised-pork"), "image/jpeg")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is False
    top1 = body["candidates"][0]
    assert top1["index"] == 77
    assert top1["name"] == "红烧肉"
    assert top1["provisional_name"] is False

    resolved = app_client.post(
        "/api/recipes/resolve", json={"dish": top1["name"], "index": top1["index"]}
    ).json()
    assert resolved["source"] == "library"
    assert resolved["recipe"]["id"] == 109
    assert resolved["recipe"]["name"] == "红烧肉"


def test_uncovered_demo_photo_degrades_cleanly(app_client):
    """库外菜走生成分支；没配 Key 时是明确降级，不是报错。"""

    top1 = app_client.post(
        "/api/recognize",
        files={"file": ("dish.jpg", _photo("dish-noodle-potato"), "image/jpeg")},
    ).json()["candidates"][0]
    assert top1["index"] == 156
    assert top1["name"] == "担担面"

    resolved = app_client.post(
        "/api/recipes/resolve", json={"dish": top1["name"], "index": top1["index"]}
    ).json()
    assert resolved["source"] == "unavailable"
    assert resolved["degraded"] is True
    assert resolved["recipe"] is None


def test_dishes_endpoint_reports_real_class_space(app_client):
    body = app_client.get("/api/dishes").json()
    assert body["class_count"] == 208
    assert body["labels_available"] is True
    assert body["covered_class_count"] == COVERED_CLASS_COUNT
    assert body["items"][0]["name"] == "麻婆豆腐", "名称取自清单，不再是「类别 #0」"
    assert body["items"][0]["provisional_name"] is False


# --------------------------- 降级 ---------------------------

def test_missing_weight_still_serves_the_contract(tmp_path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'degraded.db').as_posix()}",
        models_dir=tmp_path / "not-delivered",
    )
    with TestClient(create_app(settings)) as client:
        body = client.post(
            "/api/recognize",
            files={"file": ("dish.jpg", _photo("dish-braised-pork"), "image/jpeg")},
        ).json()
    assert body["degraded"] is True
    assert len(body["candidates"]) == 5
    assert body["class_count"] == 208
