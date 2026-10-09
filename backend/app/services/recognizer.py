"""识别能力接入层（设计方案 7.1 ~ 7.3）。

对上层只暴露一个契约：`DishRecognizer.recognize(image) -> RecognitionResult`。
上游交付的是分类模型（timm 主干，208 类；2026-09-20 起为 convnext_base，288 输入），本层负责预处理、一次前向、softmax、
取 top-k，并把「类别下标 → 菜名」的缺失情况如实标出来。

降级链：ONNX 权重缺失 / manifest 缺失 / 预处理实现缺失 / 推理异常 → MockDishRecognizer，
接口契约不变，只把 `degraded` 置为 true。**类别清单缺失不触发降级**：
仍走真实模型，只是名称退化为「类别 #N」或演示图注（设计方案 7.3）。
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from ..core.config import Settings, get_settings
from .dish_preprocess import DishModelUnavailable, load_canonical_preprocess
from .onnx_providers import build_session

logger = logging.getLogger(__name__)

_MASK32 = 0xFFFFFFFF


@dataclass(frozen=True)
class DishCandidate:
    index: int
    name: str
    confidence: float
    provisional_name: bool


@dataclass(frozen=True)
class RecognitionResult:
    candidates: List[DishCandidate]
    model_version: str
    class_count: int
    labels_available: bool
    degraded: bool


def placeholder_name(index: int) -> str:
    """类别清单未到位时的占位名，前端据此渲染「名称待确认」角标。"""

    return f"类别 #{index}"


class ClassLabels:
    """类别下标 → 菜名。

    三个来源按优先级：训练产出的正式清单 > 演示图注（原型里的图注名）> 「类别 #N」。
    后两者一律标记为临时名称，前端展示待确认角标——拿到清单前不允许去掉这条披露。
    """

    def __init__(
        self,
        labels: Optional[Mapping[int, str]] = None,
        captions: Optional[Mapping[int, str]] = None,
    ) -> None:
        self._labels: Dict[int, str] = dict(labels or {})
        self._captions: Dict[int, str] = dict(captions or {})

    @property
    def available(self) -> bool:
        """是否为训练产出的正式清单。"""

        return bool(self._labels)

    @property
    def source(self) -> str:
        if self._labels:
            return "labels"
        if self._captions:
            return "captions"
        return "placeholder"

    def resolve(self, index: int) -> Tuple[str, bool]:
        """返回 (菜名, 是否为临时名称)。"""

        if index in self._labels:
            return self._labels[index], False
        if index in self._captions:
            return self._captions[index], True
        return placeholder_name(index), True

    def capture(self, index: int, name: str) -> Optional[str]:
        """如果 index 是正式的图注类别，返回图注名，否则 None。"""

        if index in self._labels or index not in self._captions:
            return None
        return self._captions[index]


def _read_index_name_file(path: Path) -> Dict[int, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mapping: Dict[int, str] = {}
    for key, value in dict(raw).items():
        try:
            mapping[int(key)] = str(value)
        except (TypeError, ValueError):  # pragma: no cover - 数据文件由本仓库维护
            logger.warning("类别清单里有非整数下标，已跳过：%r", key)
    return mapping


def load_class_labels(settings: Optional[Settings] = None) -> ClassLabels:
    """读类别清单；清单缺失不算失败，只是名称退化为占位。"""

    settings = settings or get_settings()
    if settings.dish_labels_path.is_file():
        return ClassLabels(_read_index_name_file(settings.dish_labels_path), {})

    captions: Dict[int, str] = {}
    if settings.dish_captions_path.is_file():
        captions = _read_index_name_file(settings.dish_captions_path)
        logger.info(
            "类别清单未交付，按演示图注渲染 %d 个类别名（仍标记为待确认）", len(captions)
        )
    else:
        logger.info("类别清单与演示图注都缺失，类别名一律按「类别 #N」占位")
    return ClassLabels({}, captions)


class DishRecognizer(Protocol):
    model_version: str
    class_count: int
    degraded: bool
    labels_available: bool
    providers: Sequence[str]
    labels: ClassLabels

    def recognize(self, image: bytes) -> RecognitionResult:  # pragma: no cover - 协议声明
        ...


# --------------------------------------------------------------------------
# 确定性伪随机：与旧原型保持一致，保证「同一张图恒定同一结果」
# --------------------------------------------------------------------------

def fnv1a(data: bytes) -> int:
    """FNV-1a 32 位哈希。"""

    digest = 0x811C9DC5
    for byte in data:
        digest ^= byte
        digest = (digest * 0x01000193) & _MASK32
    return digest


class Mulberry32:
    """mulberry32 伪随机数发生器。"""

    def __init__(self, seed: int) -> None:
        self._a = seed & _MASK32

    def next(self) -> float:
        self._a = (self._a + 0x6D2B79F5) & _MASK32
        t = self._a
        t = ((t ^ (t >> 15)) * (1 | t)) & _MASK32
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & _MASK32)) & _MASK32) ^ t
        t &= _MASK32
        return ((t ^ (t >> 14)) & _MASK32) / 4294967296


def _floor4(value: float) -> float:
    """向下取整到 4 位小数；模拟结果用它避免 top-k 之和越过 1。"""

    return math.floor(max(value, 0.0) * 10000) / 10000


def _sample_candidates(
    labels: ClassLabels, class_count: int, top_k: int, seed: int
) -> List[DishCandidate]:
    """按固定种子从类别空间里抽一批候选，置信度降序且总和不超过 1。"""

    rand = Mulberry32(seed).next
    pool = list(range(max(class_count, 0)))
    picked: List[int] = []
    for _ in range(min(max(top_k, 1), len(pool))):
        picked.append(pool.pop(int(rand() * len(pool))))
    if not picked:
        return []

    top1 = 0.40 + rand() * 0.45
    tail = 1.0 - top1
    weights = [rand() + 0.05 for _ in picked[1:]]
    total = sum(weights) or 1.0

    # 向下取整到 4 位，保证 top-k 的置信度之和不会因为四舍五入越过 1
    confidences = [top1] + [tail * weight / total for weight in weights]
    confidences = [_floor4(value) for value in confidences]
    candidates: List[DishCandidate] = []
    for index, confidence in zip(picked, confidences):
        name, provisional = labels.resolve(index)
        candidates.append(
            DishCandidate(
                index=index,
                name=name,
                confidence=confidence,
                provisional_name=provisional,
            )
        )
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates


class MockDishRecognizer:
    """降级实现：模型不可用时保证接口仍然可用，同一张图结果恒定。"""

    def __init__(
        self,
        labels: Optional[ClassLabels] = None,
        settings: Optional[Settings] = None,
        reason: Optional[str] = None,
    ) -> None:
        settings = settings or get_settings()
        self._labels = labels or ClassLabels()
        self.model_version = settings.mock_model_version
        self.class_count = settings.dish_class_count
        self.degraded = True
        self.reason = reason
        self._top_k = settings.top_k
        # 模拟实现不碰 ONNX，没有执行提供程序可言：留空比编一个真实
        self.providers: Sequence[str] = ()

    @property
    def labels_available(self) -> bool:
        return self._labels.available

    @property
    def labels(self) -> ClassLabels:
        return self._labels

    def recognize(self, image: bytes) -> RecognitionResult:
        seed = fnv1a(image) if image else 0
        candidates = _sample_candidates(self._labels, self.class_count, self._top_k, seed)
        return RecognitionResult(
            candidates=candidates,
            model_version=self.model_version,
            class_count=self.class_count,
            labels_available=self._labels.available,
            degraded=True,
        )


class OnnxDishRecognizer:
    """真实实现：按 manifest 预处理，取 logits 的 top-k 并做 softmax。"""

    def __init__(
        self,
        session,
        labels: ClassLabels,
        settings: Settings,
        preprocess,
        class_count: int,
        providers: Sequence[str] = (),
    ) -> None:
        self._session = session
        self._labels = labels
        self._preprocess = preprocess
        self._top_k = settings.top_k
        self.model_version = settings.onnx_model_version
        self.class_count = class_count
        self.providers: Sequence[str] = tuple(providers)
        self.degraded = False

    @property
    def labels_available(self) -> bool:
        return self._labels.available

    @property
    def labels(self) -> ClassLabels:
        return self._labels

    @classmethod
    def from_settings(
        cls, settings: Optional[Settings] = None, labels: Optional[ClassLabels] = None
    ) -> "OnnxDishRecognizer":
        settings = settings or get_settings()
        onnx_path = settings.dish_onnx_path
        manifest_path = settings.dish_manifest_path
        if not onnx_path.is_file():
            raise DishModelUnavailable(f"缺少模型权重：{onnx_path}")
        if not manifest_path.is_file():
            raise DishModelUnavailable(f"缺少预处理契约：{manifest_path}")

        preprocess = load_canonical_preprocess()
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
        class_count = int(meta.get("num_classes") or settings.dish_class_count)
        # 建会话的这一步顺带校验加速有没有生效：装了 GPU 版运行时也可能静默回落到 CPU
        session, status = build_session(onnx_path, settings.recognize_providers)
        return cls(
            session,
            labels or load_class_labels(settings),
            settings,
            preprocess,
            class_count,
            providers=status.granted,
        )

    def recognize(self, image: bytes) -> RecognitionResult:
        preprocess_meta = self._preprocess.manifest()["preprocess"]
        batch = self._preprocess.load_image_bytes(
            image, preprocess_meta["mean"], preprocess_meta["std"]
        )
        topk: Sequence[Tuple[int, float]] = self._preprocess.predict_batch_topk(
            self._session, batch, k=self._top_k
        )
        candidates: List[DishCandidate] = []
        for index, confidence in topk:
            name, provisional = self._labels.resolve(index)
            candidates.append(
                DishCandidate(
                    index=index,
                    name=name,
                    confidence=round(float(confidence), 4),
                    provisional_name=provisional,
                )
            )
        return RecognitionResult(
            candidates=candidates,
            model_version=self.model_version,
            class_count=self.class_count,
            labels_available=self._labels.available,
            degraded=False,
        )


class FallbackDishRecognizer:
    """先真实、后降级：真实实现不可用或推理异常时自动回退（设计方案 7.3）。"""

    def __init__(self, primary: Optional[DishRecognizer], fallback: MockDishRecognizer) -> None:
        self._primary = primary
        self._fallback = fallback
        self.model_version = primary.model_version if primary else fallback.model_version
        self.class_count = primary.class_count if primary else fallback.class_count

    @property
    def degraded(self) -> bool:
        return self._primary is None

    @property
    def labels_available(self) -> bool:
        return self._primary.labels_available if self._primary else self._fallback.labels_available

    @property
    def labels(self) -> ClassLabels:
        if self._primary is not None:
            return self._primary.labels
        return self._fallback.labels

    @property
    def primary(self) -> Optional[DishRecognizer]:
        return self._primary

    @property
    def providers(self) -> Sequence[str]:
        """真实实现跑在哪些执行提供程序上；降级到模拟实现时为空。"""

        return self._primary.providers if self._primary is not None else ()

    def recognize(self, image: bytes) -> RecognitionResult:
        if self._primary is None:
            return self._fallback.recognize(image)
        try:
            return self._primary.recognize(image)
        except Exception as exc:  # 任何推理异常都不能让接口不可用
            logger.warning("真实识别失败，降级为模拟结果：%s", exc)
            result = self._fallback.recognize(image)
            return RecognitionResult(
                candidates=result.candidates,
                model_version=result.model_version,
                class_count=result.class_count,
                labels_available=result.labels_available,
                degraded=True,
            )


def build_recognizer(settings: Optional[Settings] = None) -> FallbackDishRecognizer:
    """装配识别器：模型就绪用 ONNX，否则用 Mock，二者契约完全一致。"""

    settings = settings or get_settings()
    labels = load_class_labels(settings)
    try:
        primary: Optional[DishRecognizer] = OnnxDishRecognizer.from_settings(settings, labels)
    except DishModelUnavailable as exc:
        logger.info("启用模拟识别（%s）", exc)
        primary = None
    return FallbackDishRecognizer(primary, MockDishRecognizer(labels, settings))
