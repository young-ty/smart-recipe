"""演示前自检（backend/tools/preflight.py）的测试。

自检的唯一职责是「把启动后才会暴露的问题提前报到台面上」，所以每条判定都必须可测：
缺依赖要 FAIL、缺权重是 WARN（能降级跑但别这么演示）、端口被占要提示、
而且任何输出都不许带出 API Key。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import tools.preflight as pf  # noqa: E402
from app.core.config import Settings  # noqa: E402

FAKE_KEY = "sk-test-only-not-a-real-key-0000000000"


def _settings(tmp_path: Path, **overrides) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "seed_recipes.json").write_text(
        json.dumps([{"id": 1, "name": "麻婆豆腐"}]), encoding="utf-8"
    )
    options = {
        "database_url": "sqlite+pysqlite:///%s" % (tmp_path / "preflight.db").as_posix(),
        "data_dir": data_dir,
        "models_dir": tmp_path / "models",
        "generator_api_key": "",
    }
    options.update(overrides)
    return Settings(**options)


# --------------------------- Python 与依赖 ---------------------------

def test_python_version_is_checked_against_the_minimum():
    class _Version:
        def __init__(self, major, minor, micro=0):
            self.major, self.minor, self.micro = major, minor, micro

    assert pf.check_python(_Version(3, 9, 25)).status == pf.OK
    assert pf.check_python(_Version(3, 11, 0)).status == pf.OK
    low = pf.check_python(_Version(3, 8, 10))
    assert low.status == pf.FAIL and "3.9" in low.detail


def test_missing_backend_dependency_fails_and_names_the_fix():
    def importer(name):
        if name == "onnxruntime":
            raise ImportError(name)
        return object()

    result = pf.check_backend_deps(importer)
    assert result.status == pf.FAIL
    assert "onnxruntime" in result.detail
    assert "pip install" in result.detail


def test_all_backend_dependencies_present_is_ok():
    assert pf.check_backend_deps(lambda name: object()).status == pf.OK


# --------------------------- 模型与数据 ---------------------------

def test_missing_weights_is_a_warning_not_a_failure(tmp_path):
    """权重缺失会降级成模拟识别，起得来但别这么演示——所以是 WARN。"""

    result = pf.check_model_files(_settings(tmp_path))
    assert result.status == pf.WARN
    assert "dish.onnx" in result.detail


def test_complete_model_files_are_ok(tmp_path):
    settings = _settings(tmp_path)
    settings.models_dir.mkdir(parents=True)
    for name in (settings.dish_onnx_filename, settings.dish_labels_filename):
        (settings.models_dir / name).write_bytes(b"x" * 1024)
    settings.dish_map_path.write_text("{}", encoding="utf-8")

    assert pf.check_model_files(settings).status == pf.OK


def test_seed_data_is_validated(tmp_path):
    settings = _settings(tmp_path)
    assert pf.check_seed_data(settings).status == pf.OK

    (settings.data_dir / "seed_recipes.json").write_text("{ 坏掉的 json", encoding="utf-8")
    assert pf.check_seed_data(settings).status == pf.FAIL

    (settings.data_dir / "seed_recipes.json").unlink()
    assert pf.check_seed_data(settings).status == pf.FAIL


def test_database_check_reports_the_sqlite_file(tmp_path):
    result = pf.check_database(_settings(tmp_path))
    assert result.status == pf.OK
    assert "preflight.db" in result.detail


# --------------------------- 生成层与前端 ---------------------------

def test_generator_layer_reports_no_server_key_as_designed(tmp_path):
    """服务端不预置 Key 是设计，不是待修的配置项。"""

    result = pf.check_generator_layer(_settings(tmp_path))
    assert result.status == pf.OK
    assert "RECIPE" not in result.detail  # 提示里不该冒出变量名给不了操作
    assert "不预置 Key" in result.detail


def test_generator_layer_ok_reports_model_without_leaking_key(tmp_path):
    settings = _settings(tmp_path, generator_api_key=FAKE_KEY, generator_model="m")
    result = pf.check_generator_layer(settings)
    assert result.status == pf.OK
    assert "m" in result.detail
    assert "不参与线上生成" in result.detail
    assert FAKE_KEY not in result.detail


# --------------------------- 推理硬件 ---------------------------

CUDA = "CUDAExecutionProvider"
CPU = "CPUExecutionProvider"


def _settings_with_weights(tmp_path: Path) -> Settings:
    settings = _settings(tmp_path)
    settings.models_dir.mkdir(parents=True)
    (settings.models_dir / settings.dish_onnx_filename).write_bytes(b"x" * 1024)
    return settings


def _status(granted, requested=(CUDA, CPU), preference="auto"):
    from app.services.onnx_providers import ProviderStatus

    return ProviderStatus(
        preference=preference,
        requested=tuple(requested),
        configured=tuple(requested),
        granted=tuple(granted),
        missing=(),
    )


def test_inference_hardware_reports_the_granted_providers(tmp_path):
    result = pf.check_inference_hardware(
        _settings_with_weights(tmp_path), session_probe=lambda s: _status((CUDA, CPU))
    )
    assert result.status == pf.OK
    assert CUDA in result.detail


def test_inference_hardware_is_ok_when_cpu_was_asked_for(tmp_path):
    """配置就要 CPU 的时候没有「加速没生效」这回事。"""

    result = pf.check_inference_hardware(
        _settings_with_weights(tmp_path),
        session_probe=lambda s: _status((CPU,), requested=(CPU,), preference="cpu"),
    )
    assert result.status == pf.OK
    assert result.detail.startswith("CPU")
    assert "cpu" in result.detail, "配置口径也要报出来，方便对上 .env"


def test_inference_hardware_warns_when_acceleration_silently_falls_back(tmp_path, monkeypatch):
    from app.core import cuda_dlls

    monkeypatch.setattr(cuda_dlls, "nvidia_cuda_dirs", lambda *a, **k: ())
    result = pf.check_inference_hardware(
        _settings_with_weights(tmp_path), session_probe=lambda s: _status((CPU,))
    )
    assert result.status == pf.WARN
    assert "requirements-gpu.txt" in result.detail


def test_inference_hardware_points_at_the_loader_error_when_libraries_exist(tmp_path, monkeypatch):
    """运行库装了还被拒，说明问题在别处，别再让人去重复装一遍。"""

    from app.core import cuda_dlls

    monkeypatch.setattr(cuda_dlls, "nvidia_cuda_dirs", lambda *a, **k: ("nvidia/cudnn/bin",))
    result = pf.check_inference_hardware(
        _settings_with_weights(tmp_path), session_probe=lambda s: _status((CPU,))
    )
    assert result.status == pf.WARN
    assert "requirements-gpu.txt" not in result.detail


def test_inference_hardware_warns_without_weights_and_does_not_build_a_session(tmp_path):
    def boom(settings):
        raise AssertionError("权重缺失时不该去建会话")

    result = pf.check_inference_hardware(_settings(tmp_path), session_probe=boom)
    assert result.status == pf.WARN
    assert "权重" in result.detail


def test_inference_hardware_survives_a_broken_session(tmp_path):
    """会话建不起来也只是自检信息，不能把整个自检拖崩。"""

    def boom(settings):
        raise RuntimeError("缺 cudnn64_9.dll")

    result = pf.check_inference_hardware(_settings_with_weights(tmp_path), session_probe=boom)
    assert result.status == pf.WARN
    assert "cudnn64_9.dll" in result.detail


def test_frontend_check_requires_dependencies(tmp_path):
    repo = tmp_path / "repo"
    (repo / "frontend").mkdir(parents=True)
    assert pf.check_frontend(repo).status == pf.FAIL, "没有 package.json 应算失败"

    (repo / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    assert pf.check_frontend(repo).status == pf.FAIL, "没装依赖应算失败"

    (repo / "frontend" / "node_modules").mkdir()
    assert pf.check_frontend(repo).status == pf.OK


def test_port_check_reports_busy_ports():
    assert pf.check_ports(lambda port: False).status == pf.OK

    result = pf.check_ports(lambda port: port == pf.BACKEND_PORT)
    assert result.status == pf.WARN
    assert str(pf.BACKEND_PORT) in result.detail
    assert str(pf.FRONTEND_PORT) not in result.detail


# --------------------------- 汇总与入口 ---------------------------

def test_run_all_covers_every_check_and_leaks_nothing(tmp_path):
    settings = _settings(tmp_path, generator_api_key=FAKE_KEY)
    results = pf.run_all(settings, repo_root=tmp_path, probe=lambda port: False)
    names = {row.name for row in results}
    assert len(results) == 9
    assert {
        "Python 版本",
        "后端依赖",
        "模型与类别清单",
        "推理硬件",
        "种子数据",
        "数据库",
        "大模型 API",
        "前端",
        "端口",
    } == names
    assert FAKE_KEY not in pf.render(results)


def test_render_summarizes_by_worst_status(tmp_path):
    failing = [pf.CheckResult("甲", pf.OK, ""), pf.CheckResult("乙", pf.FAIL, "坏了")]
    assert "必须先修" in pf.render(failing)

    warning = [pf.CheckResult("甲", pf.OK, ""), pf.CheckResult("乙", pf.WARN, "注意")]
    assert "建议处理" in pf.render(warning)

    clean = [pf.CheckResult("甲", pf.OK, "好")]
    assert "可以启动" in pf.render(clean)


def test_main_returns_one_when_any_check_fails(monkeypatch, capsys):
    monkeypatch.setattr(pf, "run_all", lambda: [pf.CheckResult("甲", pf.FAIL, "坏了")])
    assert pf.main([]) == 1
    assert "坏了" in capsys.readouterr().out

    monkeypatch.setattr(pf, "run_all", lambda: [pf.CheckResult("甲", pf.OK, "好")])
    assert pf.main(["--quiet"]) == 0
    assert "OK 甲" in capsys.readouterr().out
