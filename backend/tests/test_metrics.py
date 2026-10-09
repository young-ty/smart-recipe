"""指标原语与暴露格式测试（设计方案 5.5）。

覆盖三类指标的累计语义、带标签的样本行、直方图分桶的累加方式与转义规则，
以及「多线程同时写不丢计数」——识别跑在线程池里，这条是真实约束而不是凑数。
"""

from __future__ import annotations

import threading

import pytest

from app.core.metrics import REGISTRY, Counter, Gauge, Histogram, Registry, format_value


# --------------------------------------------------------------- 基本语义

def test_counter_accumulates_per_label_set():
    counter = Counter("demo_counter_total", "演示用计数", ("kind",))
    counter.inc(kind="a")
    counter.inc(2.5, kind="a")
    counter.inc(kind="b")
    assert counter.value(kind="a") == 3.5
    assert counter.value(kind="b") == 1.0
    assert counter.value(kind="never") == 0.0


def test_gauge_moves_in_both_directions():
    gauge = Gauge("demo_gauge", "演示用瞬时值")
    gauge.inc()
    gauge.inc(2)
    gauge.dec()
    assert gauge.value() == 2.0
    gauge.set(0)
    assert gauge.value() == 0.0


def test_counter_render_follows_prometheus_text_format():
    registry = Registry()
    counter = registry.register(Counter("demo_total", "演示", ("kind",)))
    counter.inc(kind="a")
    counter.inc(kind="b")
    assert registry.render() == (
        "# HELP demo_total 演示\n"
        "# TYPE demo_total counter\n"
        'demo_total{kind="a"} 1\n'
        'demo_total{kind="b"} 1\n'
    )


def test_metric_without_labels_renders_bare_name():
    registry = Registry()
    registry.register(Gauge("demo_gauge", "演示")).set(3)
    assert "demo_gauge 3\n" in registry.render()


# --------------------------------------------------------------- 直方图

def test_histogram_buckets_are_cumulative_and_keep_sum_and_count():
    histogram = Histogram("demo_seconds", "演示", buckets=(0.1, 1.0))
    for value in (0.05, 0.5, 2.0):
        histogram.observe(value)
    lines = list(histogram.lines())
    assert lines[0] == 'demo_seconds_bucket{le="0.1"} 1'
    assert lines[1] == 'demo_seconds_bucket{le="1"} 2'
    assert lines[2] == 'demo_seconds_bucket{le="+Inf"} 3'
    assert lines[3].startswith("demo_seconds_sum ")
    assert abs(float(lines[3].split()[1]) - 2.55) < 1e-9
    assert lines[4] == "demo_seconds_count 3"
    assert histogram.count() == 3


def test_histogram_puts_le_after_its_own_labels():
    """le 是桶自己的标签，必须排在业务标签之后，顺序错了采集端会当成另一条序列。"""

    histogram = Histogram("demo_seconds", "演示", buckets=(1.0,), labelnames=("path",))
    histogram.observe(0.5, path="/api/recognize")
    assert list(histogram.lines())[0] == (
        'demo_seconds_bucket{path="/api/recognize",le="1"} 1'
    )


def test_histogram_requires_at_least_one_bucket():
    with pytest.raises(ValueError):
        Histogram("demo_seconds", "演示", buckets=())


def test_histogram_without_observation_renders_nothing():
    histogram = Histogram("demo_seconds", "演示", buckets=(1.0,))
    assert list(histogram.lines()) == []


# --------------------------------------------------------------- 校验与转义

def test_invalid_names_and_labels_are_rejected():
    with pytest.raises(ValueError):
        Counter("2bad", "演示")
    with pytest.raises(ValueError):
        Counter("ok_total", "演示", ("bad-label",))
    with pytest.raises(ValueError):
        Counter("ok_total", "演示", ("x", "x"))


def test_unknown_and_missing_labels_are_rejected():
    counter = Counter("demo_total", "演示", ("kind",))
    with pytest.raises(ValueError):
        counter.inc(other="a")
    with pytest.raises(ValueError):
        counter.inc()


def test_label_values_are_escaped():
    counter = Counter("demo_total", "演示", ("label",))
    counter.inc(label='a"b\\c\nd')
    assert list(counter.lines()) == ['demo_total{label="a\\"b\\\\c\\nd"} 1']


def test_help_text_keeps_quotes_but_escapes_newlines():
    registry = Registry()
    registry.register(Gauge("demo_gauge", '带"引号"的说明'))
    assert '# HELP demo_gauge 带"引号"的说明' in registry.render()


def test_value_formatting_follows_prometheus_conventions():
    assert format_value(3.0) == "3"
    assert format_value(0.5) == "0.5"
    assert format_value(float("inf")) == "+Inf"
    assert format_value(float("-inf")) == "-Inf"
    assert format_value(float("nan")) == "NaN"


# --------------------------------------------------------------- 注册表与并发

def test_registry_reset_clears_values_but_keeps_metrics():
    registry = Registry()
    counter = registry.register(Counter("demo_total", "演示"))
    counter.inc()
    registry.reset()
    assert counter.value() == 0.0
    assert registry.get("demo_total") is counter
    counter.inc()
    assert "demo_total 1\n" in registry.render()


def test_registry_register_is_idempotent_and_find_returns_none_for_unknown():
    registry = Registry()
    counter = Counter("demo_total", "演示")
    registry.register(counter)
    registry.register(counter)
    assert len(registry.metrics()) == 1
    assert registry.find("nope") is None
    with pytest.raises(KeyError):
        registry.get("nope")


def test_counter_counts_are_exact_under_concurrent_writes():
    counter = Counter("demo_total", "演示")
    rounds = 200
    workers = 8

    def hammer() -> None:
        for _ in range(rounds):
            counter.inc()

    threads = [threading.Thread(target=hammer) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert counter.value() == rounds * workers


def test_default_registry_exposes_the_documented_metric_surface():
    """指标名与类型是采集端的契约，改动等于让看板重新配一遍。"""

    text = REGISTRY.render()
    for name, kind in (
        ("http_requests_total", "counter"),
        ("http_request_duration_seconds", "histogram"),
        ("recognize_requests_total", "counter"),
        ("recognize_inference_seconds", "histogram"),
        ("recognize_queue_seconds", "histogram"),
        ("recognize_in_flight", "gauge"),
        ("recipe_resolve_total", "counter"),
        ("recipe_cache_total", "counter"),
        ("generator_requests_total", "counter"),
        ("generator_request_seconds", "histogram"),
        ("generator_tokens_total", "counter"),
    ):
        assert f"# TYPE {name} {kind}" in text, name
