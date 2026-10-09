"""判定训练产出的 .pt 权重属于哪种保存格式，必要时转成 ONNX。

训练脚本写出的 .pt 至少有四种来源，能不能脱离训练源码用起来，差别很大：

1. state_dict       —— ``torch.save(model.state_dict(), p)``：只有张量，必须配一份模型定义才能还原；
2. 整模型           —— ``torch.save(model, p)``：pickle 里带着类路径，加载时要能 import 到训练时那个类；
3. 训练 checkpoint  —— ``torch.save({"epoch": …, "model": …}, p)``：多一层字典，权重藏在某个键下面；
4. TorchScript      —— ``torch.jit.save(traced, p)``：自带计算图，可脱离源码加载，也能直接转 ONNX。

本脚本默认**不反序列化**权重（unpickle 会执行文件里的任意代码），只读 zip 结构、
以及 pickle 指令流里的类名与字符串常量，据此给出结论。真正加载模型只在
``--export-onnx`` 时发生。

用法：
    .venv\\Scripts\\python.exe backend/tools/inspect_checkpoint.py models/dish.pt
    .venv\\Scripts\\python.exe backend/tools/inspect_checkpoint.py models/dish.pt ^
        --export-onnx models/dish.onnx --timm resnet50d

timm 架构（架构名形如 ``resnet50d.ra2_in1k``）必须用 ``--timm`` 重建：torchvision 有同名模型，
但缺 deep stem 与 avg_down，权重键对不上。torchvision 原生架构才用 ``--arch``。
导出成功后会在 ONNX 旁边写一份 ``<名字>.manifest.json``，记下来源、类别数与预处理契约。
"""

from __future__ import annotations

import hashlib
import json
import pickletools
import sys
import zipfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# pickle 指令流里出现的类名，用来判断"这份权重里有没有模型结构"
MODULE_HINTS = ("torchvision.models", "torch.nn.modules", "torch.nn.")
# 训练脚本常见的 checkpoint 顶层键；命中说明权重藏在字典里而不是直接的 state_dict
CHECKPOINT_KEYS = (
    "epoch",
    "optimizer",
    "model_state_dict",
    "state_dict",
    "best_acc",
    "best_epoch",
)
TENSOR_GLOBAL = "torch._utils _rebuild_tensor_v2"
# checkpoint 里承载权重的常见键名，按优先级排列
STATE_DICT_KEYS = ("model", "model_state_dict", "state_dict", "net", "backbone")
# checkpoint 里出现这些字样，多半就是 timm 的架构名（形如 resnet50d.ra2_in1k）
ARCH_TOKENS = (
    "resnet", "resnext", "regnet", "vit", "swin", "convnext",
    "efficientnet", "densenet", "mobilenet", "vgg",
)
_TORCHSCRIPT_PREFIX = "__torch__."

