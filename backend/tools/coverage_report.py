r"""菜谱库覆盖率报表：把 208 类拆成三个分区，并挑出"免费"能补的缺口。

背景：单说一个「N/208 类可承接」是不够的。类别-菜谱承接表 `seed_dish_map.json`
其实有三个分区，混在一起看会得出错误的结论：

- **可承接**：登记了非空 `recipe_id`，识别命中直接返回库内菜谱，不联网、结果确定；
- **显式缺口**：登记了 `recipe_id: null`，即"这类我们知道没有菜谱"，走大模型生成分支——
  缺口是**显式记录**的，不静默丢弃；
- **留白**：压根没登记。既没菜谱也没声明，报表里必须单独列出来，否则永远不知道分母应该是多少。

另外两件事只有这个脚本会告诉你：

1. **闲置菜谱**：库内菜谱里没有任何类别指向它的。菜谱写了却永远命中不到，等于没写；
2. **别名候选**：留白类别的名字与某道闲置菜谱高度相似（西红柿/番茄、茸/蓉 这类同义或错字），
   确认是同一道菜后，加一行映射即可补上——零成本，不用新写菜谱。

同义词表与相似度都从 `app/services/dish_text.py` 取，与运行时召回共用一套规则：
报表说「这个缺口靠别名就能补」，线上就必须真能召回到，否则报表只是好看。

用法（在 backend 目录下运行）：

    ..\.venv\Scripts\python.exe -m tools.coverage_report
    ..\.venv\Scripts\python.exe -m tools.coverage_report --json

脚本**只读**，不改任何数据文件。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.dish_text import SYNONYMS, fold_synonyms, similarity  # noqa: E402,F401

DEFAULT_LABELS = REPO_ROOT / "models" / "dish_labels.json"
DEFAULT_MAP = BACKEND_DIR / "app" / "data" / "seed_dish_map.json"
DEFAULT_RECIPES = BACKEND_DIR / "app" / "data" / "seed_recipes.json"

# 别名候选的相似度门槛：低于它就不报了，避免刷屏
SIMILARITY_THRESHOLD = 0.55


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize(name: str) -> str:
    """报表侧的菜名归一：去空白 + 同义写法折叠。

    同义词表与相似度来自 `app.services.dish_text`，与运行时召回共用一套规则：
    报表说「这个缺口能靠别名补」，线上就得真能补上。
    """

    return fold_synonyms(name.replace(" ", "").replace("　", ""))


def _first(row) -> int:
    """按类别下标排序用。"""

    return row[0]


def split_classes(labels: Dict[str, str], mapping: Dict[str, dict]) -> Dict[str, list]:
    """按承接表把类别分成可承接 / 显式缺口 / 留白三组。"""

    available: List[Tuple[int, str, int]] = []
    declared_gaps: List[Tuple[int, str]] = []
    blank: List[Tuple[int, str]] = []
    for index_text, name in labels.items():
        entry = mapping.get(str(index_text))
        if entry is None:
            blank.append((int(index_text), name))
        elif entry.get("recipe_id") is None:
            declared_gaps.append((int(index_text), name))
        else:
            available.append((int(index_text), name, entry["recipe_id"]))
    return {
        "available": sorted(available, key=_first),
        "declared_gaps": sorted(declared_gaps, key=_first),
        "blank": sorted(blank, key=_first),
    }


def idle_recipes(recipes: Sequence[dict], mapping: Dict[str, dict]) -> List[dict]:
    """没有任何类别指向它的菜谱：写了也用不上。"""

    pointed = {entry.get("recipe_id") for entry in mapping.values() if entry.get("recipe_id")}
    return [row for row in recipes if row["id"] not in pointed]


def alias_candidates(
    classes: Sequence[Tuple[int, str]],
    idle: Sequence[dict],
    threshold: float = SIMILARITY_THRESHOLD,
) -> List[Tuple[int, str, List[Tuple[str, float]]]]:
    """留白类别 vs 闲置菜谱：疑似同一道菜的配对，交人工确认。"""

    found = []
    for index, class_name in classes:
        norm_class = normalize(class_name)
        matches = []
        for recipe in idle:
            norm_recipe = normalize(recipe["name"])
            if norm_class == norm_recipe:
                score = 1.0
            elif norm_class in norm_recipe or norm_recipe in norm_class:
                score = 0.9
            else:
                score = round(similarity(norm_class, norm_recipe), 2)
                if score < threshold:
                    continue
            matches.append((recipe["name"], score))
        if matches:
            found.append((index, class_name, sorted(matches, key=lambda item: -item[1])))
    return found


def build_report(labels, mapping, recipes) -> dict:
    groups = split_classes(labels, mapping)
    idle = idle_recipes(recipes, mapping)
    total = len(labels)
    served = len(groups["available"])
    registered = served + len(groups["declared_gaps"])
    return {
        "total_classes": total,
        "served": served,
        "declared_gaps": [(i, n) for i, n in groups["declared_gaps"]],
        "blank": [(i, n) for i, n in groups["blank"]],
        "available": [(i, n, rid) for i, n, rid in groups["available"]],
        "recipes_total": len(recipes),
        "idle_recipes": [{"id": r["id"], "name": r["name"]} for r in idle],
        "alias_candidates": [
            {"index": i, "class_name": n, "matches": [{"recipe": r, "score": s} for r, s in m]}
            for i, n, m in alias_candidates(groups["blank"], idle)
        ],
        "coverage_of_all": served / total if total else 0.0,
        "coverage_of_registered": served / registered if registered else 0.0,
    }


def render(report: dict) -> str:
    lines = [
        "菜谱库覆盖率报表（只读）",
        "=" * 46,
        "类别总数 %d" % report["total_classes"],
        "  可承接（有菜谱）    %d  → 占全部 %.1f%%，占已登记 %.1f%%" % (
            report["served"],
            report["coverage_of_all"] * 100,
            report["coverage_of_registered"] * 100,
        ),
        "  显式缺口（登记 null）%d  → 走大模型生成分支" % len(report["declared_gaps"]),
        "  留白（未登记）      %d  → 既没菜谱也没声明，分母说不清" % len(report["blank"]),
        "",
        "库内菜谱 %d 道，被类别引用 %d 道，闲置 %d 道" % (
            report["recipes_total"],
            report["recipes_total"] - len(report["idle_recipes"]),
            len(report["idle_recipes"]),
        ),
    ]
    if report["declared_gaps"]:
        lines.append("  显式缺口：" + "、".join("#%d %s" % (i, n) for i, n in report["declared_gaps"]))
    lines.append("")
    lines.append("闲置菜谱（写了但没有任何类别指向）：")
    lines += ["  - %s（#%d）" % (r["name"], r["id"]) for r in report["idle_recipes"]]
    lines.append("")
    lines.append("别名候选（留白类别 ≈ 闲置菜谱，确认后加一行映射即可补上）：")
    if not report["alias_candidates"]:
        lines.append("  （无）")
    for row in report["alias_candidates"]:
        best = "；".join("%s (%.2f)" % (m["recipe"], m["score"]) for m in row["matches"][:3])
        lines.append("  #%-3d %s  ≈  %s" % (row["index"], row["class_name"], best))
    lines.append("")
    lines.append("下一步：先把别名候选逐条确认（零成本），再决定要新写哪些菜谱。")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="菜谱库覆盖率报表（只读）")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--recipes", type=Path, default=DEFAULT_RECIPES)
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于脚本消费")
    args = parser.parse_args(argv)

    report = build_report(
        load_json(args.labels), load_json(args.map), load_json(args.recipes)
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
