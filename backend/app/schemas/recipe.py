"""菜谱、类别清单与健康检查的请求/响应模型（设计方案 5.3.2 ~ 5.3.4）。"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

RecipeSource = Literal["library", "generated", "unavailable"]


class RecipeIngredientOut(BaseModel):
    name: str
    amount: Optional[str] = None


class RecipeOut(BaseModel):
    """库内与生成两条分支共用的菜谱形状。"""

    id: Optional[int] = Field(default=None, description="库内菜谱 id；生成结果未入库时为 null")
    origin: Literal["seed", "generated"] = Field(
        default="seed",
        description="seed = 人工整理；generated = 大模型生成（含生成后自动入库）",
    )
    name: str
    cook_time_minutes: int
    difficulty: str
    rating: Optional[float] = Field(default=None, description="生成结果不给评分")
    servings: int
    description: Optional[str] = None
    required: List[RecipeIngredientOut]
    optional: List[RecipeIngredientOut]
    steps: List[str]
    video_url: str = Field(description="由后端按菜名拼接，不采信模型直出的链接")


class ResolveRequest(BaseModel):
    dish: str = Field(min_length=1, max_length=64, description="菜名")
    index: Optional[int] = Field(default=None, ge=0, description="类别下标，用于日志与承接查询")


class ResolveResponse(BaseModel):
    dish: str
    source: RecipeSource
    degraded: bool
    recipe: Optional[RecipeOut] = None
    reason: Optional[str] = Field(default=None, description="不可用时的原因说明")


class RecipeDetail(BaseModel):
    recipe_id: int
    name: str
    origin: Literal["seed", "generated"] = Field(
        default="seed",
        description="seed = 人工整理；generated = 大模型生成后自动入库",
    )
    cook_time_minutes: int
    difficulty: str
    rating: Optional[float] = Field(default=None, description="生成后入库的菜谱没有评分")
    servings: int
    description: Optional[str] = None
    image_url: Optional[str] = None
    required: List[RecipeIngredientOut]
    optional: List[RecipeIngredientOut]
    steps: List[str]
    video_url: str


class DishItem(BaseModel):
    index: int
    name: str
    provisional_name: bool
    library_recipe_id: int


class DishListResponse(BaseModel):
    class_count: int
    labels_available: bool
    library_recipe_count: int
    covered_class_count: int
    items: List[DishItem]


class ModelInfo(BaseModel):
    loaded: bool
    version: str
    degraded: bool
    class_count: int
    labels_available: bool
    # 真实推理的执行提供程序（例如 ["CUDAExecutionProvider", "CPUExecutionProvider"]）。
    # 装了 GPU 版运行时也可能因为缺 cuDNN 静默回落到 CPU，这个字段就是让它可见；
    # 降级到模拟实现时为空列表。
    providers: List[str] = Field(default_factory=list)


class GeneratorInfo(BaseModel):
    """生成层现状。

    `configured` 说的是**这次请求**有没有拿到可用凭据（请求头或账号里存的），
    不是服务端配没配 Key——服务端不持有 Key，所以健康检查里它总是 false。
    """

    configured: bool
    model: str


class HealthResponse(BaseModel):
    status: str
    database: str
    recipe_count: int
    model: ModelInfo
    generator: GeneratorInfo
