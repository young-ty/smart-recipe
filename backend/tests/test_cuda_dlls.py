"""CUDA 运行库目录挂载（backend/app/core/cuda_dlls.py）的测试。

这一层的价值全在「建会话之前把目录挂上」：漏掉不会报错，只会静默跑在 CPU 上。
用例覆盖目录发现、去重、幂等，以及非 Windows 不动 PATH。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core import cuda_dlls  # noqa: E402


def make_vendor_tree(root: Path, components: Dict[str, List[str]]) -> Path:
    """照着 nvidia-* 轮子的布局造一棵假目录树。"""

    for name, dlls in components.items():
        bin_dir = root / "nvidia" / name / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for dll in dlls:
            (bin_dir / dll).write_bytes(b"MZ")
    return root


@pytest.fixture(autouse=True)
def clean_cache():
    """每个用例从「没挂过」的状态开始，避免模块级缓存串味。"""

    cuda_dlls.reset_cache()
    yield
    cuda_dlls.reset_cache()


# --------------------------- 目录发现 ---------------------------

def test_finds_dll_bearing_component_dirs(tmp_path):
    make_vendor_tree(
        tmp_path, {"cudnn": ["cudnn64_9.dll"], "cublas": ["cublas64_12.dll"]}
    )
    dirs = cuda_dlls.nvidia_cuda_dirs([str(tmp_path)])
    assert len(dirs) == 2
    assert dirs[0].endswith(os.path.join("nvidia", "cublas", "bin"))
    assert dirs[1].endswith(os.path.join("nvidia", "cudnn", "bin"))


def test_skips_components_without_dlls(tmp_path):
    make_vendor_tree(tmp_path, {"cudnn": ["cudnn64_9.dll"], "empty": []})
    (tmp_path / "nvidia" / "no-bin-dir").mkdir(parents=True)
    assert len(cuda_dlls.nvidia_cuda_dirs([str(tmp_path)])) == 1


def test_returns_empty_without_a_vendor_dir(tmp_path):
    assert cuda_dlls.nvidia_cuda_dirs([str(tmp_path)]) == ()


def test_ignores_blank_and_duplicate_entries(tmp_path):
    make_vendor_tree(tmp_path, {"cudnn": ["cudnn64_9.dll"]})
    dirs = cuda_dlls.nvidia_cuda_dirs([str(tmp_path), str(tmp_path), "", None])
    assert len(dirs) == 1


def test_candidate_roots_include_the_onnxruntime_site_packages():
    """onnxruntime 与 nvidia-* 未必装在同一个 prefix，两个都要找。"""

    pytest.importorskip("onnxruntime")
    import onnxruntime

    expected = str(Path(onnxruntime.__file__).resolve().parent.parent)
    assert expected in [str(root) for root in cuda_dlls._candidate_roots([])]


# --------------------------- 挂载行为 ---------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="os.add_dll_directory 只在 Windows 上存在")
def test_prepare_registers_dirs_and_prepends_path(tmp_path, monkeypatch):
    make_vendor_tree(
        tmp_path, {"cudnn": ["cudnn64_9.dll"], "cublas": ["cublas64_12.dll"]}
    )
    monkeypatch.setenv("PATH", "baseline")
    dirs = cuda_dlls.prepare_cuda_dll_paths([str(tmp_path)])
    assert len(dirs) == 2
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[:2] == list(dirs)
    assert "baseline" in parts


def test_prepare_does_not_touch_path_off_windows(tmp_path, monkeypatch):
    """Linux 上要靠 LD_LIBRARY_PATH，进程内改没有意义，所以这里不碰 PATH。"""

    make_vendor_tree(tmp_path, {"cudnn": ["cudnn64_9.dll"]})
    monkeypatch.setattr(cuda_dlls, "IS_WINDOWS", False)
    monkeypatch.setenv("PATH", "baseline")
    assert len(cuda_dlls.prepare_cuda_dll_paths([str(tmp_path)])) == 1
    assert os.environ["PATH"] == "baseline"


def test_prepare_is_a_noop_without_nvidia_wheels(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "baseline")
    assert cuda_dlls.prepare_cuda_dll_paths([str(tmp_path)]) == ()
    assert os.environ["PATH"] == "baseline"


def test_prepare_is_idempotent(tmp_path, monkeypatch):
    make_vendor_tree(tmp_path, {"cudnn": ["cudnn64_9.dll"]})
    monkeypatch.setenv("PATH", "baseline")
    first = cuda_dlls.prepare_cuda_dll_paths([str(tmp_path)])
    after_first = os.environ["PATH"]
    assert cuda_dlls.prepare_cuda_dll_paths([str(tmp_path)]) == first
    assert os.environ["PATH"] == after_first


def test_result_is_cached_until_reset(tmp_path):
    make_vendor_tree(tmp_path, {"cudnn": ["cudnn64_9.dll"]})
    assert len(cuda_dlls.prepare_cuda_dll_paths([str(tmp_path)])) == 1
    make_vendor_tree(tmp_path, {"cublas": ["cublas64_12.dll"]})
    assert len(cuda_dlls.prepare_cuda_dll_paths([str(tmp_path)])) == 1
    cuda_dlls.reset_cache()
    assert len(cuda_dlls.prepare_cuda_dll_paths([str(tmp_path)])) == 2


# --------------------------- 本机真环境 ---------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="CUDA 运行库挂载只在 Windows 上验证")
def test_local_machine_engages_cuda_when_the_runtime_is_installed():
    """装了 nvidia-* 运行库的机器上，会话必须真的拿到 CUDAExecutionProvider。

    这是整个改动唯一的验收点：漏挂目录不会报错，只会静默跑在 CPU 上。
    """

    pytest.importorskip("onnxruntime")
    if not cuda_dlls.nvidia_cuda_dirs():
        pytest.skip("本机没有 nvidia-* 运行库")
    from app.services import onnx_providers

    if "CUDAExecutionProvider" not in onnx_providers.available_providers():
        pytest.skip("本机的 onnxruntime 是 CPU 版")
    weights = Path(__file__).resolve().parent.parent.parent / "models" / "dish.onnx"
    if not weights.exists():
        pytest.skip("尚未产出 models/dish.onnx")

    session, status = onnx_providers.build_session(str(weights), preference="cuda")
    assert status.accelerated, status.describe()
    assert "CUDAExecutionProvider" in session.get_providers()
