"""并发压测：看识别接口在并发下的吞吐与尾延迟，并验证「推理不占住事件循环」。

`perf_probe.py` 量的是单请求延迟（NFR-01 的 P95 ≤ 500 ms），这里量的是另一件事：
并发上来以后吞吐有没有涨、其它接口会不会被推理拖住。默认在临时数据库上起一个
进程内的 uvicorn（真实 HTTP + 真实事件循环，不碰开发库），也可以打已在跑的服务：

  .venv\\Scripts\\python.exe backend/tools/concurrency_probe.py
  .venv\\Scripts\\python.exe backend/tools/concurrency_probe.py --base-url http://127.0.0.1:8000
  .venv\\Scripts\\python.exe backend/tools/concurrency_probe.py --levels 1,2,4,8,16 --rounds 4

判定看两件事：
- 吞吐应随并发上升并在某个点到顶（本机 16 逻辑核约 10 req/s，含 HTTP 与写库开销），
  到顶后继续加并发只会拉长排队，这是容量上限，不是缺陷；
- 突发期间 `/api/health` 的延迟应与一次推理明显不在一个量级。它一旦被拉到几百毫秒，
  就说明推理又回到事件循环里跑了（改之前实测 613 ms，与一次推理同长）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:  # 支持直接 python backend/tools/concurrency_probe.py
    sys.path.insert(0, str(BACKEND_DIR))

from tools.perf_probe import percentile  # noqa: E402

DEMO_DISHES_DIR = REPO_ROOT / "demo" / "dishes"
DEFAULT_LEVELS = (1, 2, 4, 8)
DEFAULT_ROUNDS = 2
HEALTH_SAMPLE_SECONDS = 0.05
# 健康检查必须明显快于一次推理（单张约 140 ms）：改之前它会被拉到 600 ms 量级，
# 改之后即使在并发 8、CPU 打满时也只有几十毫秒。阈值取中间，机器抖动也不会误判。
HEALTH_BUDGET_MS = 250.0


def parse_levels(raw: str) -> List[int]:
    """解析 --levels：逗号分隔的正整数，去重升序，非法值直接报错。"""

    levels = []
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        value = int(chunk)
        if value < 1:
            raise ValueError("并发度必须是正整数：%r" % raw)
        levels.append(value)
    if not levels:
        raise ValueError("至少要有一个并发度：%r" % raw)
    return sorted(set(levels))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def probe_settings(models_dir=None):
    """压测用的临时配置：临时数据库、空 Key，可选指向别的模型目录。"""

    from app.core.config import Settings

    tmp = Path(tempfile.mkdtemp(prefix="concurrency-probe-"))
    overrides = {"models_dir": models_dir} if models_dir is not None else {}
    return Settings(
        database_url="sqlite+pysqlite:///%s" % (tmp / "probe.db").as_posix(),
        generator_api_key="",  # 压测只打识别，别把大模型时延混进来
        **overrides,
    )


def summarize_level(level: int, wall_ms: float, latencies: Sequence[float], health_ms: Sequence[float]) -> Dict[str, object]:
    """一个并发档位的结果。吞吐按墙钟时间折算，才算得出「并发到底有没有用」。"""

    total = len(latencies)
    throughput = (total / wall_ms * 1000) if wall_ms > 0 else float("nan")
    return {
        "concurrency": level,
        "requests": total,
        "wall_ms": round(wall_ms, 1),
        "throughput_rps": round(throughput, 2),
        "p50_ms": round(percentile(latencies, 50), 1),
        "p95_ms": round(percentile(latencies, 95), 1),
        "max_ms": round(max(latencies), 1) if latencies else float("nan"),
        "health_samples": len(health_ms),
        "health_p95_ms": round(percentile(health_ms, 95), 1) if health_ms else float("nan"),
        "health_max_ms": round(max(health_ms), 1) if health_ms else float("nan"),
        "health_ok": bool(health_ms) and max(health_ms) <= HEALTH_BUDGET_MS,
    }


def format_table(report: Dict[str, object]) -> str:
    lines = [
        "并发  请求数  吞吐(req/s)    p50      p95      max    健康检查 p95/max   事件循环",
        "----  ------  -----------  -------  -------  -------  --------------  --------",
    ]
    for row in report["levels"]:
        lines.append(
            "%4d  %6d  %11.2f  %6.1fms %7.1fms %7.1fms  %6.1f/%6.1fms  %s"
            % (
                row["concurrency"],
                row["requests"],
                row["throughput_rps"],
                row["p50_ms"],
                row["p95_ms"],
                row["max_ms"],
                row["health_p95_ms"],
                row["health_max_ms"],
                "正常" if row["health_ok"] else "被堵住",
            )
        )
    return "\n".join(lines)


async def _post_recognize(client, url: str, image) -> float:
    name, payload = image
    started = time.perf_counter()
    response = await client.post(
        url, files={"file": (name, payload, "image/jpeg")}, timeout=120
    )
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return elapsed


async def _sample_health(client, base_url: str, stop: asyncio.Event, samples: List[float]) -> None:
    """突发期间持续打健康检查：它是纯内存 + 一次 count，慢下来只能是事件循环被占住。"""

    while not stop.is_set():
        started = time.perf_counter()
        try:
            response = await client.get("%s/api/health" % base_url, timeout=30)
            if response.status_code == 200:
                samples.append((time.perf_counter() - started) * 1000)
        except Exception:  # 压测期间的偶发失败不该中断整轮
            pass
        await asyncio.sleep(HEALTH_SAMPLE_SECONDS)


async def _sweep(base_url: str, images, levels: Sequence[int], rounds: int) -> List[Dict[str, object]]:
    import httpx

    recognize_url = "%s/api/recognize" % base_url
    results: List[Dict[str, object]] = []
    async with httpx.AsyncClient() as client:
        await _post_recognize(client, recognize_url, images[0])  # 预热：别把加载权重算进任何一档
        for level in levels:
            stop = asyncio.Event()
            health: List[float] = []
            sampler = asyncio.create_task(_sample_health(client, base_url, stop, health))
            started = time.perf_counter()
            latencies: List[float] = []
            for _ in range(rounds):
                latencies.extend(
                    await asyncio.gather(
                        *[
                            _post_recognize(client, recognize_url, images[index % len(images)])
                            for index in range(level)
                        ]
                    )
                )
            wall_ms = (time.perf_counter() - started) * 1000
            stop.set()
            await sampler
            results.append(summarize_level(level, wall_ms, latencies, health))
    return results


def load_images(limit: int = 4):
    paths = sorted(DEMO_DISHES_DIR.glob("*.jpg"))[:limit]
    if not paths:
        raise SystemExit("演示图缺失：%s" % DEMO_DISHES_DIR)
    return [(path.name, path.read_bytes()) for path in paths]


def start_server(settings=None):
    """起一个进程内的真实 uvicorn，返回 (server, thread, base_url)。

    不传 settings 时用临时数据库与空 Key：压测只打识别，既不碰开发库，也不把大模型
    的网络时延混进来。测试可以传自己的 settings（例如指向空模型目录以走降级路径）。
    """

    import uvicorn

    from app.main import create_app

    if settings is None:
        settings = probe_settings()
    app = create_app(settings)
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="probe-uvicorn", daemon=True)
    thread.start()
    deadline = time.time() + 60
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise SystemExit("后端没能在 60 秒内起来")
    return server, thread, "http://127.0.0.1:%d" % port


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="识别接口并发压测")
    parser.add_argument("--base-url", default=None, help="已在跑的服务地址；不给就自起一个")
    parser.add_argument("--levels", default=",".join(str(item) for item in DEFAULT_LEVELS))
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="每个并发档位跑几轮")
    parser.add_argument(
        "--models-dir",
        default=None,
        help="指定模型目录；指向空目录即走降级识别，测试用",
    )
    parser.add_argument("--json", action="store_true", help="额外打印 JSON，便于贴进报告")
    args = parser.parse_args(argv)

    levels = parse_levels(args.levels)
    images = load_images()
    server = thread = None
    if args.base_url:
        base_url = args.base_url.rstrip("/")
    else:
        models_dir = Path(args.models_dir) if args.models_dir else None
        server, thread, base_url = start_server(probe_settings(models_dir))
    try:
        results = asyncio.run(_sweep(base_url, images, levels, max(1, args.rounds)))
    finally:
        if server is not None:
            server.should_exit = True
            thread.join(timeout=15)

    report = {"base_url": base_url, "health_budget_ms": HEALTH_BUDGET_MS, "levels": results}
    print("识别接口并发压测：%s" % base_url)
    print(format_table(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(row["health_ok"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
