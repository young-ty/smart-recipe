"""FastAPI 应用入口（设计方案 5.1）。

启动时完成：迁移 schema → 建表 → 幂等灌种子数据 → 装配识别器与生成层
（模型缺失降级为模拟识别，未配置大模型 Key 时生成分支返回 unavailable）。
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import routes_ops, routes_recipes, routes_recognize
from .api.deps import get_recognizer
from .core.config import Settings, get_settings
from .core.concurrency import InferenceGate
from .core.database import create_all, get_engine, init_engine, session_scope
from .core.errors import ApiError
from .core.observability import RequestContextMiddleware, configure_logging
from .services.seed import seed_database

logger = logging.getLogger(__name__)

# 前端开发服务器默认地址（Vite）
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": 40001,
                    "message": "请求参数校验失败",
                    "detail": exc.errors(),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = 40401 if exc.status_code == 404 else 50001
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail), "detail": None}},
        )

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": 50001, "message": "服务内部错误，请稍后重试", "detail": None}},
        )


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    # 日志与 request id 在应用建起来时就装配好：uvicorn 那时已经把日志配完了，
    # 这里只把格式统一过去，不会重复输出
    configure_logging()

    app = FastAPI(
        title=settings.app_name,
        version=settings.api_version,
        description="上传成品菜照片 → 识别菜品 → 返回库内菜谱或由大模型生成做法",
    )
    app.state.settings = settings
    # 识别闸门：限制同时在跑的推理数，超出的请求排队而不是一起抢 CPU
    app.state.recognize_gate = InferenceGate(settings.recognize_max_concurrency)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(DEFAULT_CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 后加的中间件在最外层：request id 要能盖住 CORS 的预检与错误响应
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(routes_recognize.router)
    app.include_router(routes_recipes.router)
    app.include_router(routes_ops.router)

    init_engine(settings)
    create_all(get_engine())
    with session_scope() as session:
        summary = seed_database(session, settings)
    logger.info("种子数据：%s", summary)
    warm_up(app)
    return app


def warm_up(app: FastAPI) -> None:
    """启动时把识别器连同 ONNX 会话一起加载好。

    识别器按配置缓存，第一次用到才建。不预热的话这份开销落在第一个用户请求上，
    而且发生在依赖解析阶段——接口返回的 elapsed_ms 里看不到，看日志只会觉得
    「第一个请求怎么这么慢」。预热顺带把执行提供程序的事实打出来：装了 GPU 版
    运行时也可能因为缺 cuDNN 静默回落到 CPU，这件事越早看见越好。
    """

    settings = app.state.settings
    if not settings.warm_up_on_start:
        logger.info("按 RECIPE_WARMUP=0 跳过模型预热")
        return
    started = time.perf_counter()
    recognizer = get_recognizer(settings)
    logger.info(
        "识别器就绪：%s，%d 类，预热耗时 %d ms%s",
        recognizer.model_version,
        recognizer.class_count,
        int(round((time.perf_counter() - started) * 1000)),
        "" if recognizer.providers else "（未加载权重，当前为模拟识别）",
    )


app = create_app()
