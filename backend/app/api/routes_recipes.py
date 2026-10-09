"""菜谱解析、菜谱详情、类别清单与健康检查（设计方案 5.3.2 ~ 5.3.4）。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.database import get_db
from ..core.errors import not_found
from ..models import DishRecipeMap, Recipe
from ..schemas import (
    DishItem,
    DishListResponse,
    GeneratorInfo,
    HealthResponse,
    ModelInfo,
    RecipeDetail,
    RecipeIngredientOut,
    RecipeOut,
    ResolveRequest,
    ResolveResponse,
)
from ..services.generator import RecipeGenerator
from ..services.recipe_resolver import RecipeResolver, RecipeView, recipe_view
from ..services.recognizer import FallbackDishRecognizer
from .deps import get_app_settings, get_generator, get_recognizer

router = APIRouter(prefix="/api", tags=["recipes"])


def _ingredients(lines) -> List[RecipeIngredientOut]:
    return [RecipeIngredientOut(name=line.name, amount=line.amount) for line in lines]


def _to_recipe_out(view: Optional[RecipeView]) -> Optional[RecipeOut]:
    if view is None:
        return None
    return RecipeOut(
        id=view.id,
        origin=view.origin,
        name=view.name,
        cook_time_minutes=view.cook_time_minutes,
        difficulty=view.difficulty,
        rating=view.rating,
        servings=view.servings,
        description=view.description,
        required=_ingredients(view.required),
        optional=_ingredients(view.optional),
        steps=list(view.steps),
        video_url=view.video_url,
    )


@router.post("/recipes/resolve", response_model=ResolveResponse)
def resolve_recipe(
    payload: ResolveRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    generator: RecipeGenerator = Depends(get_generator),
) -> ResolveResponse:
    """按菜名取菜谱：先查库，再生成，都没有则明确降级。"""

    resolved = RecipeResolver(session, settings, generator).resolve(payload.dish, payload.index)
    return ResolveResponse(
        dish=resolved.dish,
        source=resolved.source,
        degraded=resolved.degraded,
        recipe=_to_recipe_out(resolved.recipe),
        reason=resolved.reason,
    )


@router.get("/recipes/{recipe_id}", response_model=RecipeDetail)
def get_recipe(
    recipe_id: int,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> RecipeDetail:
    recipe: Optional[Recipe] = session.get(Recipe, recipe_id)
    if recipe is None:
        raise not_found("菜谱不存在", {"recipe_id": recipe_id})
    view = recipe_view(recipe, settings)
    return RecipeDetail(
        recipe_id=recipe.id,
        name=view.name,
        origin=view.origin,
        cook_time_minutes=view.cook_time_minutes,
        difficulty=view.difficulty,
        rating=view.rating,
        servings=view.servings,
        description=view.description,
        image_url=recipe.image_url,
        required=_ingredients(view.required),
        optional=_ingredients(view.optional),
        steps=list(view.steps),
        video_url=view.video_url,
    )


@router.get("/dishes", response_model=DishListResponse)
def list_dishes(
    session: Session = Depends(get_db),
    recognizer: FallbackDishRecognizer = Depends(get_recognizer),
) -> DishListResponse:
    """类别清单与菜谱库覆盖情况：页面脚注、手动纠正提示与评测核对都用它。"""

    recipe_count = session.scalar(select(func.count()).select_from(Recipe)) or 0
    rows = session.scalars(
        select(DishRecipeMap)
        .where(DishRecipeMap.recipe_id.is_not(None))
        .order_by(DishRecipeMap.class_index)
    ).all()
    items = []
    for row in rows:
        name, provisional = recognizer.labels.resolve(row.class_index)
        items.append(
            DishItem(
                index=row.class_index,
                name=name,
                provisional_name=provisional,
                library_recipe_id=row.recipe_id,
            )
        )
    return DishListResponse(
        class_count=recognizer.class_count,
        labels_available=recognizer.labels_available,
        library_recipe_count=recipe_count,
        covered_class_count=len(items),
        items=items,
    )


@router.get("/health", response_model=HealthResponse)
def health(
    session: Session = Depends(get_db),
    recognizer: FallbackDishRecognizer = Depends(get_recognizer),
    generator: RecipeGenerator = Depends(get_generator),
) -> HealthResponse:
    database = "ok"
    recipe_count = 0
    try:
        session.execute(select(1))
        recipe_count = session.scalar(select(func.count()).select_from(Recipe)) or 0
    except Exception:
        database = "error"
    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        database=database,
        recipe_count=recipe_count,
        model=ModelInfo(
            loaded=recognizer.primary is not None,
            version=recognizer.model_version,
            degraded=recognizer.degraded,
            class_count=recognizer.class_count,
            labels_available=recognizer.labels_available,
            providers=list(recognizer.providers),
        ),
        generator=GeneratorInfo(
            configured=bool(generator.configured),
            model=str(getattr(generator, "model", "unset")),
        ),
    )
