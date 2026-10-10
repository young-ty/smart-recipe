r"""演示前自检：把所有「启动那一刻才会发现」的问题提前报到台面上。

为什么单独做一个脚本：演示当天最怕的不是功能不够，而是环境不对——虚拟环境没建全、
权重没拷过来、.env 忘了填、端口被上一次没关干净的服务占着、前端没装依赖。
这些问题的共同点是**等到演示开始才暴露**，而现场排查的时间成本很高。

检查项分三档：

- OK：可用；
- WARN：能跑但建议先处理（例如没配大模型 Key——库外菜会退化成提示态）；
- FAIL：跑不起来，必须先修。

用法（在仓库根目录或 backend 目录下都可以）：

    .\.venv\Scripts\python.exe backend\tools\preflight.py

退出码 0 = 没有 FAIL；1 = 存在 FAIL。`start-demo.ps1` 会先跑它，不通过就不启动。
"""

from __future__ import annotations

import argparse
import importlib
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings, get_settings  # noqa: E402
from tools.check_generator import describe, mask_secret  # noqa: E402

OK, WARN, FAIL = "OK", "WARN", "FAIL"

BACKEND_PORT = 8000
FRONTEND_PORT = 5173
MIN_PYTHON = (3, 9)

# 后端运行时真正 import 的包：缺一个就起不来
RUNTIME_MODULES = ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "httpx", "onnxruntime", "cv2", "PIL", "numpy")


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """端口上已经有人监听。用 connect 探测，不依赖 netstat 的本地化输出。"""

    with socket.socket() as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def check_python(version_info=None) -> CheckResult:
    version = version_info or sys.version_info
    current = (version.major, version.minor)
    if current < MIN_PYTHON:
        return CheckResult("Python 版本", FAIL, "%d.%d 低于要求的 %d.%d" % (current + MIN_PYTHON))
    return CheckResult("Python 版本", OK, "%d.%d.%d" % (version.major, version.minor, version.micro))


def check_backend_deps(importer: Callable[[str], object] = importlib.import_module) -> CheckResult:
    missing = []
    for name in RUNTIME_MODULES:
        try:
            importer(name)
        except Exception:  # 缺包 / 装坏了都算缺
            missing.append(name)
    if missing:
        return CheckResult(
            "后端依赖",
            FAIL,
            "缺 %s（装：.venv\\Scripts\\python.exe -m pip install -r backend/requirements.txt）"
            % "、".join(missing),
        )
    return CheckResult("后端依赖", OK, "%d 个运行时包都在" % len(RUNTIME_MODULES))


def check_model_files(settings: Settings) -> CheckResult:
    """权重缺失不算失败：识别会自动降级为模拟实现，但演示别这么跑。"""

    missing = [
        path.name
        for path in (settings.dish_onnx_path, settings.dish_labels_path, settings.dish_map_path)
        if not path.is_file()
    ]
    if missing:
        return CheckResult("模型与类别清单", WARN, "缺 %s（识别会降级为模拟结果）" % "、".join(missing))
    size_mb = settings.dish_onnx_path.stat().st_size / 1024 / 1024
    return CheckResult("模型与类别清单", OK, "%s（%.0f MB）与类别清单齐备" % (settings.dish_onnx_filename, size_mb))


def check_seed_data(settings: Settings) -> CheckResult:
    import json

    path = settings.data_dir / "seed_recipes.json"
    if not path.is_file():
        return CheckResult("种子数据", FAIL, "缺 %s" % path.name)
    try:
        recipes = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult("种子数据", FAIL, "读不出来：%s" % exc)
    return CheckResult("种子数据", OK, "%d 道菜谱待灌入" % len(recipes))


def check_database(settings: Settings) -> CheckResult:
    """库文件所在目录必须可写：首次启动要建表灌数据。"""

    if settings.database_url.startswith("sqlite"):
        db_file = Path(settings.database_url.split("///", 1)[-1])
        parent = db_file.parent
        if not os.access(str(parent), os.W_OK):
            return CheckResult("数据库", FAIL, "目录不可写：%s" % parent)
        state = "已存在" if db_file.is_file() else "首次启动会新建"
        return CheckResult("数据库", OK, "%s（%s）" % (db_file.name, state))
    return CheckResult("数据库", OK, settings.database_url.split("://", 1)[0])


def check_generator_layer(settings: Settings) -> CheckResult:
    info = describe(settings)
    if not info["configured"]:
        return CheckResult(
            "大模型 API",
            OK,
            "服务端不预置 Key（按设计）：生成只认调用方带来的凭据，库内菜谱照常，库外菜会提示填 Key",
        )
    return CheckResult(
        "大模型 API",
        OK,
        "本地工具凭据 %s @ %s（不参与线上生成）" % (info["model"], info["base_url"]),
    )


