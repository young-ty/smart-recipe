"""并发压测工具（backend/tools/concurrency_probe.py）的测试。

压测数字要贴进报告，所以这里锁三件事：档位解析、吞吐折算、「事件循环被堵住」的判定，
外加一条端到端冒烟——真起一个 uvicorn、真发并发请求（模型目录指向空目录走降级识别，
既能跑得快，也不必加载 335 MB 权重）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import tools.concurrency_probe as probe  # noqa: E402

DEV_DATABASE = BACKEND_DIR / "recipe.db"


# --------------------------- 档位解析 ---------------------------

def test_parse_levels_sorts_and_dedupes():
    assert probe.parse_levels("4, 2,4,1") == [1, 2, 4]


def test_parse_levels_rejects_bad_input():
    for raw in ("", "  ", "0", "-2", "a", "1,x"):
        with pytest.raises(ValueError):
            probe.parse_levels(raw)


# --------------------------- 吞吐与判定 ---------------------------

def test_summarize_level_converts_wall_time_to_throughput():
    row = probe.summarize_level(4, wall_ms=1000.0, latencies=[250.0] * 4, health_ms=[3.0] * 5)
    assert row["requests"] == 4
    assert row["throughput_rps"] == pytest.approx(4.0)
    assert row["p50_ms"] == 250.0
    assert row["health_ok"] is True


def test_summarize_level_flags_a_stalled_event_loop():
    """健康检查被拖到与推理同量级，就是事件循环被占了，必须判失败。"""

    stalled = probe.summarize_level(1, wall_ms=600.0, latencies=[600.0], health_ms=[613.0])
    assert stalled["health_ok"] is False

    healthy = probe.summarize_level(1, wall_ms=150.0, latencies=[150.0], health_ms=[4.0, 5.0])
    assert healthy["health_ok"] is True
    assert healthy["health_max_ms"] == 5.0


def test_summarize_level_without_health_samples_is_not_ok():
    """一个健康样本都没采到不能算通过——采样任务是死是活要看得出来。"""

    row = probe.summarize_level(1, wall_ms=100.0, latencies=[100.0], health_ms=[])
    assert row["health_ok"] is False
    assert row["health_samples"] == 0


def test_format_table_shows_every_level_and_the_verdict():
    report = {
        "levels": [
            probe.summarize_level(1, 100.0, [100.0], [4.0]),
            probe.summarize_level(8, 800.0, [800.0] * 8, [613.0]),
        ]
    }
    text = probe.format_table(report)
    assert "并发" in text
    assert text.count("\n") == 3  # 表头两行 + 两档数据
    assert "被堵住" in text and "正常" in text


# --------------------------- 端到端 ---------------------------

def test_main_runs_a_real_burst_against_a_temp_server(tmp_path, capsys):
    before = DEV_DATABASE.read_bytes() if DEV_DATABASE.is_file() else None

    code = probe.main(
        [
            "--levels",
            "2",
            "--rounds",
            "1",
            "--models-dir",
            str(tmp_path / "models-not-delivered"),  # 空目录 → 降级识别，跑得快
            "--json",
        ]
    )

    assert code == 0, "降级识别下事件循环不该被堵住"
    printed = capsys.readouterr().out
    report = json.loads(printed[printed.index("{") :])
    assert report["levels"][0]["concurrency"] == 2
    assert report["levels"][0]["health_ok"] is True

    after = DEV_DATABASE.read_bytes() if DEV_DATABASE.is_file() else None
    assert before == after, "压测默认必须用临时库，不能写开发库"