_UNICODE_OPS = (
    "SHORT_BINUNICODE",
    "BINUNICODE",
    "BINUNICODE8",
    "UNICODE",
    "SHORT_BINSTRING",
    "BINSTRING",
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pickle_blob(path: Path, is_zip: bool, members: Sequence[str]) -> Tuple[Optional[str], bytes]:
    """取出 pickle 字节流；不做反序列化。"""

    if not is_zip:
        return None, path.read_bytes()
    with zipfile.ZipFile(path) as archive:
        name = next((item for item in members if item.endswith("data.pkl")), None)
        return name, archive.read(name) if name else b""


def _scan_pickle(blob: bytes) -> Tuple[List[str], List[str]]:
    """返回 (引用的全局类名, 字符串常量)，只解析指令流，不执行。"""

    globals_found: List[str] = []
    strings: List[str] = []
    try:
        for opcode, arg, _ in pickletools.genops(blob):
            if opcode.name in ("GLOBAL", "STACK_GLOBAL") and arg:
                globals_found.append(str(arg))
            elif opcode.name in _UNICODE_OPS and arg is not None:
                strings.append(str(arg))
    except Exception:  # 截断或非标准 pickle：拿到的部分已经够判定
        pass
    return globals_found, strings


def inspect(path: Path) -> dict:
    """读容器与 pickle 指令流，给出格式判定；不加载模型。"""

    with path.open("rb") as handle:
        head = handle.read(4)
    is_zip = head[:2] == b"PK"
    members: List[str] = []
    if is_zip:
        with zipfile.ZipFile(path) as archive:
            members = archive.namelist()

    pickle_name, blob = _pickle_blob(path, is_zip, members)
    globals_found, strings = _scan_pickle(blob)

    jit_ok, jit_error = _try_jit_load(path)

    module_classes = sorted(
        {name for name in globals_found if any(hint in name for hint in MODULE_HINTS)}
    )
    has_tensors = TENSOR_GLOBAL in globals_found
    checkpoint_keys = [key for key in CHECKPOINT_KEYS if key in strings]

    if jit_ok:
        kind = "torchscript"
    elif any(name.startswith(_TORCHSCRIPT_PREFIX) for name in globals_found):
        kind = "torchscript"
    elif module_classes:
        kind = "whole_model"
    elif checkpoint_keys:
        kind = "training_checkpoint"
    elif has_tensors:
        kind = "state_dict"
    else:
        kind = "unknown"

    arch_hints = sorted(
        {
            value
            for value in strings
            if " " not in value and any(token in value.lower() for token in ARCH_TOKENS)
        }
    )

    return {
        "path": str(path),
        "arch_hints": arch_hints,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_of(path),
        "container": "zip" if is_zip else "legacy-pickle",
        "archive_root": _archive_root(members),
        "member_count": len(members),
        "pickle_name": pickle_name,
        "kind": kind,
        "torchscript_loadable": jit_ok,
        "torchscript_error": jit_error,
        "module_classes": module_classes,
        "referenced_globals": sorted(set(globals_found)),
        "checkpoint_keys": checkpoint_keys,
        "has_tensors": has_tensors,
    }


def _archive_root(members: Sequence[str]) -> Optional[str]:
    """新格式的成员形如 ``state_dict/data.pkl``，第一段就是 torch.save 用的存档名。"""

    if not members:
        return None
    first = members[0].split("/", 1)
    return first[0] if len(first) == 2 else None


def _try_jit_load(path: Path) -> Tuple[bool, Optional[str]]:
    try:
        import torch
    except ImportError:
        return False, "本环境没有 torch，无法判断是否为 TorchScript"
    try:
        torch.jit.load(str(path), map_location="cpu")
        return True, None
    except Exception as exc:  # noqa: BLE001 - 失败原因需要原样展示给用户
        return False, str(exc).splitlines()[0][:160]


def verdict(info: dict) -> List[str]:
    """把判定翻译成"能不能用、下一步做什么"。"""

    kind = info["kind"]
    lines: List[str] = []
    if kind == "torchscript":
        lines.append("可脱离训练源码加载，也能直接转 ONNX；这一份不必返工。")
        lines.append("优先补一份类别清单（类别索引 → 菜名），索引顺序必须与训练一致。")
    elif kind == "whole_model":
        classes = "、".join(info["module_classes"][:3]) or "未知"
        lines.append(f"pickle 里带着模型类：{classes}")
        lines.append("能 import 到同一个类即可加载；这里就是 torchvision 标准 ResNet，本仓库能重建。")
        lines.append("仍需类别清单：类结构还原得出来，菜名还原不出来。")
    elif kind == "state_dict":
        lines.append("只有张量，没有模型结构。必须配一份模型定义才能还原。")
        lines.append("若训练用的就是 torchvision 标准 ResNet50，本仓库可直接重建并加载；自定义结构则需要训练代码。")
        lines.append("类别清单同样必须单独提供。")
    elif kind == "training_checkpoint":
        keys = "、".join(info["checkpoint_keys"])
        lines.append(f"这是训练脚本的 checkpoint（顶层键：{keys}），权重藏在其中一个键下面。")
        lines.append("取出对应键后按 state_dict 处理；其余键（优化器、轮次）部署时不要。")
        if info.get("arch_hints"):
            hint = info["arch_hints"][0]
            lines.append(f"权重里的架构名是 {hint}：多半来自 timm，torchvision 的同名模型结构对不上。")
            lines.append(f"重建时用 --timm {hint.split('.')[0]}，不要用 --arch。")
    else:
        lines.append("无法判定格式：既没有张量重建指令，也没有可识别的类名。")
        lines.append("可能是空的、截断的，或者不是 torch 存档。请重新导出一次。")

    lines.append("不要直接把 .pt 放进后端：运行依赖里只有 onnxruntime，装 torch 会拖垮本地启动。")
    lines.append("转成 ONNX 后即可复用后端 OnnxDishRecognizer 的加载与降级路径。")
    return lines


def is_tensor_map(value) -> bool:
    """是不是"键 → 张量"的字典（state_dict 的形状）。"""

    return (
        isinstance(value, dict)
        and bool(value)
        and all(hasattr(item, "shape") for item in value.values())
    )


def unwrap_state_dict(payload):
    """从训练 checkpoint 里取出真正的权重。

    不能按"第一个看起来像字典的键"取：``optimizer`` 也是字典，而且排在 ``model`` 前面，
    取错了会得到 ``{"state", "param_groups"}`` 然后加载失败。这里按"值是不是张量"判断，
    并优先认 ``model`` / ``state_dict`` 这些惯用名。
    """

    if is_tensor_map(payload):
        return payload
    if not isinstance(payload, dict):
        return payload
    candidates = [key for key, value in payload.items() if is_tensor_map(value)]
    for name in STATE_DICT_KEYS:
        if name in candidates:
            return payload[name]
    return payload[candidates[0]] if len(candidates) == 1 else payload


def export_onnx(
    path: Path,
    out_path: Path,
    info: dict,
    arch: str = "resnet50",
    num_classes: Optional[int] = None,
    input_size: int = 224,
    timm_name: Optional[str] = None,
) -> dict:
    """加载权重并导出 ONNX，同时在旁边写一份 manifest。只有走到这里才会反序列化文件。"""

    import torch

    def _load(obj_path: Path):
        try:
            return torch.load(str(obj_path), map_location="cpu", weights_only=False)
        except TypeError:  # torch < 2.0 不认识 weights_only
            return torch.load(str(obj_path), map_location="cpu")

    kind = info["kind"]
    meta: dict = {}
    if kind == "torchscript":
        model = torch.jit.load(str(path), map_location="cpu")
    elif kind == "whole_model":
        model = _load(path)
    else:
        payload = _load(path)
        if isinstance(payload, dict):
            for key in ("epoch", "best_top1", "best_acc"):
                if key in payload:
                    meta[key] = payload[key]
        payload = unwrap_state_dict(payload)
        if isinstance(payload, dict) and not num_classes:
            head = next((key for key in payload if key.endswith("fc.weight")), None)
            if head is not None:
                num_classes = int(payload[head].shape[0])
        if timm_name:
            import timm

            model = timm.create_model(
                timm_name, pretrained=False, num_classes=num_classes or 1000
            )
        else:
            import torchvision

            model = getattr(torchvision.models, arch)(weights=None)
            if num_classes and num_classes != model.fc.out_features:
                model.fc = torch.nn.Linear(model.fc.in_features, num_classes)
        model.load_state_dict(payload)

    model.eval()
    dummy = torch.randn(1, 3, input_size, input_size)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with torch.no_grad():
        torch.onnx.export(
            model,
            dummy,
            str(out_path),
            opset_version=12,
            input_names=["input"],
            output_names=["logits"],
            dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        )

    result = verify_onnx(out_path)
    result["manifest"] = _write_manifest(
        path, out_path, info, result, timm_name or arch, meta, model,
        arch_source="timm" if timm_name else "torchvision",
    )
    result["manifest_path"] = str(out_path.with_suffix(".manifest.json"))
    return result


def resolve_preprocess(model) -> Optional[dict]:
    """从 timm 取该架构的推理配置：这是预处理契约，不是可以猜的东西。"""

    try:
        import timm
    except ImportError:
        return None
    try:
        config = timm.data.resolve_data_config({}, model=model)
    except Exception:  # noqa: BLE001 - 拿不到就如实返回 None，不编造默认值
        return None
    return {
        "input_size": [int(dim) for dim in config["input_size"]],
        "interpolation": str(config.get("interpolation", "bicubic")),
        "mean": [float(value) for value in config["mean"]],
        "std": [float(value) for value in config["std"]],
        "crop_pct": float(config.get("crop_pct", 0.875)),
        "crop_mode": str(config.get("crop_mode", "center")),
        "layout": "NCHW",
        "color_order": "RGB",
        "scale": 1 / 255,
    }


def _write_manifest(
    source: Path,
    out_path: Path,
    info: dict,
    result: dict,
    arch: str,
    meta: dict,
    model,
    arch_source: str = "torchvision",
) -> dict:
    """把"这份 ONNX 是怎么来的、输入怎么喂"写死，供后端与训练产出对齐。"""

    manifest = {
        "source_file": source.name,
        "source_sha256": info["sha256"],
        "source_kind": info["kind"],
        "arch": arch,
        "arch_source": arch_source,
        "num_classes": result["class_count"],
        "onnx_file": out_path.name,
        "onnx_sha256": sha256_of(out_path),
        "onnx_size_bytes": out_path.stat().st_size,
        "input": {"name": "input", "shape": result["input_shape"]},
        "output": {"name": "logits", "shape": result["output_shape"]},
        "checkpoint_meta": meta,
        "preprocess": resolve_preprocess(model),
        "labels": "缺少类别清单：类别索引 → 菜名必须随权重一并提供，无法从权重里还原",
    }
    out_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def verify_onnx(path: Path) -> dict:
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    probe = np.zeros(
        [dim if isinstance(dim, int) else 1 for dim in inputs[0].shape], dtype=np.float32
    )
    logits = session.run(None, {inputs[0].name: probe})[0]
    return {
        "input_shape": [dim if isinstance(dim, int) else str(dim) for dim in inputs[0].shape],
        "output_shape": [dim if isinstance(dim, int) else str(dim) for dim in outputs[0].shape],
        "class_count": int(logits.shape[-1]),
        "probe_top1": int(logits[0].argmax()),
        "size_bytes": path.stat().st_size,
    }


def main(argv: List[str]) -> int:
    args = [item for item in argv[1:] if not item.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    path = Path(args[0])
    if not path.exists():
        print(f"[错误] 文件不存在：{path}")
        return 1

    info = inspect(path)
    print(f"文件：{path}")
    print(f"大小：{info['size_bytes'] / 1024 / 1024:.1f} MB    sha256：{info['sha256'][:16]}…")
    print(f"容器：{info['container']}    存档名：{info['archive_root']}    成员数：{info['member_count']}")
    print(f"TorchScript 可加载：{'是' if info['torchscript_loadable'] else '否'}"
          + ("" if info["torchscript_loadable"] else f"（{info['torchscript_error']}）"))
    if info["module_classes"]:
        print("pickle 里引用的模型类：")
        for name in info["module_classes"][:8]:
            print(f"  - {name}")
    if info["arch_hints"]:
        print("权重里出现的架构名：" + "、".join(info["arch_hints"][:4]))
    print(f"判定：{info['kind']}")
    print("-" * 68)
    for line in verdict(info):
        print(f"· {line}")

    onnx_out = _value_after(argv, "--export-onnx")
    if onnx_out:
        arch = _value_after(argv, "--arch") or "resnet50"
        timm_name = _value_after(argv, "--timm")
        classes = _value_after(argv, "--num-classes")
        size = _value_after(argv, "--input-size")
        print("-" * 68)
        print(
            f"导出 ONNX：{'timm ' + timm_name if timm_name else 'torchvision ' + arch}"
            f" num_classes={classes or '自动'} input={size or 224}"
        )
        result = export_onnx(
            path,
            Path(onnx_out),
            info,
            arch=arch,
            num_classes=int(classes) if classes else None,
            input_size=int(size) if size else 224,
            timm_name=timm_name,
        )
        print(f"  输入 {result['input_shape']} → 输出 {result['output_shape']}")
        print(f"  类别数：{result['class_count']}（推理空图 top1={result['probe_top1']}，仅用于确认形状）")
        print(f"  已写出：{onnx_out}（{result['size_bytes'] / 1024 / 1024:.1f} MB）")
        print(f"  已写出：{result['manifest_path']}")
        preprocess = result["manifest"]["preprocess"]
        if preprocess:
            print(
                "  预处理：%s×%s %s，crop_pct=%.3f，mean=%s，std=%s"
                % (
                    preprocess["input_size"][-1],
                    preprocess["input_size"][-2],
                    preprocess["interpolation"],
                    preprocess["crop_pct"],
                    preprocess["mean"],
                    preprocess["std"],
                )
            )
        print("  提醒：类别清单仍需单独提供，本脚本无法从权重里还原菜名。")
    return 0


def _value_after(argv: Sequence[str], name: str) -> Optional[str]:
    for index, item in enumerate(argv):
        if item == name and index + 1 < len(argv):
            return argv[index + 1]
    return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
