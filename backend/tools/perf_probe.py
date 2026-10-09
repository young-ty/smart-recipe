r"""NFR-01 本地压测：把识别与菜谱检索的 P95 量出来。

验收标准（设计方案 10.3）写的是「本地压测 100 次识别请求，P95 <= 500 ms」，另外
NFR-01 还要求菜谱检索 P95 <= 200 ms。之前只有一张图的单次实测（143 ms），
这里补上可复现的压测脚本：

  .venv\\Scripts\\python.exe backend/tools/perf_probe.py

做法是走 FastAPI 的 TestClient（同一进程内直接调应用，不经过网络与 uvicorn，
量到的是接口自身的耗时），循环打 demo/dishes 下的实拍图，默认 100 次。
每次请求都会真实写一条 recognition_logs，因此默认用临时数据库，不污染开发库。

两个要注意的口径：
- 识别器按配置缓存，ONNX 只在第一次请求时加载，所以先热身再计时，否则样本里
  混进一次模型加载的几百毫秒。
- 生成层显式置空 Key：库外菜走大模型会把网络时延混进来，而 NFR-01 管的是本地
  推理与检索，生成接口另有 8 s 的独立预算（设计方案 7.5）。
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Sequence

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:  # 支持直接 python backend/tools/perf_probe.py
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

DEMO_DISHES_DIR = REPO_ROOT / "demo" / "dishes"
# 压测用的库内菜：红烧肉在种子库里（#109），命中检索而不是走生成分支
DEFAULT_LIBRARY_DISH = "红烧肉"

RECOGNIZE_BUDGET_MS = 500.0
RESOLVE_BUDGET_MS = 200.0

WARMUP_ROUNDS = 2
_MIME_BY_SUFFIX = {".png": "image/png", ".webp": "image/webp"}


def percentile(values: Sequence[float], pct: float) -> float:
    """线性插值分位数：100 个样本时 P95 不取 max，比「排序后看第 95 个」稳。"""

    ordered = sorted(values)
    if not ordered:
        return float("nan")
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(samples: Sequence[float], budget_ms: float) -> Dict[str, object]:
    return {
        "count": len(samples),
        "mean": round(statistics.fmean(samples), 1),
        "p50": round(percentile(samples, 50), 1),
        "p90": round(percentile(samples, 90), 1),
        "p95": round(percentile(samples, 95), 1),
        "max": round(max(samples), 1),
        "budget_ms": budget_ms,
        "passed": percentile(samples, 95) <= budget_ms,
    }


def load_images(images_dir: Path, limit: int = 4) -> List[Path]:
    """取前 limit 张实拍图轮换使用，避免 100 次压测全打同一张图。"""

    if images_dir.is_file():
        return [images_dir]
    if not images_dir.is_dir():
        raise SystemExit("压测图片目录不存在：%s" % images_dir)
    candidates = sorted(
        path
        for path in images_dir.iterdir()
        if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    )
    if not candidates:
        raise SystemExit("压测图片目录里没有图片：%s" % images_dir)
    return candidates[:limit]


def measure(call: Callable[[], object], times: int) -> List[float]:
    """串行调用 times 次，返回每次端到端耗时（毫秒）。非 200 立即中断。"""

    samples: List[float] = []
    for _ in range(times):
        started = time.perf_counter()
        response = call()
        samples.append((time.perf_counter() - started) * 1000.0)
        status = getattr(response, "status_code", 200)
        if status != 200:
            raise SystemExit(
                "压测请求返回 %s：%s" % (status, getattr(response, "text", "")[:200])
            )
    return samples


def run_probe(
    requests: int = 100,
    resolve_requests: int = 100,
    images_dir: Path = DEMO_DISHES_DIR,
    models_dir: Path = REPO_ROOT / "models",
    dish: str = DEFAULT_LIBRARY_DISH,
    keep_database: bool = False,
) -> dict:
    """跑两组压测并返回结果；识别模式、图片来源一并带回来，便于核对口径。"""

    images = load_images(images_dir)
    payloads = [path.read_bytes() for path in images]

    tmp_dir = Path(tempfile.mkdtemp(prefix="perf-probe-"))
    try:
        settings = Settings(
            database_url="sqlite+pysqlite:///" + (tmp_dir / "perf.db").as_posix(),
            models_dir=Path(models_dir),
            generator_api_key="",
        )
        app = create_app(settings)
        with TestClient(app) as client:
            health = client.get("/api/health").json()

            pick = itertools.count()

            def recognize():
                index = next(pick)
                image = images[index % len(images)]
                payload = payloads[index % len(payloads)]
                mime = _MIME_BY_SUFFIX.get(image.suffix.lower(), "image/jpeg")
                return client.post(
                    "/api/recognize",
                    files={"file": ("perf" + image.suffix.lower(), payload, mime)},
                )

            def resolve():
                return client.post("/api/recipes/resolve", json={"dish": dish})

            for _ in range(WARMUP_ROUNDS):
                recognize()
                resolve()

            first_recognize = recognize().json()
            resolve_body = resolve().json()

            recognize_samples = measure(recognize, requests)
            resolve_samples = measure(resolve, resolve_requests)

            report = {
                "images": [path.name for path in images],
                "model": health.get("model", {}),
                "generator_configured": health.get("generator", {}).get("configured"),
                "recognize": dict(
                    summarize(recognize_samples, RECOGNIZE_BUDGET_MS),
                    degraded=first_recognize.get("degraded"),
                    candidates=len(first_recognize.get("candidates") or []),
                ),
                "resolve": dict(
                    summarize(resolve_samples, RESOLVE_BUDGET_MS),
                    dish=dish,
                    source=resolve_body.get("source"),
                ),
            }
    finally:
        if not keep_database:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            print("临时数据库保留在：%s" % tmp_dir)

    return report


def render(report: dict) -> str:
    model = report["model"]
    mode = "ONNX 真实模型" if not model.get("degraded") else "降级（模拟识别）"
    lines = [
        "识别与检索压测（NFR-01）",
        "=" * 46,
        "模型：%s  %s 类  名称清单=%s  模式=%s" % (
            model.get("version"),
            model.get("class_count"),
            "已交付" if model.get("labels_available") else "缺失",
            mode,
        ),
        "图片：%s" % "、".join(report["images"]),
        "",
        "POST /api/recognize",
        _line(report["recognize"]),
        "",
        "POST /api/recipes/resolve（%s，来源 %s）" % (
            report["resolve"]["dish"], report["resolve"]["source"]
        ),
        _line(report["resolve"]),
    ]
    return "\n".join(lines)


def _line(stats: dict) -> str:
    return "  %d 次  mean %s ms  p50 %s  p90 %s  p95 %s  max %s  → 预算 %s ms：%s" % (
        stats["count"],
        stats["mean"],
        stats["p50"],
        stats["p90"],
        stats["p95"],
        stats["max"],
        stats["budget_ms"],
        "通过" if stats["passed"] else "超标",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NFR-01 识别与检索压测（只读，默认用临时库）")
    parser.add_argument("--requests", type=int, default=100, help="识别请求次数")
    parser.add_argument("--resolve-requests", type=int, default=100, help="检索请求次数")
    parser.add_argument("--images", type=Path, default=DEMO_DISHES_DIR, help="图片或图片目录")
    parser.add_argument("--models-dir", type=Path, default=REPO_ROOT / "models")
    parser.add_argument("--dish", default=DEFAULT_LIBRARY_DISH, help="检索压测用的库内菜名")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--json-out", type=Path, default=None, help="把 JSON 结果写到文件")
    parser.add_argument("--strict", action="store_true", help="P95 超预算时返回非零退出码")
    parser.add_argument("--keep-database", action="store_true", help="保留临时数据库便于排查")
    args = parser.parse_args(argv)

    report = run_probe(
        requests=args.requests,
        resolve_requests=args.resolve_requests,
        images_dir=args.images,
        models_dir=args.models_dir,
        dish=args.dish,
        keep_database=args.keep_database,
    )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))

    failed = [key for key in ("recognize", "resolve") if not report[key]["passed"]]
    if failed:
        print("\n未达 NFR-01 预算：" + "、".join(failed))
        return 1 if args.strict else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
