"""菜谱检索与生成分流（设计方案 7.4）。

判定顺序是硬约束：**先查库，再生成**。菜谱库存在时永远以库内结果为准，
保证同一道菜多次刷新结果稳定，不因大模型的随机性抖动；生成层只负责补空。

```
菜名（可能来自候选，也可能是用户手输）
  → 归一化（去空白、全半角统一、去「的做法」这类噪声后缀）
  → 精确匹配 recipes.name
  → 未命中则匹配 dish_recipe_map.alias
  → 未命中则按类别下标走 dish_recipe_map 的承接关系
  → 仍未命中 → 生成分支（FR-13）：先查缓存，再调大模型，失败则 unavailable
```

生成结果按 sha1(菜名 + 提示词版本) 缓存：提示词版本变更即换键，历史缓存不误用。

生成成功的菜谱会**提升入库**（`recipes.origin = "generated"`，id 从
`settings.promoted_recipe_id_base` 起编）：下次同名请求直接命中库内，结果稳定且不再
消耗额度。落库失败或 `promote_generated` 关闭时只写缓存，本次回答不受影响。

生成结果入库前要过一道闸（`_sanitize_generated`）：菜名必须就是请求的那道菜，主辅料
必须能挂到食材表上。闸门放在缓存之前、落库之前，所以「当次返回的菜谱」和「入库后再查
到的菜谱」是同一份——否则用户刷新一下，菜谱就少了几样料。
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from ..core.config import Settings, get_settings
from ..core.metrics import RECIPE_CACHE, RECIPE_RESOLVE
from ..models import (
    DishRecipeMap,
    GeneratedRecipe,
    Ingredient,
    Recipe,
    RecipeIngredient,
    RecipeStep,
)
from .generator import (
    GeneratedRecipePayload,
    IngredientAmount,
    RecipeGenerator,
    strip_invisible,
)
from .dish_text import fold_synonyms

logger = logging.getLogger(__name__)

SOURCE_LIBRARY = "library"
SOURCE_GENERATED = "generated"
SOURCE_UNAVAILABLE = "unavailable"

# 用户手输时常见的噪声后缀，归一化时剥掉（长后缀先匹配）
NOISE_SUFFIXES = ("怎么做", "如何做", "的做法", "的菜谱", "做法", "菜谱")
# 类别清单未到位时页面上的占位名，形如「类别 #77」
PLACEHOLDER_PATTERN = re.compile(r"^类别\s*#\s*\d+$")

UNAVAILABLE_REASON = "菜谱库暂缺，生成服务不可用"
# 没带凭据时的说法要和「调了但失败」分开：前者用户自己能修，后者只能等
NO_CREDENTIALS_REASON = "菜谱库暂缺，这道菜要现写需要你自己的模型 Key：在左侧「我的模型 Key」里填一把"
PLACEHOLDER_REASON = "该类别还没有菜名，先补类别清单再走生成"

# 模型写食材时爱加括号说明（「清水（打浆用）」「面粉(中筋)」），匹配前先剥掉
INGREDIENT_NOTE_PATTERN = re.compile(r"[（(][^）)]*[）)]")
# 子串兜底的最低字数：单字字典名（「盐」「姜」）太容易误命中，不参与兜底
MIN_SUBSTRING_MATCH = 2


def normalize_dish(raw: str) -> str:
    """菜名归一化：全角转半角、去控制与零宽字符、去所有空白、剥掉噪声后缀。

    手输菜名（FR-10）是自由文本：零宽字符与控制符既会污染缓存键，又会被大模型
    当成另一条指令读，所以在这一层就清掉，库内检索与生成分支拿到的是同一个名字。
    """

    text = unicodedata.normalize("NFKC", raw or "").strip()
    text = strip_invisible(text)
    text = re.sub(r"\s+", "", text)
    for suffix in NOISE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return text


def dish_key(raw: str) -> str:
    """菜名之间比对用的键：结构归一 + 同义写法折叠（`dish_text` 那张表）。

    与 `normalize_dish` 分开是有意的：归一化同时被 `ingredient_key` 复用，
    食材名那里不能把「鸡蛋」折成「蛋」，否则字典条目会对不上。
    """

    return fold_synonyms(normalize_dish(raw))


def is_placeholder(name: str) -> bool:
    """是否为「类别 #N」占位名——这种名字没有语义，不能送进大模型。"""

    return bool(PLACEHOLDER_PATTERN.match(name or ""))


