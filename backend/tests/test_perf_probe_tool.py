"""NFR-01 压测工具（backend/tools/perf_probe.py）的测试。

压测数字会直接写进测试报告与验收结论，所以这里锁三件事：
分位数算得对、默认不动开发库、超预算时能如实报告。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import tools.perf_probe as probe  # noqa: E402

DESIGN_DOC = REPO_ROOT / "docs" / "项目设计方案.md"
DEV_DATABASE = BACKEND_DIR / "recipe.db"


# --------------------------- 分位数 ---------------------------

def test_percentile_interpolates_between_neighbours():
    values = list(range(1, 101))  # 1..100
    assert probe.percentile(values, 50) == pytest.approx(50.5)
    assert probe.percentile(values, 95) == pytest.approx(95.05)


def test_percentile_handles_degenerate_inputs():
    assert probe.percentile([7.0], 95) == 7.0
    assert math.isnan(probe.percentile([], 95))


def test_percentile_boundaries_are_min_and_max():
    values = [3.0, 1.0, 2.0]
    assert probe.percentile(values, 0) == 1.0
    assert probe.percentile(values, 100) == 3.0


# --------------------------- 汇总与预算 ---------------------------

def test_summarize_reports_expected_quantiles():
    stats = probe.summarize(list(range(1, 101)), budget_ms=500.0)
    assert stats["count"] == 100
    assert stats["p95"] == pytest.approx(95.05, abs=0.1)
    assert stats["max"] == 100.0
    assert stats["passed"] is True


def test_summarize_flags_budget_violation():
    """P95 与 max 的区别在这里：只有少数超时要判超标，靠的是分位数不是最大值。"""

    samples = [10.0] * 99 + [9000.0]
    stats = probe.summarize(samples, budget_ms=500.0)
    assert stats["max"] == 9000.0
    assert stats["p95"] < 500.0
    assert stats["passed"] is True

    over = probe.summarize([900.0] * 100, budget_ms=500.0)
    assert over["passed"] is False


def test_budgets_match_the_design_document():
    """NFR-01 的预算写死在文档里，工具与文档不能各说各话。"""

    text = DESIGN_DOC.read_text(encoding="utf-8")
    assert "P95 ≤ 500 ms" in text and "P95 ≤ 200 ms" in text
    assert probe.RECOGNIZE_BUDGET_MS == 500.0
    assert probe.RESOLVE_BUDGET_MS == 200.0


# --------------------------- 图片来源 ---------------------------

def test_load_images_only_picks_pictures_and_caps_the_count(tmp_path):
    for name in ("a.jpg", "b.png", "c.webp", "e.jpg"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("not a picture", encoding="utf-8")

    picked = probe.load_images(tmp_path, limit=2)
    assert [path.name for path in picked] == ["a.jpg", "b.png"]


def test_load_images_accepts_a_single_file(tmp_path):
    single = tmp_path / "one.jpg"
    single.write_bytes(b"x")
    assert probe.load_images(single) == [single]


def test_load_images_rejects_empty_directory(tmp_path):
    with pytest.raises(SystemExit):
        probe.load_images(tmp_path)


# --------------------------- 计时 ---------------------------

class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_measure_records_one_sample_per_call():
    samples = probe.measure(lambda: _Response(200), 5)
    assert len(samples) == 5
    assert all(sample >= 0 for sample in samples)


def test_measure_stops_on_non_200():
    with pytest.raises(SystemExit):
        probe.measure(lambda: _Response(413, "too large"), 3)


# --------------------------- 端到端 ---------------------------

def _demo_images_available() -> bool:
    return probe.DEMO_DISHES_DIR.is_dir() and any(probe.DEMO_DISHES_DIR.iterdir())


@pytest.mark.skipif(not _demo_images_available(), reason="演示图未入库")
def test_run_probe_uses_temp_database_and_leaves_dev_database_alone(tmp_path):
    before = DEV_DATABASE.read_bytes() if DEV_DATABASE.is_file() else None

    report = probe.run_probe(
        requests=3,
        resolve_requests=3,
        models_dir=tmp_path / "models-not-delivered",  # 空目录 → 降级，不必加载 351 MB 权重
        dish="红烧肉",
    )

    assert report["recognize"]["count"] == 3
    assert report["resolve"]["count"] == 3
    assert report["recognize"]["p95"] <= probe.RECOGNIZE_BUDGET_MS
    assert report["resolve"]["p95"] <= probe.RESOLVE_BUDGET_MS
    assert report["resolve"]["source"] == "library", "红烧肉应在种子库里，检索应命中库内"
    assert report["model"]["degraded"] is True, "空模型目录应降级为模拟识别"
    assert report["recognize"]["candidates"] == 5

    after = DEV_DATABASE.read_bytes() if DEV_DATABASE.is_file() else None
    assert before == after, "压测默认必须用临时库，不能写开发库"


@pytest.mark.skipif(not _demo_images_available(), reason="演示图未入库")
def test_run_probe_report_is_json_serialisable(tmp_path):
    report = probe.run_probe(
        requests=2, resolve_requests=2, models_dir=tmp_path / "none"
    )
    json.dumps(report, ensure_ascii=False)  # 不抛异常即可
    assert report["images"], "结果里要带上用了哪些图，便于复现"


def test_render_shows_both_endpoints_and_verdict():
    report = {
        "images": ["a.jpg"],
        "model": {"version": "m", "class_count": 208, "labels_available": True, "degraded": False},
        "generator_configured": False,
        "recognize": probe.summarize([100.0] * 10, probe.RECOGNIZE_BUDGET_MS),
        "resolve": dict(
            probe.summarize([5.0] * 10, probe.RESOLVE_BUDGET_MS),
            dish="红烧肉",
            source="library",
        ),
    }
    text = probe.render(report)
    assert "/api/recognize" in text and "/api/recipes/resolve" in text
    assert "通过" in text and "红烧肉" in text


def test_main_json_output_is_machine_readable(monkeypatch, capsys):
    monkeypatch.setattr(
        probe,
        "run_probe",
        lambda **kwargs: {
            "images": ["a.jpg"],
            "model": {"version": "m", "class_count": 208, "labels_available": True, "degraded": False},
            "generator_configured": False,
            "recognize": probe.summarize([1.0], probe.RECOGNIZE_BUDGET_MS),
            "resolve": dict(
                probe.summarize([1.0], probe.RESOLVE_BUDGET_MS), dish="红烧肉", source="library"
            ),
        },
    )
    assert probe.main(["--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["recognize"]["count"] == 1


def test_main_strict_mode_fails_when_budget_is_blown(monkeypatch):
    monkeypatch.setattr(
        probe,
        "run_probe",
        lambda **kwargs: {
            "images": ["a.jpg"],
            "model": {"version": "m", "class_count": 208, "labels_available": True, "degraded": False},
            "generator_configured": False,
            "recognize": probe.summarize([900.0], probe.RECOGNIZE_BUDGET_MS),
            "resolve": dict(
                probe.summarize([1.0], probe.RESOLVE_BUDGET_MS), dish="红烧肉", source="library"
            ),
        },
    )
    assert probe.main(["--strict"]) == 1, "加了 --strict 就该用退出码把超标报出来"
    assert probe.main([]) == 0, "不加 --strict 只报告，不阻断"
