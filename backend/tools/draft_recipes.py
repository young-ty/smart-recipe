r"""按类别批量起草菜谱，产出**草案**交人工审校（补覆盖率的半自动入口）。

为什么只做「草案」而不直接入库：库内菜谱是这条链路的确定性来源——识别命中就返回它，
结果稳定、不联网、不花额度。生成结果的可信度低于人工过一遍的数据，直接混进菜谱库会把
这个保证贬值。所以流程刻意做成三段，**中间那一段必须是人**：

    1. --plan   只读：列出留白类别，说明这一次会处理哪些
    2. --write  调用生成层，把结果写成草案（drafts/recipe_drafts.json）
    3. 人工审校：改菜名/步骤/用量，补 rating（生成层按设计不给评分），把 approved 改成 true
    4. --merge  只合并 approved 且字段齐全的草案，其余原样跳过

食材名要用上 seed_ingredients.json 的 id：模型给的是「西红柿」「蒜」这类写法，
所以匹配走「食材名 + 别名」。匹配不上的记进 unmatched_ingredients，合并时会被拦下来，
由人决定是新增食材还是换个写法。

用法（在 backend 目录下运行）：

    ..\.venv\Scripts\python.exe -m tools.draft_recipes            # 等价于 --plan，不联网
    ..\.venv\Scripts\python.exe -m tools.draft_recipes --write --limit 10
    ..\.venv\Scripts\python.exe -m tools.draft_recipes --write --indices 2,6,7
    ..\.venv\Scripts\python.exe -m tools.draft_recipes --merge

草案目录 drafts/ 已 gitignore：它是中间产物，审校后合进 seed 数据，本身不留版本。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings, get_settings  # noqa: E402
from app.services.generator import (  # noqa: E402
    GeneratedRecipePayload,
    RecipeGenerator,
    build_generator,
)

DATA_DIR = BACKEND_DIR / "app" / "data"
LABELS_PATH = REPO_ROOT / "models" / "dish_labels.json"
INGREDIENTS_PATH = DATA_DIR / "seed_ingredients.json"
RECIPES_PATH = DATA_DIR / "seed_recipes.json"
DISH_MAP_PATH = DATA_DIR / "seed_dish_map.json"
DRAFTS_PATH = REPO_ROOT / "drafts" / "recipe_drafts.json"

DEFAULT_LIMIT = 10


class DraftError(RuntimeError):
    """草案不合规（缺 rating、食材没映射上……），合并时必须拦下来。"""


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    """写回数据文件：固定 2 空格缩进、保留中文、末尾换行，避免整文件重排。

    父目录不存在就建（草案落在 drafts/，默认不带这个目录）。
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------- 食材映射

def build_ingredient_index(rows: Sequence[dict]) -> Dict[str, int]:
    """把「食材名 + 别名」摊平成 名字 → id 的查表。"""

    index: Dict[str, int] = {}
    for row in rows:
        names = [row["name"]] + [
            alias.strip() for alias in (row.get("alias") or "").split(",") if alias.strip()
        ]
        for name in names:
            index.setdefault(name, row["id"])
    return index


def match_ingredient(name: str, index: Dict[str, int]) -> Optional[int]:
    """按名字或别名查 id；查不到返回 None（交给人工，不猜）。"""

    return index.get((name or "").strip())


# --------------------------------------------------------------- 待办类别

def blank_classes(labels: Dict[str, str], mapping: Dict[str, dict]) -> List[Tuple[int, str]]:
    """留白类别：既没菜谱也没登记缺口的那些。"""

    rows = [
        (int(index), name)
        for index, name in labels.items()
        if str(index) not in mapping
    ]
    return sorted(rows)


def next_recipe_id(recipes: Sequence[dict]) -> int:
    return max((row["id"] for row in recipes), default=100) + 1


def drafted_indices(drafts: dict) -> set:
    return {row["class_index"] for row in drafts.get("drafts", [])}


# --------------------------------------------------------------- 生成草案

