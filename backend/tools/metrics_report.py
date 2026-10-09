# -*- coding: utf-8 -*-
"""把 /metrics 的暴露文本读成一张人能看的表（设计方案 5.5）。

项目没有接 Prometheus + Grafana，指标抓下来是一堆 `name{label="value"} 数字`，
看不出服务现在什么状态。这个工具做两件事：把文本解析回结构化数据，再按分桶用线性
插值估算 p50 / p95 打印出来。

  .venv\\Scripts\\python.exe backend/tools/metrics_report.py
  .venv\\Scripts\\python.exe backend/tools/metrics_report.py --url http://127.0.0.1:8000/metrics
  .venv\\Scripts\\python.exe backend/tools/metrics_report.py --file probe.txt --json

插值是估算：桶宽决定精度，桶边界之间按均匀分布假设取值。想看不带假设的分位数就上
Prometheus，这里够用来判断「延迟有没有变坏、缓存有没有省下调用」。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.observability import ensure_utf8  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8000/metrics"

_LABEL_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def unescape_label(raw: str) -> str:
    """还原暴露格式里的转义，让标签值按原文显示。"""

    out: List[str] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if character == "\\" and index + 1 < len(raw):
            following = raw[index + 1]
            out.append({"n": "\n", "\\": "\\", '"': '"'}.get(following, following))
            index += 2
            continue
        out.append(character)
        index += 1
    return "".join(out)


def parse_labels(raw: str) -> Dict[str, str]:
    return {name: unescape_label(value) for name, value in _LABEL_PATTERN.findall(raw)}


def parse_line(line: str) -> Optional[Tuple[str, Dict[str, str], float]]:
    """解析一行样本；解析不了（空行、注释、坏行）返回 None。"""

    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "{" in line:
        name, rest = line.split("{", 1)
        if "}" not in rest:
            return None
        label_text, value_text = rest.rsplit("}", 1)
        labels = parse_labels(label_text)
    else:
        pieces = line.split(None, 1)
        if len(pieces) != 2:
            return None
        name, value_text = pieces
        labels = {}
    try:
        value = float(value_text.strip())
    except ValueError:
        return None
    return name.strip(), labels, value


def parse_exposition(text: str) -> Tuple[Dict[str, str], List[Tuple[str, Dict[str, str], float]]]:
    types: Dict[str, str] = {}
    samples: List[Tuple[str, Dict[str, str], float]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            parts = stripped.split(None, 3)
            if len(parts) == 4 and parts[1] == "TYPE":
                types[parts[2]] = parts[3].strip()
            continue
        parsed = parse_line(stripped)
        if parsed is not None:
            samples.append(parsed)
    return types, samples


def label_suffix(labels: Dict[str, str]) -> str:
    if not labels:
        return ""
    pairs = ",".join("%s=%s" % (name, labels[name]) for name in sorted(labels))
    return "{%s}" % pairs


def histogram_quantile(
    buckets: Sequence[Tuple[float, float]], quantile: float
) -> Optional[float]:
    """按桶线性插值估算分位数。

    buckets 是 (上界, 累计计数) 且上界升序，最后一个是 +Inf。落进 +Inf 桶时只能给出
    一个下界，返回 None 让调用方照实说「估不出来」，不编数字。
    """

    if not buckets:
        return None
    total = buckets[-1][1]
    if total <= 0:
        return None
    rank = quantile * total
    lower_bound = 0.0
    previous_count = 0.0
    for upper, cumulative in buckets:
        if cumulative >= rank:
            if math.isinf(upper):
                return None
            span = cumulative - previous_count
            if span <= 0:
                return upper
            return lower_bound + (upper - lower_bound) * (rank - previous_count) / span
        lower_bound, previous_count = upper, cumulative
    return None


def summarize(
    types: Dict[str, str], samples: Sequence[Tuple[str, Dict[str, str], float]]
) -> Dict[str, object]:
    """把样本分成计数、瞬时值和直方图三组。"""

    counters: Dict[str, float] = {}
    gauges: Dict[str, float] = {}
    histograms: Dict[str, Dict[str, object]] = {}

    for name, labels, value in samples:
        if name.endswith("_bucket"):
            base = name[: -len("_bucket")]
            key = base + label_suffix({k: v for k, v in labels.items() if k != "le"})
            entry = histograms.setdefault(key, {"buckets": [], "sum": None, "count": None})
            entry["buckets"].append((float(labels.get("le", "inf")), value))  # type: ignore[union-attr]
            continue
        if name.endswith("_count") or name.endswith("_sum"):
            base, field = name.rsplit("_", 1)
            key = base + label_suffix(labels)
            entry = histograms.setdefault(key, {"buckets": [], "sum": None, "count": None})
            entry[field] = value  # type: ignore[index]
            continue
        target = counters if types.get(name) == "counter" else gauges
        target[name + label_suffix(labels)] = value

    for entry in histograms.values():
        entry["buckets"] = sorted(entry["buckets"], key=lambda item: item[0])  # type: ignore[arg-type]

    return {"counters": counters, "gauges": gauges, "histograms": histograms}


def format_report(data: Dict[str, object], source: str) -> str:
    lines = ["指标报告：%s" % source, ""]

    lines.append("计数型")
    counters: Dict[str, float] = data["counters"]  # type: ignore[assignment]
    if not counters:
        lines.append("  （还没有样本）")
    for key in sorted(counters):
        lines.append("  %-64s %s" % (key, _number(counters[key])))

    lines.append("")
    lines.append("瞬时值")
    gauges: Dict[str, float] = data["gauges"]  # type: ignore[assignment]
    if not gauges:
        lines.append("  （还没有样本）")
    for key in sorted(gauges):
        lines.append("  %-64s %s" % (key, _number(gauges[key])))

    lines.append("")
    lines.append("耗时分布（p50 / p95 由桶线性插值估算）")
    histograms: Dict[str, Dict[str, object]] = data["histograms"]  # type: ignore[assignment]
    if not histograms:
        lines.append("  （还没有样本）")
    for key in sorted(histograms):
        entry = histograms[key]
        count = entry.get("count")
        if not count:
            lines.append("  %-64s （还没有样本）" % key)
            continue
        p50 = histogram_quantile(entry["buckets"], 0.5)  # type: ignore[arg-type]
        p95 = histogram_quantile(entry["buckets"], 0.95)  # type: ignore[arg-type]
        lines.append(
            "  %-64s n=%-6s p50=%-12s p95=%s"
            % (
                key,
                _number(count),
                _milliseconds(p50),
                _milliseconds(p95),
            )
        )
    return "\n".join(lines)


def _number(value: object) -> str:
    number = float(value)  # type: ignore[arg-type]
    return str(int(number)) if number == int(number) else ("%.4f" % number).rstrip("0")


def _milliseconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "估不出来"
    return "%.1fms" % (seconds * 1000.0)


def fetch(url: str) -> str:
    import httpx

    response = httpx.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def build_report(text: str, source: str) -> Dict[str, object]:
    types, samples = parse_exposition(text)
    data = summarize(types, samples)
    data["source"] = source
    return data


def main(argv: Optional[Sequence[str]] = None) -> int:
    # 输出重定向到文件或管道时 Windows 默认走 ANSI 代码页，中文会变乱码
    ensure_utf8(sys.stdout)
    parser = argparse.ArgumentParser(description="把 /metrics 读成一张表")
    parser.add_argument("--url", default=DEFAULT_URL, help="指标地址")
    parser.add_argument("--file", default=None, help="改从文件读暴露文本（与 --url 二选一）")
    parser.add_argument("--json", action="store_true", help="额外输出 JSON")
    args = parser.parse_args(argv)

    try:
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
            source = args.file
        else:
            text = fetch(args.url)
            source = args.url
    except Exception as exc:  # 服务没起、网络不通：给一句能看懂的话，别抛栈
        print("取指标失败（%s）：%s" % (source, exc))
        return 1

    data = build_report(text, source)
    print(format_report(data, source))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
