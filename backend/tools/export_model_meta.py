"""从训练产出的 ONNX 中导出 labels.json 与 preprocess.json。

背景（设计方案 7.1、7.3）：权重体积大、不便 diff，不入版本库；仓库只保留
可复现的导出脚本与校验和。训练产出新的 ONNX 后重跑本脚本即可。

用法：
    .venv\Scripts\python.exe backend/tools/export_model_meta.py [模型路径] [输出目录]
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MODEL = REPO_ROOT / "models" / "best.onnx"
DEFAULT_OUTPUT = REPO_ROOT / "models"

# Ultralytics 预测预处理：letterbox 到正方形、RGB、归一化到 [0, 1]、不做 ImageNet 均值方差
ULTRALYTICS_PREPROCESS = {
    "color_order": "RGB",
    "mean": [0.0, 0.0, 0.0],
    "std": [1.0, 1.0, 1.0],
    "layout": "NCHW",
    "letterbox": True,
    "pad_value": 114,
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_names(raw: str) -> dict:
    """``names`` 元数据形如 ``{0: 'candy', 1: 'egg tart', ...}`` 的 Python 字面量。"""

    names = ast.literal_eval(raw)
    return {int(index): str(label) for index, label in names.items()}


def main(argv: list) -> int:
    model_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_MODEL
    output_dir = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUTPUT
    if not model_path.exists():
        print(f"[错误] 模型文件不存在：{model_path}")
        return 1

    import onnxruntime as ort

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    meta = dict(session.get_modelmeta().custom_metadata_map)
    if "names" not in meta:
        print("[错误] 模型元数据里没有 names，无法导出类别清单")
        return 1

    names = parse_names(meta["names"])
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    input_shape = [dim if isinstance(dim, int) else str(dim) for dim in inputs[0].shape]
    # 输入为 NCHW，取最后两维作为网络输入尺寸
    input_size = int(input_shape[-1]) if isinstance(input_shape[-1], int) else 640

    output_dir.mkdir(parents=True, exist_ok=True)
    labels_path = output_dir / "labels.json"
    preprocess_path = output_dir / "preprocess.json"
    manifest_path = output_dir / "model_manifest.json"

    labels_path.write_text(
        json.dumps({str(i): names[i] for i in sorted(names)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    preprocess = {
        "input_size": input_size,
        "task": meta.get("task", "unknown"),
        "head": meta.get("head", "unknown"),
        "stride": int(meta.get("stride", 32)),
        **ULTRALYTICS_PREPROCESS,
    }
    preprocess_path.write_text(
        json.dumps(preprocess, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    manifest = {
        "file": model_path.name,
        "sha256": sha256_of(model_path),
        "size_bytes": model_path.stat().st_size,
        "class_count": len(names),
        "input_shape": input_shape,
        "outputs": [
            {
                "name": out.name,
                "shape": [dim if isinstance(dim, int) else str(dim) for dim in out.shape],
            }
            for out in outputs
        ],
        "ultralytics_version": meta.get("version"),
        "exported_from": meta.get("description"),
        "export_date": meta.get("date"),
        "args": meta.get("args"),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"类别数：{len(names)}")
    print(f"输入：{input_shape}  输出：{[o['shape'] for o in manifest['outputs']]}")
    print(f"已写出：{labels_path.name} / {preprocess_path.name} / {manifest_path.name}")
    print(f"权重 sha256：{manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