def payload_to_draft(
    class_index: int,
    class_name: str,
    recipe_id: int,
    payload: GeneratedRecipePayload,
    ingredient_index: Dict[str, int],
) -> dict:
    """把生成层的结构化输出转成草案条目，并记录没映射上的食材。"""

    unmatched: List[str] = []

    def convert(items):
        converted = []
        for item in items:
            ingredient_id = match_ingredient(item.name, ingredient_index)
            if ingredient_id is None:
                unmatched.append(item.name)
            converted.append({
                "name": item.name,
                "amount": item.amount,
                "ingredient_id": ingredient_id,
            })
        return converted

    return {
        "class_index": class_index,
        "class_name": class_name,
        "recipe_id": recipe_id,
        "approved": False,
        "rating": None,  # 生成层按设计不给评分，必须人工补
        "unmatched_ingredients": unmatched,
        "recipe": {
            "name": payload.name,
            "cook_time_minutes": payload.cook_time_minutes,
            "difficulty": payload.difficulty,
            "servings": payload.servings,
            "description": payload.description,
            "required": convert(payload.required),
            "optional": convert(payload.optional),
            "steps": list(payload.steps),
        },
    }


def draft_one(
    generator: RecipeGenerator,
    class_index: int,
    class_name: str,
    recipe_id: int,
    ingredient_index: Dict[str, int],
) -> Optional[dict]:
    """真实调用一次；失败（超时 / 校验不过）返回 None，不伪造草案。"""

    payload = generator.generate(class_name)
    if payload is None:
        return None
    return payload_to_draft(class_index, class_name, recipe_id, payload, ingredient_index)


# --------------------------------------------------------------- 合并

def validate_for_merge(draft: dict) -> None:
    """合并前的硬门槛：人工审校必须留下痕迹，缺字段一律拒绝整条。"""

    name = draft.get("class_name") or "#%s" % draft.get("class_index")
    if not draft.get("approved"):
        raise DraftError("%s：还没审校（approved 不是 true）" % name)
    if draft.get("rating") is None:
        raise DraftError("%s：缺 rating，评分必须人工给" % name)
    if draft.get("unmatched_ingredients"):
        raise DraftError(
            "%s：还有食材没映射到 id（%s）"
            % (name, "、".join(draft["unmatched_ingredients"])),
        )
    recipe = draft.get("recipe") or {}
    for group in ("required", "optional"):
        for item in recipe.get(group) or []:
            if item.get("ingredient_id") is None:
                raise DraftError("%s：%s 里的「%s」没有 ingredient_id" % (name, group, item.get("name")))
    if not recipe.get("steps"):
        raise DraftError("%s：步骤为空" % name)


def draft_to_seed_recipe(draft: dict) -> dict:
    """草案 → seed_recipes.json 的条目形状（去掉审校辅助字段）。"""

    recipe = dict(draft["recipe"])
    return {
        "id": draft["recipe_id"],
        "name": recipe["name"],
        "cook_time_minutes": recipe["cook_time_minutes"],
        "difficulty": recipe["difficulty"],
        "rating": draft["rating"],
        "servings": recipe.get("servings", 2),
        "description": recipe.get("description"),
        "required": [
            {"id": item["ingredient_id"], "amount": item.get("amount")}
            for item in recipe.get("required") or []
        ],
        "optional": [
            {"id": item["ingredient_id"], "amount": item.get("amount")}
            for item in recipe.get("optional") or []
        ],
        "steps": list(recipe["steps"]),
    }


def merge_drafts(drafts: dict, recipes: list, mapping: dict) -> Tuple[list, dict, List[str]]:
    """把审校过的草案合进菜谱库与承接表，返回 (新菜谱表, 新承接表, 跳过说明)。

    刻意不做「部分合并」：任何一条不合规就抛错，让人先补齐再重跑——
    半合进去的状态最难查。
    """

    known_ids = {row["id"] for row in recipes}
    new_recipes = list(recipes)
    new_mapping = dict(mapping)
    skipped: List[str] = []

    for draft in drafts.get("drafts", []):
        if not draft.get("approved"):
            skipped.append("#%s %s：未审校" % (draft["class_index"], draft["class_name"]))
            continue
        validate_for_merge(draft)
        if draft["recipe_id"] in known_ids:
            raise DraftError("菜谱 id %s 已存在，先改 recipe_id" % draft["recipe_id"])
        new_recipes.append(draft_to_seed_recipe(draft))
        known_ids.add(draft["recipe_id"])
        recipe_name = draft["recipe"]["name"]
        new_mapping[str(draft["class_index"])] = {
            "recipe_id": draft["recipe_id"],
            # 菜名与类别名不同时，类别名必须进 alias，否则识别结果命不中
            "alias": None if recipe_name == draft["class_name"] else draft["class_name"],
        }

    ordered = sorted(new_mapping.items(), key=lambda item: int(item[0]))
    return new_recipes, {key: value for key, value in ordered}, skipped


