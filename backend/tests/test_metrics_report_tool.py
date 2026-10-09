"""指标报告工具（backend/tools/metrics_report.py）的测试。

指标文本是采集端的契约，所以这里锁住解析、分位数估算和「估不出来就说估不出来」
三种行为，再补一条走真实 /metrics 的端到端冒烟。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import tools.metrics_report as report  # noqa: E402

SAMPLE = """# HELP demo_total 演示
# TYPE demo_total counter
demo_total{kind="a"} 3
demo_total{kind="b"} 1.5
# TYPE demo_gauge gauge
demo_gauge 2
# TYPE demo_seconds histogram
demo_seconds_bucket{le="0.1"} 1
demo_seconds_bucket{le="0.2"} 3
demo_seconds_bucket{le="+Inf"} 4
demo_seconds_sum 0.45
demo_seconds_count 4
"""


# --------------------------------------------------------------- 解析

def test_parse_line_reads_name_labels_and_value():
    assert report.parse_line('demo_total{kind="a",other="b c"} 3') == (
        "demo_total",
        {"kind": "a", "other": "b c"},
        3.0,
    )


def test_parse_line_skips_comments_and_broken_lines():
    for line in ("", "   ", "# HELP demo 说明", "demo_total", 'demo_total{kind="a"'):
        assert report.parse_line(line) is None


def test_parse_line_rejects_non_numeric_value():
    assert report.parse_line('demo_total{kind="a"} abc') is None


def test_label_values_are_unescaped():
    parsed = report.parse_line(r'demo_total{path="/api/a\"b"} 1')
    assert parsed is not None
    assert parsed[1]["path"] == '/api/a"b'


def test_parse_exposition_collects_types_and_samples():
    types, samples = report.parse_exposition(SAMPLE)
    assert types["demo_total"] == "counter"
    assert types["demo_seconds"] == "histogram"
    assert len(samples) == 8


# --------------------------------------------------------------- 分位数

def test_histogram_quantile_interpolates_inside_a_bucket():
    buckets = [(0.1, 1.0), (0.2, 3.0), (math.inf, 4.0)]
    # 中位数落在 0.1~0.2 桶里：rank=2，桶内已累计 1，桶里有 2 个样本
    assert report.histogram_quantile(buckets, 0.5) == 0.1 + (0.1 * (2 - 1) / 2)


def test_histogram_quantile_returns_none_when_it_falls_into_the_infinite_bucket():
    buckets = [(0.1, 1.0), (math.inf, 4.0)]
    assert report.histogram_quantile(buckets, 0.95) is None


def test_histogram_quantile_without_samples_is_none():
    assert report.histogram_quantile([], 0.5) is None
    assert report.histogram_quantile([(0.1, 0.0), (math.inf, 0.0)], 0.5) is None


# --------------------------------------------------------------- 汇总与呈现

def test_summarize_splits_counters_gauges_and_histograms():
    types, samples = report.parse_exposition(SAMPLE)
    data = report.summarize(types, samples)
    assert data["counters"]["demo_total{kind=a}"] == 3.0
    assert data["gauges"]["demo_gauge"] == 2.0
    entry = data["histograms"]["demo_seconds"]
    assert entry["count"] == 4.0
    assert entry["sum"] == 0.45
    assert entry["buckets"] == [(0.1, 1.0), (0.2, 3.0), (math.inf, 4.0)]


def test_summarize_groups_histograms_by_all_labels_except_le():
    text = (
        '# TYPE demo_seconds histogram\n'
        'demo_seconds_bucket{path="/a",le="1"} 1\n'
        'demo_seconds_bucket{path="/b",le="1"} 2\n'
    )
    types, samples = report.parse_exposition(text)
    data = report.summarize(types, samples)
    assert set(data["histograms"]) == {"demo_seconds{path=/a}", "demo_seconds{path=/b}"}


def test_format_report_renders_counts_and_percentiles():
    types, samples = report.parse_exposition(SAMPLE)
    text = report.format_report(report.summarize(types, samples), "demo.txt")
    assert "计数型" in text
    assert "demo_total{kind=a}" in text
    assert "瞬时值" in text
    assert "demo_seconds" in text
    assert "n=4" in text


def test_format_report_says_so_when_a_percentile_cannot_be_estimated():
    text = (
        "# TYPE demo_seconds histogram\n"
        'demo_seconds_bucket{le="0.1"} 1\n'
        'demo_seconds_bucket{le="+Inf"} 10\n'
        'demo_seconds_count 10\n'
    )
    types, samples = report.parse_exposition(text)
    rendered = report.format_report(report.summarize(types, samples), "demo.txt")
    assert "估不出来" in rendered


def test_format_report_handles_empty_input():
    rendered = report.format_report(report.summarize({}, []), "demo.txt")
    assert rendered.count("（还没有样本）") == 3


# --------------------------------------------------------------- 端到端

def test_report_against_the_live_metrics_endpoint(client):
    """走一遍真实暴露文本：解析、汇总、渲染都要能跑通。"""

    client.get("/api/health")  # 先产生一个样本，空注册表渲染出来只有 HELP / TYPE
    body = client.get("/metrics").text
    data = report.build_report(body, "in-process")
    assert data["counters"]
    assert data["histograms"]
    rendered = report.format_report(data, "in-process")
    assert "计数型" in rendered
