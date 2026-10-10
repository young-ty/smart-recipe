"""FastAPI 依赖：配置、识别器、生成层与当前账号的装配。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.database import get_db
from ..core.errors import bad_request, unauthorized
from ..models import User
from ..services import accounts
from ..services.generator import RecipeGenerator, build_generator
from ..services.recognizer import FallbackDishRecognizer, build_recognizer

# 用户自带模型凭据的请求头。Key 只走请求头，不进 URL、不进请求体、不落库：
# 落进 URL 会被访问日志记下来，落进请求体则会被 pydantic 模型固化进 OpenAPI 文档。
REQUEST_LLM_KEY_HEADER = "X-LLM-Api-Key"
REQUEST_LLM_BASE_URL_HEADER = "X-LLM-Base-Url"
REQUEST_LLM_MODEL_HEADER = "X-LLM-Model"
MAX_API_KEY_LENGTH = 200
MAX_BASE_URL_LENGTH = 200
MAX_MODEL_LENGTH = 64
# 本机地址是显式例外：本地起一个兼容 OpenAI 协议的网关（llama.cpp / vLLM）时用得上
LOCAL_BASE_URL_PREFIXES = ("http://localhost", "http://127.0.0.1", "http://[::1]")


@dataclass(frozen=True)
class LlmCredentials:
    """用户在设置里填的模型凭据，只在这一次请求里活着。"""

    api_key: str
    base_url: Optional[str] = None
    model: Optional[str] = None


def _check_base_url(base_url: str) -> None:
    """只接受 https，或本机 http：明文把 Key 发到外网是明确要挡掉的情况。"""

    if base_url.startswith("https://"):
        return
    if any(base_url.startswith(prefix) for prefix in LOCAL_BASE_URL_PREFIXES):
        return
    raise bad_request("模型地址只接受 https:// 或本机的 http://localhost / http://127.0.0.1")


def llm_credentials_from_request(request: Request) -> Optional[LlmCredentials]:
    """从请求头取用户自带的模型凭据；没有就返回 None（回落服务端配置）。"""

    api_key = (request.headers.get(REQUEST_LLM_KEY_HEADER) or "").strip()
    if not api_key:
        return None
    if len(api_key) > MAX_API_KEY_LENGTH:
        raise bad_request("模型 Key 过长")
    base_url = (request.headers.get(REQUEST_LLM_BASE_URL_HEADER) or "").strip().rstrip("/") or None
    model = (request.headers.get(REQUEST_LLM_MODEL_HEADER) or "").strip() or None
    if base_url is not None:
        if len(base_url) > MAX_BASE_URL_LENGTH:
            raise bad_request("模型地址过长")
        _check_base_url(base_url)
    if model is not None and len(model) > MAX_MODEL_LENGTH:
        raise bad_request("模型名过长")
    return LlmCredentials(api_key=api_key, base_url=base_url, model=model)


def get_app_settings(request: Request) -> Settings:
    """取当前应用实例的配置，测试可通过 create_app(Settings(...)) 注入。"""

    return request.app.state.settings


def optional_user(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> Optional[User]:
    """当前登录账号；没登录返回 None（不抛异常，页面按匿名态继续用）。"""

    return accounts.user_for_token(session, request.cookies.get(settings.session_cookie_name))


def require_user(user: Optional[User] = Depends(optional_user)) -> User:
    """需要登录的接口用它；未登录统一 401 / 40101。"""

    if user is None:
        raise unauthorized("请先登录")
    return user


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


def get_generator(
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> RecipeGenerator:
    """生成层：请求头带了用户自己的 Key 就现造一个，否则用配置缓存的那个。"""

    credentials = llm_credentials_from_request(request)
    if credentials is None:
        return _generator_for(settings)
    return build_generator(
        settings,
        api_key=credentials.api_key,
        base_url=credentials.base_url,
        model=credentials.model,
    )
