r"""把训练产出的类别清单（CSV）转成后端运行时用的 JSON。

清单给的是 `Food list - Menu.csv`：两行表头 + 208 行数据，
列为 `List No.,Chinese Name,English Name`。后端要的是「类别下标 → 菜名」的扁平映射，
所以这里做一次转换，并把校验做足——**映射错了比没有映射更糟**：
下标必须连续覆盖 0~207、菜名不能为空、不能重复。

用法：

    .\.venv\Scripts\python.exe backend\tools\import_labels.py "ONNX/Food list - Menu.csv"
    .\.venv\Scripts\python.exe backend\tools\import_labels.py 输入.csv --out models/dish_labels.json

CSV 里的英文名列会在解析时做完整性检查，但不写进 JSON：
后端只用到中文名，多存一份反而容易和前端各拿一份对不上。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "models" / "dish_labels.json"
EXPECTED_CLASSES = 208


def parse_csv(text: str):
    """返回 (class_index → 中文名, 英文名数量)，表头行按「首列是数字」自动跳过。"""

    names = {}
    english = 0
    for row in csv.reader(text.splitlines()):
        if not row or not row[0].strip().isdigit():
            continue
        index = int(row[0])
        if len(row) < 3 or not row[1].strip() or not row[2].strip():
            raise SystemExit("第 %d 行列数不足或名字为空：%r" % (index, row))
        if index in names:
            raise SystemExit("类别下标重复：%d" % index)
        names[index] = row[1].strip()
        english += 1
    return names, english


def main() -> int:
    parser = argparse.ArgumentParser(description="CSV 类别清单 → models/dish_labels.json")
    parser.add_argument("source", type=Path, help="训练产出的 CSV")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出路径")
    args = parser.parse_args()

    if not args.source.is_file():
        raise SystemExit("找不到输入文件：%s" % args.source)

    names, english = parse_csv(args.source.read_text(encoding="utf-8-sig"))

    missing = [i for i in range(EXPECTED_CLASSES) if i not in names]
    extra = [i for i in names if i >= EXPECTED_CLASSES]
    if missing or extra:
        raise SystemExit("类别下标不是 0~%d：缺 %s，越界 %s" % (EXPECTED_CLASSES - 1, missing, extra))
    duplicates = {n for n in names.values() if list(names.values()).count(n) > 1}
    if duplicates:
        raise SystemExit("菜名重复，顺序可能错位：%s" % sorted(duplicates))

    payload = {str(index): names[index] for index in range(EXPECTED_CLASSES)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("已写入 %s：%d 个类别（英文名 %d 条一并核过）" % (args.out, len(payload), english))
    print("抽样：%s" % "、".join("#%d %s" % (i, payload[str(i)]) for i in (0, 77, 121, 131, 157)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
