"""执行提供程序解析与校验（backend/app/services/onnx_providers.py）的测试。

核心用例是「静默回落」：运行时装了 CUDA、配置也要了 CUDA，但会话只拿到 CPU。
本机（onnxruntime 1.19.2 + torch 自带 cuDNN 8）实测就是这个状态——不报错、不降级，
logits 与耗时和纯 CPU 一模一样，所以必须由这一层把它判成降级并告警。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services import onnx_providers as providers  # noqa: E402

AVAILABLE = ("TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider")
CPU = "CPUExecutionProvider"
CUDA = "CUDAExecutionProvider"


class FakeSession:
    """只实现被校验用到的那一个方法：会话真正拿到了哪些提供程序。"""

    def __init__(self, granted) -> None:
        self._granted = list(granted)

    def get_providers(self):
        return list(self._granted)


def make_factory(granted):
    captured = {}

    def _factory(model_path, configured):
        captured["model_path"] = model_path
        captured["configured"] = tuple(configured)
        return FakeSession(granted)

    return _factory, captured


# --------------------------------------------------------------- 解析

def test_auto_prefers_acceleration_and_keeps_cpu_last():
    assert providers.parse_preference("auto") == (CUDA, CPU)
    assert providers.parse_preference(None) == providers.AUTO_PREFERENCE
    assert providers.parse_preference("") == providers.AUTO_PREFERENCE
    assert providers.parse_preference("  AUTO ") == providers.AUTO_PREFERENCE


def test_short_names_map_to_runtime_names():
    assert providers.parse_preference("cpu") == (CPU,)
    assert providers.parse_preference("cuda,cpu") == (CUDA, CPU)
    assert providers.normalize_provider("directml") == "DmlExecutionProvider"
    assert providers.parse_preference("TensorrtExecutionProvider") == (
        "TensorrtExecutionProvider",
    )


def test_unknown_names_are_reported_instead_of_raising():
    """配置写错时抛异常会被上层降级链接住，识别直接退回模拟实现，比跑在 CPU 上更糟。"""

    assert providers.parse_preference("cudo") == ("cudo",)
    assert providers.parse_preference("cuda, cudo") == (CUDA, "cudo")
    assert providers.parse_preference("cuda,cuda") == (CUDA,)


# --------------------------------------------------------------- 按能力筛选

def test_resolve_keeps_installed_and_reports_missing():
    configured, missing = providers.resolve_providers(
        (CUDA, "OpenVINOExecutionProvider"), AVAILABLE
    )
    assert configured == (CUDA, CPU)
    assert missing == ("OpenVINOExecutionProvider",)


def test_resolve_falls_back_to_cpu_when_nothing_requested_is_installed():
    configured, missing = providers.resolve_providers(("OpenVINOExecutionProvider",), AVAILABLE)
    assert configured == (CPU,)
    assert missing == ("OpenVINOExecutionProvider",)


def test_resolve_keeps_an_explicit_order():
    configured, _ = providers.resolve_providers((CPU, CUDA), AVAILABLE)
    assert configured == (CPU, CUDA)


# --------------------------------------------------------------- 建会话与校验

def test_build_session_reports_granted_providers():
    factory, captured = make_factory([CUDA, CPU])
    _, status = providers.build_session(
        "m.onnx", "auto", available=AVAILABLE, session_factory=factory
    )
    assert captured["configured"] == (CUDA, CPU)
    assert captured["model_path"] == "m.onnx"
    assert status.granted == (CUDA, CPU)
    assert status.accelerated is True
    assert status.downgraded is False
    assert status.missing == ()


def test_build_session_flags_a_silent_fallback_to_cpu():
    """要了 CUDA、运行时也支持 CUDA，会话却只拿到 CPU：这就是本机的实际状态。"""
    factory, _ = make_factory([CPU])
    _, status = providers.build_session(
        "m.onnx", "auto", available=AVAILABLE, session_factory=factory
    )
    assert status.granted == (CPU,)
    assert status.requested_acceleration is True
    assert status.accelerated is False
    assert status.downgraded is True


def test_cpu_only_configuration_is_not_a_downgrade():
    factory, _ = make_factory([CPU])
    _, status = providers.build_session(
        "m.onnx", "cpu", available=AVAILABLE, session_factory=factory
    )
    assert status.downgraded is False
    assert status.accelerated is False


def test_downgrade_is_reported_as_a_warning(caplog):
    factory, _ = make_factory([CPU])
    with caplog.at_level("WARNING"):
        providers.build_session("m.onnx", "cuda", available=AVAILABLE, session_factory=factory)
    assert any("加速没生效" in record.getMessage() for record in caplog.records)


def test_missing_provider_is_named_in_the_status():
    factory, _ = make_factory([CPU])
    _, status = providers.build_session(
        "m.onnx", "cuda,openvino", available=AVAILABLE, session_factory=factory
    )
    assert status.missing == ("OpenVINOExecutionProvider",)
    assert "OpenVINOExecutionProvider" in status.describe()
    assert status.downgraded is True


def test_status_describe_lists_every_granted_provider():
    status = providers.ProviderStatus(
        preference="auto",
        requested=(CUDA, CPU),
        configured=(CUDA, CPU),
        granted=(CUDA, CPU),
        missing=(),
    )
    assert status.describe() == "执行提供程序：%s → %s" % (CUDA, CPU)


def test_session_without_get_providers_falls_back_to_the_configured_list():
    """老运行时或替身没有 get_providers 时，按配置列表报，别编。"""
    def factory(model_path, configured):
        return object()

    _, status = providers.build_session(
        "m.onnx", "cpu", available=AVAILABLE, session_factory=factory
    )
    assert status.granted == (CPU,)


def test_real_runtime_is_importable_and_offers_cpu():
    pytest.importorskip("onnxruntime")
    assert CPU in providers.available_providers()


# --------------------------- CUDA 运行库挂载 ---------------------------

def test_available_providers_mounts_cuda_dll_dirs_first(monkeypatch):
    """列提供程序之前必须先挂 CUDA 运行库目录，否则后面建会话只能拿到 CPU。"""

    pytest.importorskip("onnxruntime")
    calls = []
    monkeypatch.setattr(
        providers, "prepare_cuda_dll_paths", lambda *a, **k: calls.append("mount") or ()
    )
    providers.available_providers()
    assert calls == ["mount"]


def test_session_factory_mounts_cuda_dll_dirs_first(monkeypatch):
    """调用方直接传 available 时不会走 available_providers，建会话这步也不能漏。"""

    pytest.importorskip("onnxruntime")
    calls = []
    monkeypatch.setattr(
        providers, "prepare_cuda_dll_paths", lambda *a, **k: calls.append("mount") or ()
    )
    with pytest.raises(Exception):
        providers._default_session_factory("no-such-model.onnx", [CPU])
    assert calls == ["mount"]
