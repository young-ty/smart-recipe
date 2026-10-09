# -*- coding: utf-8 -*-
"""进程内指标采集与 Prometheus 文本暴露（设计方案 5.5）。

为什么手写而不引 prometheus_client：本项目只用到 Counter / Gauge / Histogram 三类，
需求边界清楚；部署环境已经要装 onnxruntime 这类几百兆的包，指标这层不再多引一个
依赖。暴露格式用的是公开标准（Prometheus text exposition format 0.0.4），
自己拼一遍也能看清每个数字是从哪来的。

两条实现约束：

1. **写操作要加锁**。识别跑在 FastAPI 的线程池里，采集端又可能同时抓 /metrics，
   不加锁会读到「和值对不上桶计数」的中间态。
2. **标签值必须转义**。路径、菜名这类值会进标签，双引号与换行不转义会让整段
   暴露文本变成非法格式，采集端直接拒收。

指标对象在模块导入期就注册进 REGISTRY，进程内全局共享（与 prometheus_client 的
默认注册表一个用法）：业务代码只 import 对象再 inc / observe，不用一路传参。
"""

from __future__ import annotations

import math
import re
import threading
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_NAME_PATTERN = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# 一把全局可重入锁：所有指标对象的读写都从它过
_LOCK = threading.RLock()


def _escape_label(value: object) -> str:
    """标签值转义：反斜杠、换行、双引号（暴露格式的硬性要求）。"""

    return (
        str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    )


def _escape_help(text: object) -> str:
    """HELP 文本只转义反斜杠与换行，双引号在这里是普通字符。"""

    return str(text).replace("\\", "\\\\").replace("\n", "\\n")


def format_value(value: float) -> str:
    """渲染数值：整数不带小数点，特殊值写成 Prometheus 认识的写法。"""

    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "+Inf" if number > 0 else "-Inf"
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


class Metric:
    """指标基类：校验名字与标签，拼样本行。"""

    kind = "untyped"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        if not _NAME_PATTERN.match(name):
            raise ValueError(f"非法的指标名：{name!r}")
        if len(set(labelnames)) != len(labelnames):
            raise ValueError(f"标签名重复：{tuple(labelnames)!r}")
        for label in labelnames:
            if not _LABEL_PATTERN.match(label):
                raise ValueError(f"非法的标签名：{label!r}")
        self.name = name
        self.help = help_text
        self.labelnames: Tuple[str, ...] = tuple(labelnames)

    def _key(self, labels: Dict[str, object]) -> Tuple[str, ...]:
        unknown = set(labels) - set(self.labelnames)
        if unknown:
            raise ValueError(f"{self.name} 不认识标签：{sorted(unknown)!r}")
        missing = set(self.labelnames) - set(labels)
        if missing:
            raise ValueError(f"{self.name} 缺少标签：{sorted(missing)!r}")
        return tuple(str(labels[label]) for label in self.labelnames)

    def _line(
        self,
        key: Tuple[str, ...],
        value: float,
        suffix: str = "",
        extra: Sequence[Tuple[str, object]] = (),
    ) -> str:
        name = self.name + suffix
        pairs = [
            f'{label}="{_escape_label(raw)}"'
            for label, raw in zip(self.labelnames, key)
        ]
        pairs += [f'{label}="{_escape_label(raw)}"' for label, raw in extra]
        if not pairs:
            return f"{name} {format_value(value)}"
        return f"{name}{{{','.join(pairs)}}} {format_value(value)}"

    def lines(self) -> Iterable[str]:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


class Counter(Metric):
    """只增不减的累计量（进程重启才归零）。"""

    kind = "counter"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: Dict[Tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **labels: object) -> None:
        key = self._key(labels)
        with _LOCK:
            self._values[key] = self._values.get(key, 0.0) + float(amount)

    def value(self, **labels: object) -> float:
        with _LOCK:
            return self._values.get(self._key(labels), 0.0)

    def lines(self) -> Iterable[str]:
        with _LOCK:
            snapshot = sorted(self._values.items())
        for key, value in snapshot:
            yield self._line(key, value)

    def reset(self) -> None:
        with _LOCK:
            self._values.clear()


class Gauge(Metric):
    """可增可减的瞬时值，采集端抓到的是抓取那一刻的值。"""

    kind = "gauge"

    def __init__(self, name: str, help_text: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, labelnames)
        self._values: Dict[Tuple[str, ...], float] = {}

    def set(self, value: float, **labels: object) -> None:
        key = self._key(labels)
        with _LOCK:
            self._values[key] = float(value)

    def inc(self, amount: float = 1.0, **labels: object) -> None:
        key = self._key(labels)
        with _LOCK:
            self._values[key] = self._values.get(key, 0.0) + float(amount)

    def dec(self, amount: float = 1.0, **labels: object) -> None:
        self.inc(-float(amount), **labels)

    def value(self, **labels: object) -> float:
        with _LOCK:
            return self._values.get(self._key(labels), 0.0)

    def lines(self) -> Iterable[str]:
        with _LOCK:
            snapshot = sorted(self._values.items())
        for key, value in snapshot:
            yield self._line(key, value)

    def reset(self) -> None:
        with _LOCK:
            self._values.clear()


