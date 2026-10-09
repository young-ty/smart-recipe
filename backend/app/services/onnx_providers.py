# -*- coding: utf-8 -*-
"""ONNX Runtime 执行提供程序的选择与校验（设计方案 7.3）。

为什么要单独一层：装了 GPU 版的 onnxruntime **不等于**跑在 GPU 上。本机实测
（2026-10-06，onnxruntime 1.19.2 + torch 2.3.1 自带的 cuDNN 8）：

```
ort.get_available_providers()
  -> ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']

ort.InferenceSession(model, providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
  .get_providers()
  -> ['CPUExecutionProvider']
```

`onnxruntime_providers_cuda.dll` 加载失败（错误码 126：cuDNN 9 与 CUDA 12 的运行库不在
搜索路径里），运行时**静默**回落到 CPU：不抛异常、不回退到上层降级链，只在 C++ 层打一行
错误。后果是两张不同的执行路径给出**完全一致**的 logits（最大绝对差 0.0）、单张耗时也
几乎一样（138.4 ms vs 142.8 ms）——只看「服务起没起来」发现不了自己跑在 CPU 上。

把 `nvidia-*` 的运行库补齐、并在建会话之前挂进搜索路径之后（`app/core/cuda_dlls.py`），
同一台机器的同一条命令给出 `['CUDAExecutionProvider', 'CPUExecutionProvider']`，
单张 p50 从 137 ms 落到 11 ms。

所以这里做三件事：解析 `RECIPE_ONNX_PROVIDERS`、按当前运行时的能力解析出最终列表、
最后**校验会话真正拿到了什么**并把事实报出来（加速没生效时是 WARNING，不是 INFO）。

解析不做「遇到不认识的名字就抛异常」：配置写错时抛异常会被上层降级链接住，识别直接
退回模拟实现——那比跑在 CPU 上更糟。这里把不认识或没装的名字收进 `missing` 照实报出来，
真模型继续在 CPU 上跑，问题在日志和 `/api/health` 里看得见。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from ..core.cuda_dlls import prepare_cuda_dll_paths

logger = logging.getLogger(__name__)

CPU_PROVIDER = "CPUExecutionProvider"

# 配置里用短名，代码里用全名：短名好写好记，全名是运行时的真实标识
PROVIDER_ALIASES = {
    "cpu": CPU_PROVIDER,
    "cuda": "CUDAExecutionProvider",
    "tensorrt": "TensorrtExecutionProvider",
    "dml": "DmlExecutionProvider",
    "directml": "DmlExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
    "rocm": "ROCMExecutionProvider",
    "migraphx": "MIGraphXExecutionProvider",
}

# auto：有 GPU 就用，没有就用 CPU。顺序即优先级，CPU 永远留在最后兜底。
AUTO_PREFERENCE = ("CUDAExecutionProvider", CPU_PROVIDER)
DEFAULT_PREFERENCE = "auto"


@dataclass(frozen=True)
class ProviderStatus:
    """一次会话创建的执行提供程序档案。"""

    preference: str
    requested: Tuple[str, ...]  # 按配置解析出来的理想顺序
    configured: Tuple[str, ...]  # 实际交给 InferenceSession 的列表
    granted: Tuple[str, ...]  # session.get_providers() 的真实结果
    missing: Tuple[str, ...]  # 想用但当前运行时没装 / 名字不认识的

    @property
    def accelerated(self) -> bool:
        return any(provider != CPU_PROVIDER for provider in self.granted)

    @property
    def requested_acceleration(self) -> bool:
        return any(provider != CPU_PROVIDER for provider in self.requested)

    @property
    def downgraded(self) -> bool:
        """要了加速却没拿到——配置看着是 GPU，实际在 CPU 上跑。"""

        return self.requested_acceleration and not self.accelerated

    def describe(self) -> str:
        text = "执行提供程序：%s" % (" → ".join(self.granted) or "无")
        if self.missing:
            text += "（配置里没生效的：%s）" % "、".join(self.missing)
        return text


def normalize_provider(name: str) -> str:
    """短名转全名；已经是全名或不认识的原样返回，交给可用性判断去筛。"""

    text = (name or "").strip()
    return PROVIDER_ALIASES.get(text.lower(), text)


def parse_preference(raw: Optional[str]) -> Tuple[str, ...]:
    """解析 RECIPE_ONNX_PROVIDERS：auto / cpu / cuda / 逗号分隔的一串。"""

    text = (raw or "").strip()
    if not text or text.lower() == DEFAULT_PREFERENCE:
        return AUTO_PREFERENCE
    ordered = []
    for chunk in text.split(","):
        provider = normalize_provider(chunk)
        if provider and provider not in ordered:
            ordered.append(provider)
    return tuple(ordered) or AUTO_PREFERENCE


def resolve_providers(
    requested: Sequence[str], available: Sequence[str]
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """按运行时能力筛一遍：返回 (交给运行时的列表, 没生效的名字)。"""

    installed = set(available)
    configured = [provider for provider in requested if provider in installed]
    missing = [provider for provider in requested if provider not in installed]
    # CPU 永远兜底：筛完为空（或者配置只写了 GPU）时，也不能让会话建不起来
    if CPU_PROVIDER in installed and CPU_PROVIDER not in configured:
        configured.append(CPU_PROVIDER)
    return tuple(configured), tuple(missing)


def available_providers() -> Tuple[str, ...]:
    """当前运行时支持的执行提供程序。"""

    prepare_cuda_dll_paths()
    import onnxruntime as ort

    return tuple(ort.get_available_providers())


def _default_session_factory(model_path, providers):
    prepare_cuda_dll_paths()
    import onnxruntime as ort

    return ort.InferenceSession(str(model_path), providers=list(providers))


def build_session(
    model_path,
    preference: Optional[str] = None,
    available: Optional[Sequence[str]] = None,
    session_factory: Optional[Callable] = None,
):
    """建会话并校验加速有没有真的生效，返回 (session, ProviderStatus)。

    `available` 与 `session_factory` 是给测试留的口子：没有 GPU 的机器也要能验证
    「解析出的顺序对不对」「回落有没有被识别出来」。
    """

    installed = tuple(available) if available is not None else available_providers()
    requested = parse_preference(preference)
    configured, missing = resolve_providers(requested, installed)
    factory = session_factory or _default_session_factory
    session = factory(model_path, configured)

    granted = tuple(getattr(session, "get_providers", lambda: configured)())
    status = ProviderStatus(
        preference=(preference or "").strip() or DEFAULT_PREFERENCE,
        requested=requested,
        configured=configured,
        granted=granted or configured,
        missing=missing,
    )
    report_status(status)
    return session, status


def report_status(status: ProviderStatus) -> None:
    """把会话的真实执行位置报出来。加速没生效是 WARNING。"""

    if status.downgraded:
        logger.warning(
            "推理加速没生效，实际跑在 %s：%s。onnxruntime-gpu 要 CUDA 12 + cuDNN 9 的"
            "运行库（cudart / cublas / cudnn / cufft 四件套），并且建会话之前挂进 DLL "
            "搜索路径；缺一个就会静默回落到 CPU（日志里只有一行 C++ 层的错误）",
            status.granted[0] if status.granted else "未知",
            status.describe(),
        )
        return
    logger.info("%s", status.describe())
