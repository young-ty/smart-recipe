# -*- coding: utf-8 -*-
"""把 nvidia-* 轮子里的 CUDA 运行库目录挂进进程的 DLL 搜索路径（设计方案 7.3.2）。

onnxruntime-gpu 在 Windows 上是延迟加载 CUDA 提供程序的：建会话的那一刻才去
``LoadLibrary`` 拉 ``onnxruntime_providers_cuda.dll``，而这个 DLL 自己还要再拉一串
运行库。用最小 PE 导入表解析读出来的直接依赖是：

===============  ==========================
DLL              pip 包
===============  ==========================
cudart64_12.dll  nvidia-cuda-runtime-cu12
cublas64_12.dll  nvidia-cublas-cu12
cublasLt64_12.dll nvidia-cublas-cu12
cudnn64_9.dll    nvidia-cudnn-cu12
cufft64_11.dll   nvidia-cufft-cu12
===============  ==========================

这些 DLL 都不在 onnxruntime 自己的目录里，而在同一个 site-packages 下的
``nvidia/<组件>/bin``。pip 装 nvidia 的轮子不会替我们改 PATH，Python 也不会自动加，
少任何一个都是 ``LoadLibrary failed with error 126``，随后运行时在 C++ 层打一行错误、
静默回落 CPU：不抛异常、不影响返回结果，只有耗时能看出来。

所以建会话之前先跑一次 :func:`prepare_cuda_dll_paths`：``os.add_dll_directory()``
登记目录，同时把目录前插 PATH。两个都做是因为 ``os.add_dll_directory`` 只对带
``LOAD_LIBRARY_SEARCH_*`` 标志的加载生效，而依赖解析走的是 PATH。

非 Windows 平台直接返回空：Linux 上这些 ``.so`` 要靠 ``LD_LIBRARY_PATH``，
进程起来之后改它没有意义，属于部署期的事。
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# 挂上的目录与句柄都留一份：os.add_dll_directory 返回的句柄一旦被回收，登记就失效
_prepared: Optional[Tuple[str, ...]] = None
_handles: List[object] = []


def _candidate_roots(paths: Optional[Sequence[str]] = None) -> List[Path]:
    """待搜索的 site-packages 候选：sys.path 加上 onnxruntime 自己所在的那一个。"""

    entries = list(sys.path if paths is None else paths)
    try:
        spec = importlib.util.find_spec("onnxruntime")
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.origin:
        entries.append(str(Path(spec.origin).resolve().parent.parent))
    roots: List[Path] = []
    seen = set()
    for entry in entries:
        if not entry:
            continue
        key = str(entry)
        if key in seen:
            continue
        seen.add(key)
        roots.append(Path(entry))
    return roots


def nvidia_cuda_dirs(paths: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    """找出所有装了 DLL 的 ``nvidia/<组件>/bin`` 目录，按组件名排序。"""

    found: List[str] = []
    seen = set()
    for root in _candidate_roots(paths):
        vendor = root / "nvidia"
        if not vendor.is_dir():
            continue
        for bin_dir in sorted(vendor.glob("*/bin")):
            key = str(bin_dir)
            if key in seen or not bin_dir.is_dir():
                continue
            if not any(bin_dir.glob("*.dll")):
                continue
            seen.add(key)
            found.append(key)
    return tuple(found)


def _prepend_path(dirs: Sequence[str]) -> None:
    parts = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    existing = set(dirs)
    ordered = list(dirs) + [part for part in parts if part not in existing]
    os.environ["PATH"] = os.pathsep.join(ordered)


def prepare_cuda_dll_paths(paths: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    """把 CUDA 运行库目录挂进当前进程，返回挂上的目录。

    进程内只真正执行一次，之后直接返回上次的结果；没装 nvidia 的运行库时返回空元组，
    调用方不需要先判断环境。
    """

    global _prepared
    if _prepared is not None:
        return _prepared
    dirs = nvidia_cuda_dirs(paths)
    if dirs and IS_WINDOWS:
        for directory in dirs:
            try:
                _handles.append(os.add_dll_directory(directory))
            except OSError as exc:
                logger.warning("登记 CUDA 运行库目录失败：%s（%s）", directory, exc)
        _prepend_path(dirs)
    _prepared = dirs
    if dirs:
        logger.info("CUDA 运行库目录已挂载：%s", "、".join(dirs))
    return dirs


def reset_cache() -> None:
    """清掉「只挂一次」的缓存，供测试构造干净的起点。"""

    global _prepared
    _prepared = None