# --------------------------------------------------------------- 入口

def _select(blank, drafts, args):
    """按 --indices / --limit 挑出这次要处理的类别，已起草过的默认跳过。"""

    if args.indices:
        wanted = [int(part) for part in args.indices.split(",") if part.strip()]
        selected = [row for row in blank if row[0] in wanted]
        missing = sorted(set(wanted) - {row[0] for row in selected})
        if missing:
            raise DraftError("这些类别不是留白状态（已登记或缺席）：%s" % missing)
        return selected
    done = drafted_indices(drafts)
    remaining = [row for row in blank if row[0] not in done] if not args.force else blank
    return remaining[: args.limit]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="按类别起草菜谱草案（人工审校后再合并）")
    parser.add_argument("--write", action="store_true", help="真实调用生成层并写入草案（消耗额度）")
    parser.add_argument("--merge", action="store_true", help="合并 approved 的草案到种子数据")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="--write 一次处理几类")
    parser.add_argument("--indices", default="", help="只处理这些类别下标，逗号分隔")
    parser.add_argument("--force", action="store_true", help="重生成已起草过的类别")
    parser.add_argument("--drafts", type=Path, default=DRAFTS_PATH)
    args = parser.parse_args(argv)

    settings = get_settings()
    labels = load_json(LABELS_PATH)
    mapping = load_json(DISH_MAP_PATH)
    recipes = load_json(RECIPES_PATH)
    drafts = load_json(args.drafts) if args.drafts.exists() else {"drafts": []}
    blank = blank_classes(labels, mapping)

    if args.merge:
        recipes_new, mapping_new, skipped = merge_drafts(drafts, recipes, mapping)
        write_json(RECIPES_PATH, recipes_new)
        write_json(DISH_MAP_PATH, mapping_new)
        print("已合并 %d 条，跳过 %d 条" % (len(recipes_new) - len(recipes), len(skipped)))
        for note in skipped:
            print("  跳过（%s）" % note)
        print("接下来跑一遍测试：pytest backend/tests -q")
        return 0

    if not args.write:
        print("留白类别 %d 个，已起草 %d 个。" % (len(blank), len(drafted_indices(drafts))))
        for index, name in _select(blank, drafts, args):
            print("  #%-3d %s" % (index, name))
        print("\n加 --write 才会真实调用生成层（消耗额度）。")
        return 0

    if not settings.generator_configured:
        print("未配置 RECIPE_GENERATOR_API_KEY，无法起草。")
        return 1

    # 起草是离线数据准备工作，用的是本地工具那份 Key（见 7.8）
    generator = build_generator(settings, api_key=settings.generator_api_key)
    ingredient_index = build_ingredient_index(load_json(INGREDIENTS_PATH))
    selected = _select(blank, drafts, args)
    recipe_id = next_recipe_id(recipes + drafts.get("drafts", []))
    written = drafts.get("drafts", [])
    created = 0
    for index, name in selected:
        draft = draft_one(generator, index, name, recipe_id, ingredient_index)
        if draft is None:
            print("  #%-3d %s：生成失败，跳过（不写半截草案）" % (index, name))
            continue
        written = [row for row in written if row["class_index"] != index] + [draft]
        recipe_id += 1
        created += 1
        print("  #%-3d %s → 草案 ok（未映射食材 %d 个）" % (index, name, len(draft["unmatched_ingredients"])))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": getattr(generator, "model", "unset"),
        "prompt_version": settings.prompt_version,
        "drafts": sorted(written, key=lambda row: row["class_index"]),
    }
    write_json(args.drafts, payload)
    print("\n写入 %s：新增 %d 条，累计 %d 条待审校。" % (args.drafts, created, len(payload["drafts"])))
    print("人工审校后把 approved 改成 true、补上 rating，再跑 --merge。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
