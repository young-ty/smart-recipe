"""识别接入层测试：类别名解析、确定性、降级路径（设计方案 7.1 ~ 7.3）。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from app.core.config import REPO_ROOT, Settings
from app.services.dish_preprocess import (
    CANONICAL_PREPROCESS_PATH,
    DishModelUnavailable,
    load_canonical_preprocess,
)
from app.services.recognizer import (
    ClassLabels,
    DishCandidate,
    RecognitionResult,
    FallbackDishRecognizer,
    MockDishRecognizer,
    Mulberry32,
    OnnxDishRecognizer,
    build_recognizer,
    fnv1a,
    load_class_labels,
    placeholder_name,
)

# 与 conftest 保持一致的样例图片；此处独立定义以免测试模块互相 import
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_1X1_BLUE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

DISH_ONNX = REPO_ROOT / "models" / "dish.onnx"
DISH_MANIFEST = REPO_ROOT / "models" / "dish.manifest.json"


# --------------------------- 伪随机基础件 ---------------------------

def test_fnv1a_matches_known_vectors():
    assert fnv1a(b"") == 0x811C9DC5
    assert fnv1a(b"a") == 0xE40C292C
    assert fnv1a(b"foobar") == 0xBF9CF968


def test_mulberry32_stays_in_unit_interval():
    rand = Mulberry32(12345).next
    values = [rand() for _ in range(50)]
    assert all(0.0 <= value < 1.0 for value in values)
    assert Mulberry32(12345).next() == values[0], "同一种子必须可复现"


# --------------------------- 类别名解析 ---------------------------

def test_placeholder_name_is_marked_for_the_frontend():
    assert placeholder_name(77) == "类别 #77"


def test_delivered_labels_are_not_provisional():
    labels = ClassLabels({77: "红烧肉"})
    assert labels.available is True
    assert labels.source == "labels"
    assert labels.resolve(77) == ("红烧肉", False)


def test_captions_are_always_provisional():
    labels = ClassLabels({}, {77: "红烧肉"})
    assert labels.available is False
    assert labels.source == "captions"
    assert labels.resolve(77) == ("红烧肉", True)
    assert labels.resolve(78) == ("类别 #78", True)


def test_labels_win_over_captions():
    labels = ClassLabels({77: "红烧肉"}, {77: "旧图注"})
    assert labels.resolve(77) == ("红烧肉", False)


def test_empty_labels_resolve_to_placeholder():
    labels = ClassLabels()
    assert labels.source == "placeholder"
    assert labels.available is False
    assert labels.resolve(0) == ("类别 #0", True)


def test_load_class_labels_reads_delivered_file(tmp_path):
    (tmp_path / "dish_labels.json").write_text(
        json.dumps({str(i): f"菜 {i}" for i in range(208)}, ensure_ascii=False), encoding="utf-8"
    )
    labels = load_class_labels(Settings(data_dir=tmp_path, models_dir=tmp_path))
    assert labels.available is True
    assert labels.resolve(77) == ("菜 77", False)


def test_load_class_labels_falls_back_to_captions(tmp_path):
    (tmp_path / "dish_captions.json").write_text(
        json.dumps({"77": "红烧肉"}, ensure_ascii=False), encoding="utf-8"
    )
    labels = load_class_labels(Settings(data_dir=tmp_path, models_dir=tmp_path))
    assert labels.available is False
    assert labels.resolve(77) == ("红烧肉", True)


def test_load_class_labels_without_any_file(tmp_path):
    labels = load_class_labels(Settings(data_dir=tmp_path, models_dir=tmp_path))
    assert labels.source == "placeholder"
    assert labels.resolve(77) == ("类别 #77", True)


# --------------------------- 降级实现 ---------------------------

def test_mock_recognizer_is_deterministic():
    recognizer = MockDishRecognizer()
    assert recognizer.recognize(PNG_1X1).candidates == recognizer.recognize(PNG_1X1).candidates
    assert (
        recognizer.recognize(PNG_1X1).candidates != recognizer.recognize(PNG_1X1_BLUE).candidates
    )


def test_mock_recognizer_returns_top_k_descending_distinct():
    recognizer = MockDishRecognizer()
    result = recognizer.recognize(PNG_1X1)
    assert result.degraded is True
    assert result.class_count == 208
    assert len(result.candidates) == Settings().top_k
    confidences = [item.confidence for item in result.candidates]
    assert confidences == sorted(confidences, reverse=True)
    assert all(0.0 <= value <= 1.0 for value in confidences)
    assert sum(confidences) <= 1.0
    indices = [item.index for item in result.candidates]
    assert len(set(indices)) == len(indices)
    assert all(0 <= index < 208 for index in indices)


def test_mock_recognizer_uses_labels_for_names():
    labels = ClassLabels({index: f"菜 {index}" for index in range(208)})
    result = MockDishRecognizer(labels).recognize(PNG_1X1)
    assert result.labels_available is True
    assert all(item.provisional_name is False for item in result.candidates)
    assert all(item.name == f"菜 {item.index}" for item in result.candidates)


class _BoomRecognizer:
    model_version = "boom"
    class_count = 208
    degraded = False
    labels_available = False
    labels = ClassLabels()
    providers = ("CUDAExecutionProvider", "CPUExecutionProvider")

    def recognize(self, image: bytes):
        raise RuntimeError("推理炸了")


class _OkRecognizer:
    """模拟「真实实现可用」的情形：正常返回且不降级。"""

    model_version = "ok"
    class_count = 208
    degraded = False
    labels_available = True
    labels = ClassLabels({0: "红烧肉"})
    providers = ("CUDAExecutionProvider", "CPUExecutionProvider")

    def recognize(self, image: bytes):
        return RecognitionResult(
            candidates=[DishCandidate(index=0, name="红烧肉", confidence=0.8, provisional_name=False)],
            model_version=self.model_version,
            class_count=self.class_count,
            labels_available=True,
            degraded=False,
        )


def test_fallback_uses_primary_when_it_works():
    recognizer = FallbackDishRecognizer(_OkRecognizer(), MockDishRecognizer())
    assert recognizer.degraded is False
    result = recognizer.recognize(PNG_1X1)
    assert result.degraded is False
    assert result.candidates[0].name == "红烧肉"


def test_fallback_degrades_when_no_primary():
    fallback = MockDishRecognizer()
    recognizer = FallbackDishRecognizer(None, fallback)
    assert recognizer.degraded is True
    assert recognizer.model_version == fallback.model_version
    assert recognizer.primary is None
    assert recognizer.recognize(PNG_1X1).degraded is True


def test_fallback_swallows_primary_failure():
    recognizer = FallbackDishRecognizer(_BoomRecognizer(), MockDishRecognizer())
    result = recognizer.recognize(PNG_1X1)
    assert result.degraded is True
    assert result.candidates


def test_fallback_reports_the_primary_providers():
    """页面与 /api/health 要能看到推理实际跑在哪块硬件上。"""

    recognizer = FallbackDishRecognizer(_OkRecognizer(), MockDishRecognizer())
    assert recognizer.providers == ("CUDAExecutionProvider", "CPUExecutionProvider")


def test_fallback_without_a_primary_reports_no_providers():
    """模拟实现不碰 ONNX，留空比编一个真实。"""

    recognizer = FallbackDishRecognizer(None, MockDishRecognizer())
    assert recognizer.providers == ()
    assert MockDishRecognizer().providers == ()


def test_build_recognizer_degrades_when_model_absent(settings):
    recognizer = build_recognizer(settings)
    assert recognizer.primary is None
    assert recognizer.degraded is True
    assert recognizer.class_count == 208


# --------------------------- 真实实现 ---------------------------

def test_onnx_recognizer_requires_weight(tmp_path):
    with pytest.raises(DishModelUnavailable):
        OnnxDishRecognizer.from_settings(Settings(models_dir=tmp_path))


def test_canonical_preprocess_module_is_the_repo_one():
    module = load_canonical_preprocess()
    assert CANONICAL_PREPROCESS_PATH.is_file()
    for name in ("manifest", "load_image_bytes", "predict_batch_topk", "create_session"):
        assert hasattr(module, name), f"预处理实现缺少 {name}"


@pytest.mark.skipif(not DISH_ONNX.is_file(), reason="尚未交付 models/dish.onnx")
def test_build_recognizer_loads_dish_model():
    recognizer = build_recognizer(Settings())
    assert isinstance(recognizer.primary, OnnxDishRecognizer)
    assert recognizer.degraded is False
    assert recognizer.model_version == "dish-convnext-base-208-onnx"
    assert recognizer.class_count == 208
