"""FastAPI 依赖：配置、识别器与生成层装配。"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request

from ..core.config import Settings
from ..services.generator import RecipeGenerator, build_generator
from ..services.recognizer import FallbackDishRecognizer, build_recognizer


def get_app_settings(request: Request) -> Settings:
    """取当前应用实例的配置，测试可通过 create_app(Settings(...)) 注入。"""

    return request.app.state.settings


@lru_cache(maxsize=4)
def _recognizer_for(settings: Settings) -> FallbackDishRecognizer:
    """按配置缓存识别器：模型只在首次使用时加载一次（设计方案 7.3）。"""

    return build_recognizer(settings)


@lru_cache(maxsize=4)
def _generator_for(settings: Settings) -> RecipeGenerator:
    """生成层同样按配置缓存；未配置 Key 时是「不生成」实现，不抛异常。"""

    return build_generator(settings)


def get_recognizer(settings: Settings = Depends(get_app_settings)) -> FallbackDishRecognizer:
    return _recognizer_for(settings)


def get_generator(settings: Settings = Depends(get_app_settings)) -> RecipeGenerator:
    return _generator_for(settings)