def _default_session_probe(settings: Settings):
    """用真实权重建一次会话，拿回它会话档案。"""

    from app.services import onnx_providers

    _, status = onnx_providers.build_session(
        settings.dish_onnx_path, settings.recognize_providers
    )
    return status


def check_inference_hardware(
    settings: Settings, session_probe: Optional[Callable[[Settings], object]] = None
) -> CheckResult:
    """识别实际跑在哪个执行提供程序上。

    不建会话看不出真相：装了 GPU 版 onnxruntime、配置也写成了 cuda，只要缺一个 nvidia
    运行库，会话就会静默回落到 CPU，不抛异常也不影响结果，只有耗时能看出来
    （见 `docs/项目设计方案.md` 7.3.2）。所以这里用真实权重建一次会话再断言。
    """

    if not settings.dish_onnx_path.is_file():
        return CheckResult("推理硬件", WARN, "权重缺失，识别会降级为模拟结果，无法确认执行提供程序")

    probe = session_probe or _default_session_probe
    try:
        status = probe(settings)
    except Exception as exc:  # 建不起来也算自检信息，别把整个自检拖崩
        return CheckResult("推理硬件", WARN, "会话建不起来：%s" % exc)

    if status.accelerated:
        return CheckResult(
            "推理硬件",
            OK,
            "%s（配置 %s）" % ("、".join(status.granted), status.preference),
        )
    if status.downgraded:
        from app.core.cuda_dlls import nvidia_cuda_dirs

        hint = (
            "缺 nvidia 运行库（装：.venv\\Scripts\\python.exe -m pip install -r backend/requirements-gpu.txt）"
            if not nvidia_cuda_dirs()
            else "运行库已就位却仍被拒绝，去看 onnxruntime 打出的加载错误"
        )
        return CheckResult(
            "推理硬件",
            WARN,
            "配置要了 %s，会话只拿到 CPU：%s" % ("、".join(status.requested), hint),
        )
    return CheckResult("推理硬件", OK, "CPU（配置 %s）" % status.preference)


def check_frontend(repo_root: Path) -> CheckResult:
    frontend = repo_root / "frontend"
    if not (frontend / "package.json").is_file():
        return CheckResult("前端", FAIL, "找不到 frontend/package.json")
    if not (frontend / "node_modules").is_dir():
        return CheckResult("前端", FAIL, "没装依赖（先 cd frontend 跑 npm install）")
    return CheckResult("前端", OK, "依赖已安装")


def check_ports(probe: Callable[[int], bool] = port_in_use) -> CheckResult:
    busy = [port for port in (BACKEND_PORT, FRONTEND_PORT) if probe(port)]
    if busy:
        return CheckResult(
            "端口",
            WARN,
            "%s 已被占用：可能是上次的服务没关，先关掉或确认它就是本项目"
            % "、".join(str(port) for port in busy),
        )
    return CheckResult("端口", OK, "%d 与 %d 都空闲" % (BACKEND_PORT, FRONTEND_PORT))


def run_all(
    settings: Optional[Settings] = None,
    repo_root: Path = REPO_ROOT,
    probe: Callable[[int], bool] = port_in_use,
    importer: Callable[[str], object] = importlib.import_module,
) -> List[CheckResult]:
    settings = settings or get_settings()
    return [
        check_python(),
        check_backend_deps(importer),
        check_model_files(settings),
        check_inference_hardware(settings),
        check_seed_data(settings),
        check_database(settings),
        check_generator_layer(settings),
        check_frontend(repo_root),
        check_ports(probe),
    ]


def render(results: Sequence[CheckResult]) -> str:
    width = max(len(row.name) for row in results)
    lines = ["演示前自检", "=" * 46]
    for row in results:
        lines.append("  [%s] %-*s  %s" % (row.status, width, row.name, row.detail))
    failures = [row for row in results if row.status == FAIL]
    warnings = [row for row in results if row.status == WARN]
    lines.append("")
    if failures:
        lines.append("有 %d 项必须先修，否则起不来。" % len(failures))
    elif warnings:
        lines.append("可以启动；上面 %d 项建议处理。" % len(warnings))
    else:
        lines.append("全部通过，可以启动。")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="演示前环境自检")
    parser.add_argument("--quiet", action="store_true", help="只输出结论行")
    args = parser.parse_args(argv)

    results = run_all()
    if args.quiet:
        for row in results:
            print("%s %s" % (row.status, row.name))
    else:
        print(render(results))
    return 1 if any(row.status == FAIL for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