def ingredient_key(raw: str) -> str:
    """食材名的归一化键：先剥掉括号里的说明，再按菜名同一套规则收尾。"""

    return normalize_dish(INGREDIENT_NOTE_PATTERN.sub("", raw or ""))


def match_ingredient(name: str, index: dict) -> Optional[int]:
    """把生成的食材名挂到食材字典上：先精确（含别名），再退一步按子串兜底。

    模型不会每次都写字典里的标准名，「干黄豆」「猪里脊肉」「清水（打浆用）」这类写法
    都要能落到 黄豆 / 猪里脊 / 清水 上。兜底只认两字以上的字典名，避免「盐」这种
    单字名把不相干的食材也吸过来。
    """

    key = ingredient_key(name)
    if not key:
        return None
    hit = index.get(key)
    if hit is not None:
        return hit
    best_length, best_id = 0, None
    for dict_key, ingredient_id in index.items():
        if (
            len(dict_key) >= MIN_SUBSTRING_MATCH
            and dict_key in key
            and len(dict_key) > best_length
        ):
            best_length, best_id = len(dict_key), ingredient_id
    return best_id


def cache_key(dish: str, prompt_version: str) -> str:
    """生成结果的缓存键：sha1(菜名 + 提示词版本)。

    键只由菜名与提示词版本决定，同一道菜重复查询稳定命中同一份结果。
    """

    return hashlib.sha1(f"{dish}{prompt_version}".encode("utf-8")).hexdigest()


def build_video_url(name: str, settings: Settings) -> str:
    """视频入口由后端按菜名拼接并做 URL 编码（FR-14）。"""

    keyword = urllib.parse.quote(f"{name} 做法")
    return settings.video_search_template.format(keyword=keyword)


@dataclass(frozen=True)
class IngredientLine:
    name: str
    amount: Optional[str] = None


def recipe_view(recipe: Recipe, settings: Settings) -> RecipeView:
    """把 ORM 菜谱翻成接口视图（库内分支与菜谱详情接口共用）。"""

    return RecipeView(
        id=recipe.id,
        name=recipe.name,
        cook_time_minutes=recipe.cook_time_minutes,
        difficulty=recipe.difficulty,
        rating=recipe.rating,
        servings=recipe.servings,
        description=recipe.description,
        required=[
            IngredientLine(link.ingredient.name, link.amount)
            for link in recipe.ingredients
            if link.is_required
        ],
        optional=[
            IngredientLine(link.ingredient.name, link.amount)
            for link in recipe.ingredients
            if not link.is_required
        ],
        steps=recipe.step_contents,
        video_url=build_video_url(recipe.name, settings),
        origin=recipe.origin,
    )


@dataclass(frozen=True)
class RecipeView:
    """给前端/接口用的菜谱视图：库内与生成共用同一形状。"""

    id: Optional[int]
    name: str
    cook_time_minutes: int
    difficulty: str
    rating: Optional[float]
    servings: int
    description: Optional[str]
    required: List[IngredientLine]
    optional: List[IngredientLine]
    steps: List[str]
    video_url: str
    # seed = 人工整理；generated = 大模型生成（已入库或本次生成），前端据此标注来源
    origin: str = "seed"


@dataclass(frozen=True)
class ResolvedRecipe:
    dish: str
    source: str
    degraded: bool
    recipe: Optional[RecipeView]
    reason: Optional[str] = None