class Histogram(Metric):
    """累计直方图：按固定桶累计，同时记总和与次数，分位数由采集端算。

    桶只记「小于等于上界」的增量，`_bucket` 行渲染时才累加——这样 observe 只需
    更新一个桶，不用每来一个值就把整排桶重写一遍。
    """

    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        buckets: Sequence[float] = (),
        labelnames: Sequence[str] = (),
    ) -> None:
        super().__init__(name, help_text, labelnames)
        ordered = tuple(sorted(float(bound) for bound in buckets))
        if not ordered:
            raise ValueError("直方图至少要有一个桶")
        self.buckets = ordered
        self._counts: Dict[Tuple[str, ...], List[int]] = {}
        self._sums: Dict[Tuple[str, ...], float] = {}
        self._totals: Dict[Tuple[str, ...], int] = {}

    def observe(self, value: float, **labels: object) -> None:
        key = self._key(labels)
        number = float(value)
        with _LOCK:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            for position, bound in enumerate(self.buckets):
                if number <= bound:
                    counts[position] += 1
                    break
            self._sums[key] = self._sums.get(key, 0.0) + number
            self._totals[key] = self._totals.get(key, 0) + 1

    def count(self, **labels: object) -> int:
        with _LOCK:
            return self._totals.get(self._key(labels), 0)

    def lines(self) -> Iterable[str]:
        with _LOCK:
            keys = sorted(set(self._counts) | set(self._sums))
            snapshot = {
                key: (
                    list(self._counts.get(key, ())),
                    self._sums.get(key, 0.0),
                    self._totals.get(key, 0),
                )
                for key in keys
            }
        for key in keys:
            counts, total_sum, total = snapshot[key]
            cumulative = 0
            for position, bound in enumerate(self.buckets):
                if position < len(counts):
                    cumulative += counts[position]
                yield self._line(
                    key, cumulative, "_bucket", (("le", format_value(bound)),)
                )
            yield self._line(key, total, "_bucket", (("le", "+Inf"),))
            yield self._line(key, total_sum, "_sum")
            yield self._line(key, total, "_count")

    def reset(self) -> None:
        with _LOCK:
            self._counts.clear()
            self._sums.clear()
            self._totals.clear()


class Registry:
    """指标注册表：按注册顺序渲染，同一份数据每次输出的行序都一样。"""

    def __init__(self) -> None:
        self._metrics: List[Metric] = []

    def register(self, metric: Metric) -> Metric:
        with _LOCK:
            if metric not in self._metrics:
                self._metrics.append(metric)
        return metric

    def metrics(self) -> Tuple[Metric, ...]:
        with _LOCK:
            return tuple(self._metrics)

    def get(self, name: str) -> Metric:
        for metric in self.metrics():
            if metric.name == name:
                return metric
        raise KeyError(name)

    def find(self, name: str) -> Optional[Metric]:
        for metric in self.metrics():
            if metric.name == name:
                return metric
        return None

    def render(self) -> str:
        """渲染成 Prometheus 文本暴露格式，末尾留一个换行。"""

        lines: List[str] = []
        with _LOCK:
            for metric in self._metrics:
                lines.append(f"# HELP {metric.name} {_escape_help(metric.help)}")
                lines.append(f"# TYPE {metric.name} {metric.kind}")
                lines.extend(metric.lines())
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """清空样本值，不注销指标（测试与本地排查用）。"""

        with _LOCK:
            for metric in self._metrics:
                metric.reset()


REGISTRY = Registry()


# ---------------------------------------------------------------------------
# 本项目实际采集的指标
# 命名依 Prometheus 惯例：累计量以 _total 结尾，耗时一律换算成秒。
# 分类只放取值有限的维度（outcome / source），路径用路由模板而不是真实 URL，
# 否则每一张图片都会长出一个新时间序列，采集端会被基数打爆。
# ---------------------------------------------------------------------------

HTTP_REQUESTS = REGISTRY.register(
    Counter(
        "http_requests_total",
        "HTTP 请求数，按方法、路由模板与状态码分列",
        ("method", "path", "status"),
    )
)

HTTP_DURATION = REGISTRY.register(
    Histogram(
        "http_request_duration_seconds",
        "HTTP 请求耗时（秒），按方法、路由模板分列",
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
        labelnames=("method", "path"),
    )
)

RECOGNIZE_REQUESTS = REGISTRY.register(
    Counter(
        "recognize_requests_total",
        "识别接口请求数，outcome 取 ok / degraded / error",
        ("outcome",),
    )
)

RECOGNIZE_INFERENCE = REGISTRY.register(
    Histogram(
        "recognize_inference_seconds",
        "单次识别的前向耗时（秒），不含排队与请求体解析",
        buckets=(0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0),
    )
)

RECOGNIZE_QUEUE = REGISTRY.register(
    Histogram(
        "recognize_queue_seconds",
        "请求在推理闸门前排队的时间（秒），容量打满时这个值会抬起来",
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0),
    )
)

RECOGNIZE_IN_FLIGHT = REGISTRY.register(
    Gauge("recognize_in_flight", "当前占用推理名额的请求数")
)

RECIPE_RESOLVE = REGISTRY.register(
    Counter(
        "recipe_resolve_total",
        "菜谱解析结果数，source 取 library / generated / unavailable",
        ("source",),
    )
)

RECIPE_CACHE = REGISTRY.register(
    Counter(
        "recipe_cache_total",
        "生成结果的本地缓存命中情况，result 取 hit / miss",
        ("result",),
    )
)

GENERATOR_REQUESTS = REGISTRY.register(
    Counter(
        "generator_requests_total",
        "大模型生成调用数，outcome 取 ok / invalid / error / skipped",
        ("outcome",),
    )
)

GENERATOR_DURATION = REGISTRY.register(
    Histogram(
        "generator_request_seconds",
        "大模型生成调用耗时（秒），含网络往返",
        buckets=(0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0),
    )
)

GENERATOR_TOKENS = REGISTRY.register(
    Counter(
        "generator_tokens_total",
        "大模型消耗的 token 数，kind 取 input / output",
        ("kind",),
    )
)
