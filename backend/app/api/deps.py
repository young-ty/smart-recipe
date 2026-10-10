"""FastAPI 依赖：配置、识别器、生成层与当前账号的装配。"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.crypto import SecretBox, load_or_create_master_key
from ..core.database import get_db
from ..core.errors import bad_request, unauthorized
from ..models import User
from ..services import accounts
from ..services.generator import LlmCredentials, RecipeGenerator, build_generator
from ..services.recognizer import FallbackDishRecognizer, build_recognizer

# 用户自带模型凭据的请求头。Key 不走 URL 也不进请求体：落进 URL 会被访问日志记下来，
# 落进请求体则会被 pydantic 模型固化进 OpenAPI 文档。它按请求来去、不在进程里留副本；
# 登录用户也可以把 Key 存在账号上（加密落库，见 accounts.store_llm_key），
# 这条路径排在请求头之后。
REQUEST_LLM_KEY_HEADER = "X-LLM-Api-Key"
REQUEST_LLM_BASE_URL_HEADER = "X-LLM-Base-Url"
REQUEST_LLM_MODEL_HEADER = "X-LLM-Model"
MAX_API_KEY_LENGTH = 200
MAX_BASE_URL_LENGTH = accounts.MAX_BASE_URL_LENGTH
MAX_MODEL_LENGTH = accounts.MAX_MODEL_LENGTH


def llm_credentials_from_request(request: Request) -> Optional[LlmCredentials]:
    """从请求头取用户自带的模型凭据；没有就返回 None（再去看账号里存没存）。"""

    api_key = (request.headers.get(REQUEST_LLM_KEY_HEADER) or "").strip()
    if not api_key:
        return None
    if len(api_key) > MAX_API_KEY_LENGTH:
        raise bad_request("模型 Key 过长")
    base_url = accounts.check_base_url(request.headers.get(REQUEST_LLM_BASE_URL_HEADER))
    model = accounts.normalize_model(request.headers.get(REQUEST_LLM_MODEL_HEADER))
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
    """没有调用方凭据时用的生成层：服务端不持有任何 Key，所以它总是「不生成」实现。"""

    return build_generator(settings)


@lru_cache(maxsize=4)
def _secret_box_for(settings: Settings) -> SecretBox:
    """账号级模型 Key 的加解密器；主密钥只在首次取用时加载一次。"""

    return SecretBox(load_or_create_master_key(settings.secret_key_file))


def get_secret_box(settings: Settings = Depends(get_app_settings)) -> SecretBox:
    return _secret_box_for(settings)


def get_recognizer(settings: Settings = Depends(get_app_settings)) -> FallbackDishRecognizer:
    return _recognizer_for(settings)


def get_generator(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    user: Optional[User] = Depends(optional_user),
    secret_box: SecretBox = Depends(get_secret_box),
) -> RecipeGenerator:
    """生成层：只认调用方自己带来的凭据。

    请求头优先于账号里存的（临时用另一把 Key 试一下时不必先改账号），两者都没有就
    返回「不生成」实现——服务端不预置 Key，也就不会替谁掏这份额度（见 7.8）。
    """

    credentials = llm_credentials_from_request(request)
    if credentials is None:
        credentials = accounts.llm_credentials(user, secret_box)
    if credentials is None:
        return _generator_for(settings)
    return build_generator(
        settings,
        api_key=credentials.api_key,
        base_url=credentials.base_url,
        model=credentials.model,
    )