class RecipeResolver:
    """把菜名解析成菜谱：库内命中 / 生成 / 明确不可用。"""

    def __init__(
        self,
        session: Session,
        settings: Optional[Settings] = None,
        generator: Optional[RecipeGenerator] = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._generator = generator

    # ------------------------------------------------------------------ 库内检索
    def _by_name(self, key: str) -> Optional[Recipe]:
        return self._session.scalar(select(Recipe).where(Recipe.name == key))

    def _by_alias(self, key: str) -> Optional[Recipe]:
        statement = (
            select(Recipe)
            .join(DishRecipeMap, DishRecipeMap.recipe_id == Recipe.id)
            .where(DishRecipeMap.alias == key)
        )
        return self._session.scalars(statement).first()

    def _by_class_index(self, index: int) -> Optional[Recipe]:
        row = self._session.get(DishRecipeMap, index)
        if row is None or row.recipe_id is None:
            return None
        return self._session.get(Recipe, row.recipe_id)

    def _by_synonym(self, key: str) -> Optional[Recipe]:
        """同义写法兜底：库内写「番茄炒蛋」，用户输「西红柿炒蛋」也算命中。

        这一步放在精确、别名与类别三条路都走完之后，是库内召回的最后一次尝试；
        口径与 `backend/tools/coverage_report.py` 的别名候选共用 `dish_text` 那张表。
        菜谱只有几十上百道，直接在进程内折一遍两边再比，比在 SQL 里做字符串函数更直观。
        """

        folded = dish_key(key)
        for recipe in self._session.scalars(select(Recipe).order_by(Recipe.id)):
            if dish_key(recipe.name) == folded:
                return recipe
        return None

    def _to_view(self, recipe: Recipe) -> RecipeView:
        return recipe_view(recipe, self._settings)

    # ------------------------------------------------------------------ 生成分支
    def _payload_to_view(self, payload: GeneratedRecipePayload) -> RecipeView:
        return RecipeView(
            id=None,
            name=payload.name,
            cook_time_minutes=payload.cook_time_minutes,
            difficulty=payload.difficulty,
            rating=None,  # 生成结果不给评分：模型编的分数没有意义
            servings=payload.servings,
            description=payload.description,
            required=[IngredientLine(item.name, item.amount) for item in payload.required],
            optional=[IngredientLine(item.name, item.amount) for item in payload.optional],
            steps=list(payload.steps),
            video_url=build_video_url(payload.name, self._settings),
            origin="generated",
        )

    def _load_cached(self, key: str) -> Optional[GeneratedRecipePayload]:
        row = self._session.get(
            GeneratedRecipe, cache_key(key, self._settings.prompt_version)
        )
        if row is None:
            return None
        try:
            return GeneratedRecipePayload.model_validate_json(row.payload_json)
        except ValidationError:
            logger.warning("生成缓存与当前模式不兼容，按未命中处理：%s", row.cache_key)
            return None

    def _cache_generated(self, key: str, payload: GeneratedRecipePayload) -> None:
        """生成结果进缓存：关闭落库或落库失败时，重复请求仍不必再调大模型。"""

        row = GeneratedRecipe(
            cache_key=cache_key(key, self._settings.prompt_version),
            dish=key,
            payload_json=payload.model_dump_json(),
            model=getattr(self._generator, "model", "unset"),
            prompt_version=self._settings.prompt_version,
        )
        self._session.merge(row)
        self._session.flush()

    # ------------------------------------------------------------------ 落库
    def _view_with_promotion(
        self, payload: GeneratedRecipePayload, index: Optional[int]
    ) -> RecipeView:
        """生成结果（新生成的或缓存里的）先尝试落库，成功就按库内菜谱返回。"""

        promoted = self._promote_to_library(payload, index)
        if promoted is None:
            return self._payload_to_view(payload)
        return self._to_view(promoted)

    def _ingredient_index(self) -> dict:
        """食材字典的「标准名 + 别名 → id」，用来把生成结果挂到食材表上。"""

        index: dict = {}
        for row in self._session.scalars(select(Ingredient)):
            index.setdefault(ingredient_key(row.name), row.id)
            for alias in (row.alias or "").split(","):
                if alias.strip():
                    index.setdefault(ingredient_key(alias), row.id)
        return index

    @staticmethod
    def _split_known_ingredients(
        lines: List[IngredientAmount], index: dict
    ) -> tuple:
        """按食材字典把主辅料拆成「挂得上食材表的」与「挂不上的」两拨。"""

        known: List[IngredientAmount] = []
        unknown: List[IngredientAmount] = []
        for line in lines:
            target = known if match_ingredient(line.name, index) is not None else unknown
            target.append(line)
        return known, unknown

    def _sanitize_generated(
        self, key: str, payload: GeneratedRecipePayload
    ) -> Optional[GeneratedRecipePayload]:
        """生成结果的当次闸门：答非所问、主料全挂不上食材表的一律判失败。

        返回 None 表示这次生成不可用，上层转成降级态。闸门与落库共用同一套规则，
        保证当次返回的菜谱就是入库后能查到的那一份。
        """

        if dish_key(payload.name) != fold_synonyms(key):
            logger.warning(
                "生成结果答非所问，按失败处理：请求「%s」，返回「%s」", key, payload.name
            )
            return None

        index = self._ingredient_index()
        required, unknown_required = self._split_known_ingredients(payload.required, index)
        optional, unknown_optional = self._split_known_ingredients(payload.optional, index)
        if payload.required and not required:
            logger.warning(
                "生成菜谱「%s」的主料没有一样在食材字典里，按失败处理", payload.name
            )
            return None

        unknown = [line.name for line in unknown_required + unknown_optional]
        if not unknown:
            return payload
        logger.warning(
            "生成菜谱「%s」有 %d 种食材不在字典里，已从结果中剔除"
            "（建议补进 backend/app/data/seed_ingredients.json）：%s",
            payload.name,
            len(unknown),
            "、".join(unknown),
        )
        return payload.model_copy(update={"required": required, "optional": optional})

    def _next_recipe_id(self) -> int:
        """自动入库的 id 从 promoted_recipe_id_base 起编，避开种子号段。"""

        base = self._settings.promoted_recipe_id_base
        current = self._session.scalar(select(func.max(Recipe.id)).where(Recipe.id >= base))
        return base if current is None else int(current) + 1

    def _link_class_index(self, index: Optional[int], recipe_id: int) -> None:
        """把新入库的菜谱挂到类别上，但只认领显式留白（recipe_id 为 NULL）的行。"""

        if index is None:
            return
        row = self._session.get(DishRecipeMap, index)
        if row is None or row.recipe_id is not None:
            return
        row.recipe_id = recipe_id
        self._session.flush()

    def _insert_promoted(
        self, payload: GeneratedRecipePayload, index: Optional[int]
    ) -> Optional[Recipe]:
        """按生成结果建一条菜谱主记录；返回 None 表示库里已有同名菜，不重复入库。"""

        # 走到这里 payload 的菜名已由闸门认证为「就是这次要查的那道菜」，而那道菜在
        # resolve 的第一步就查过库、确认不存在，所以重复入库只有并发才可能触发。
        if self._by_name(normalize_dish(payload.name)) is not None:  # pragma: no cover
            logger.info("菜谱库已有「%s」，不再重复入库", payload.name)
            return None

        recipe = Recipe(
            id=self._next_recipe_id(),
            name=payload.name,
            cook_time_minutes=payload.cook_time_minutes,
            difficulty=payload.difficulty,
            rating=None,  # 模型编的评分没有意义，入库也不给分
            servings=payload.servings,
            description=payload.description,
            origin="generated",
        )
        ingredients = self._ingredient_index()
        seen: set = set()
        for is_required, lines in ((True, payload.required), (False, payload.optional)):
            for line in lines:
                ingredient_id = match_ingredient(line.name, ingredients)
                if ingredient_id is None:  # pragma: no cover - 上游闸门已剔除
                    continue
                if ingredient_id in seen:  # 主辅料写了同一样东西时只留第一条
                    continue
                seen.add(ingredient_id)
                recipe.ingredients.append(
                    RecipeIngredient(
                        ingredient_id=ingredient_id,
                        is_required=is_required,
                        amount=line.amount,
                    )
                )
        for step_no, content in enumerate(payload.steps, start=1):
            # recipe_steps.content 是 VARCHAR(255)，超长步骤截断入库，避免严格后端写入失败
            recipe.steps.append(RecipeStep(step_no=step_no, content=content[:255]))
        self._session.add(recipe)
        self._session.flush()
        self._link_class_index(index, recipe.id)
        return recipe

    def _promote_to_library(
        self, payload: GeneratedRecipePayload, index: Optional[int]
    ) -> Optional[Recipe]:
        """把生成结果写进菜谱库，让下次同名请求直接命中库内，不再消耗额度。

        落库是锦上添花：失败一律只记日志并退回生成结果，不影响本次回答；关掉
        `promote_generated` 就完全不落库，演示时可以每次都走生成分支。
        """

        if not self._settings.promote_generated:
            return None
        try:
            with self._session.begin_nested():
                recipe = self._insert_promoted(payload, index)
            return recipe
        except Exception as exc:  # 唯一约束、外键缺失、只读库……一律降级为本次不落库
            logger.warning("生成结果落库失败，本次仍按生成结果回答：%s", exc)
            return None

    # ------------------------------------------------------------------ 入口
    def resolve(self, dish: str, index: Optional[int] = None) -> ResolvedRecipe:
        """解析菜谱：先查库，再生成，都没有则明确降级。"""

        key = normalize_dish(dish)
        if not key:
            RECIPE_RESOLVE.inc(source=SOURCE_UNAVAILABLE)
            return ResolvedRecipe(
                dish="",
                source=SOURCE_UNAVAILABLE,
                degraded=True,
                recipe=None,
                reason="菜名为空，无法检索菜谱",
            )

        recipe = self._by_name(key) or self._by_alias(key)
        if recipe is None and index is not None:
            recipe = self._by_class_index(index)
        if recipe is None:
            recipe = self._by_synonym(key)
        if recipe is not None:
            RECIPE_RESOLVE.inc(source=SOURCE_LIBRARY)
            return ResolvedRecipe(
                dish=key, source=SOURCE_LIBRARY, degraded=False, recipe=self._to_view(recipe)
            )

        if is_placeholder(key):
            RECIPE_RESOLVE.inc(source=SOURCE_UNAVAILABLE)
            return ResolvedRecipe(
                dish=key,
                source=SOURCE_UNAVAILABLE,
                degraded=True,
                recipe=None,
                reason=PLACEHOLDER_REASON,
            )

        cached = self._load_cached(key)
        if cached is not None:
            # 缓存也要过同一道闸：落库能力上线前的旧缓存可能是答非所问、或挂不上
            # 食材字典的结果，宁可当未命中重新生成，也别让同一道菜出现两个口径。
            cached = self._sanitize_generated(key, cached)
        if cached is not None:
            # 缓存里可能存着「落库能力上线前」生成的结果，也要补一次落库；
            # 否则同一道菜能不能进库，全看它以前有没有被缓存过，行为不可预期。
            RECIPE_CACHE.inc(result="hit")
            RECIPE_RESOLVE.inc(source=SOURCE_GENERATED)
            return ResolvedRecipe(
                dish=key,
                source=SOURCE_GENERATED,
                degraded=False,
                recipe=self._view_with_promotion(cached, index),
            )

        # 缓存没命中才轮到调大模型：这个计数直接反映省下了多少次调用
        RECIPE_CACHE.inc(result="miss")
        payload = self._generator.generate(key) if self._generator is not None else None
        if payload is not None:
            payload = self._sanitize_generated(key, payload)
        if payload is None:
            RECIPE_RESOLVE.inc(source=SOURCE_UNAVAILABLE)
            return ResolvedRecipe(
                dish=key,
                source=SOURCE_UNAVAILABLE,
                degraded=True,
                recipe=None,
                # 没有凭据是「用户填一把就能解决」，别说成服务坏了
                reason=(
                    NO_CREDENTIALS_REASON
                    if self._generator is None or not getattr(self._generator, "configured", False)
                    else UNAVAILABLE_REASON
                ),
            )
        self._cache_generated(key, payload)
        RECIPE_RESOLVE.inc(source=SOURCE_GENERATED)
        return ResolvedRecipe(
            dish=key,
            source=SOURCE_GENERATED,
            degraded=False,
            recipe=self._view_with_promotion(payload, index),
        )
