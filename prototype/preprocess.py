# -*- coding: utf-8 -*-
"""models/dish.onnx 的推理契约：预处理与 top-k 解码。

训练脚本用的是 timm 的评测变换（resize → center crop → ImageNet 归一化），
尺寸、插值、裁剪方式一律从 models/dish.manifest.json 读，不在这里写死——
两代权重的契约就不同（resnet50d：224 / 双三次；convnext：288 / 双线性），
写死就会在换模型时悄悄算错。两个很容易写错的细节：

1. 缩放后的长边用 int() 截断，而不是 round()；
2. 中心裁剪起点用 int(round((边长 - target) / 2))，而不是整除 //2。

实测 1242×698 的「莴笋炒虾仁」那张图，裁剪起点偏 1 个像素，top-1 置信度就从 64.3% 掉到 53.2%；
「土豆肉末拌面」那张从 45.9% 掉到 38.1%。这类偏差不会报错，只会让页面上的数字变得不可信。

生成页面（prototype/build_dish_prototype.py）、校验页面（tests/test_dish_prototype.py）
与后端识别层（backend/app/services/recognizer.py）共用这一份实现，
保证展示的数字和验证的数字来自同一套代码——后端按文件路径加载本模块，不做第二份拷贝。
"""

import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "models" / "dish.manifest.json"
MODEL_PATH = ROOT / "models" / "dish.onnx"

_MANIFEST = None


def manifest():
    """读转换权重时落盘的预处理契约（models/dish.manifest.json）。"""

    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return _MANIFEST


def input_size():
    return manifest()["preprocess"]["input_size"][-1]


def crop_pct():
    return manifest()["preprocess"]["crop_pct"]


def interpolation():
    """把 manifest 里的插值名翻成 PIL 常量；写错插值同样会让置信度漂移。"""

    from PIL import Image

    mapping = {"bicubic": Image.BICUBIC, "bilinear": Image.BILINEAR, "nearest": Image.NEAREST}
    name = manifest()["preprocess"]["interpolation"]
    if name not in mapping:
        raise SystemExit("manifest 里的插值方式不认识：%r" % name)
    return mapping[name]


def to_batch(image, mean, std):
    """把一张 PIL RGB 图按 manifest 预处理成 NCHW float32 数组 [1, 3, target, target]。"""

    import numpy as np
    from PIL import Image

    target = int(input_size())
    crop = int(round(target / crop_pct()))
    width, height = image.size
    short, long_side = min(width, height), max(width, height)
    new_short = crop
    new_long = int(new_short * long_side / short)
    size = (new_long, new_short) if width >= height else (new_short, new_long)
    image = image.resize(size, interpolation())

    left = int(round((image.width - target) / 2))
    top = int(round((image.height - target) / 2))
    image = image.crop((left, top, left + target, top + target))

    array = np.asarray(image).astype("float32") / 255.0
    array = (array - np.asarray(mean, dtype="float32")) / np.asarray(std, dtype="float32")
    return array.transpose(2, 0, 1)[None, ...]


def load_image(image_path, mean, std):
    """按 manifest 预处理一张磁盘上的图，返回 NCHW 的 float32 数组。"""

    from PIL import Image

    return to_batch(Image.open(image_path).convert("RGB"), mean, std)


def load_image_bytes(payload, mean, std):
    """按 manifest 预处理一段内存里的图片字节；上传接口走这条路。"""

    from PIL import Image

    return to_batch(Image.open(io.BytesIO(payload)).convert("RGB"), mean, std)


def create_session(model_path=None):
    import onnxruntime as ort

    return ort.InferenceSession(
        str(model_path or MODEL_PATH), providers=["CPUExecutionProvider"]
    )


def topk_from_logits(logits, k=5):
    """softmax 后取 top-k，返回 [(类别下标, 概率), ...]，按概率降序。"""

    import numpy as np

    shifted = np.exp(logits - logits.max())
    probs = shifted / shifted.sum()
    order = probs.argsort()[::-1][:k]
    return [(int(index), float(probs[index])) for index in order]


def predict_batch_topk(session, batch, k=5):
    """跑一遍模型：batch 已经在内存里，省掉重复预处理。"""

    logits = session.run(None, {"input": batch})[0][0]
    return topk_from_logits(logits, k)


def predict_topk(session, image_path, k=5):
    """跑一遍模型，返回 [(类别下标, softmax 概率), ...]，按概率降序。"""

    preprocess = manifest()["preprocess"]
    batch = load_image(image_path, preprocess["mean"], preprocess["std"])
    return predict_batch_topk(session, batch, k)


def measure_latency_ms(session, image_path, runs=5):
    """测单张推理耗时（毫秒，中位数），用于页面上的模型信息条。"""

    import statistics
    import time

    preprocess = manifest()["preprocess"]
    batch = load_image(image_path, preprocess["mean"], preprocess["std"])
    session.run(None, {"input": batch})  # warmup
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        session.run(None, {"input": batch})
        samples.append((time.perf_counter() - started) * 1000.0)
    return int(round(statistics.median(samples)))