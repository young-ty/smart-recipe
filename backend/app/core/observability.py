# -*- coding: utf-8 -*-
"""请求级上下文与结构化日志（设计方案 5.5）。

一次识别请求要经过中间件、事件循环、线程池三层。出问题时只看到一句「识别失败」
是查不下去的，所以这里做两件事：

1. 给每个请求打一个 request id。进来的 X-Request-ID 直接沿用（前端或网关可以
   自己串链路），没有就生成一个；写回响应头，同时放进 ContextVar，让它自动跟到
   线程池和日志里，不用一路当参数传。
2. 日志带上 request id。默认是「文本 + [req=...]」，把 RECIPE_LOG_FORMAT 设成
   json 就换成一行一条的 JSON，交给日志系统直接收。

中间件用纯 ASGI 写法而不是 BaseHTTPMiddleware：后者会给响应包一层任务组，
流式响应与后台任务的时序会变，而我们的推理正好跑在线程池里。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional

from starlette.datastructures import Headers, MutableHeaders

from .metrics import HTTP_DURATION, HTTP_REQUESTS

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
# 外部传进来的 id 最长认这么多字符，超了宁愿自己生成一个
MAX_REQUEST_ID_LENGTH = 64

_request_id: ContextVar[str] = ContextVar("request_id", default="")

# 我们挂上去的处理器打个标记，configure_logging 重复调用时认它，不会越挂越多
_HANDLER_FLAG = "_recipe_handler"
# uvicorn 自己配了日志（且 propagate=False），要顺带把格式换成同一套
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
# 常见噪声源：这些库在 INFO 上打逐请求、逐连接的日志，跟着根日志一起放开会把业务
# 日志淹掉（实测 httpx 会把压测里的每一次请求都打一行），业务日志在前、它们在后
QUIET_LOGGERS = ("httpx", "httpcore", "urllib3", "multipart")


def current_request_id() -> str:
    """取当前请求的 id；不在请求上下文里（离线脚本、后台任务）返回空串。"""

    return _request_id.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


@contextlib.contextmanager
def request_id_context(value: str) -> Iterator[str]:
    """手工把 request id 绑到当前上下文。

    请求结束之后接着跑的任务（后台补库、离线批处理）想让日志挂在同一条链路上，
    用这个包一层即可；中间件内部走的也是同一套机制。
    """

    token = _request_id.set(value)
    try:
        yield value
    finally:
        _request_id.reset(token)


def sanitize_request_id(raw: Optional[str]) -> str:
    """收拾外部传进来的 id：太长、带控制字符的一律丢弃。

    不然前端传一个带换行的值，日志里就能凭空多出一行，看上去像另一个请求发的。
    """

    if not raw:
        return ""
    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_REQUEST_ID_LENGTH:
        return ""
    if any(character < " " or character == "\x7f" for character in candidate):
        return ""
    return candidate


class RequestIdFilter(logging.Filter):
    """把当前请求的 id 落到日志记录上。

    格式化器自己也会兜底读一次 ContextVar，这个过滤器的作用是让记录本身带上这个
    字段——别的处理器（日志采集、告警）拿到记录时同样读得到。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", ""):
            record.request_id = current_request_id() or "-"
        return True


class _RecordRequestId:
    """格式化之前确保记录上有 request_id 字段。"""

    def _ensure_request_id(self, record: logging.LogRecord) -> None:
        if not getattr(record, "request_id", ""):
            record.request_id = current_request_id() or "-"

    def format(self, record: logging.LogRecord) -> str:
        self._ensure_request_id(record)
        return super().format(record)  # type: ignore[misc]


class TextFormatter(_RecordRequestId, logging.Formatter):
    """人类可读格式：行首带 request id，方便 grep 出一条链路的全部日志。"""

    DEFAULT_FORMAT = (
        "%(asctime)s %(levelname)s [req=%(request_id)s] %(name)s: %(message)s"
    )

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None) -> None:
        super().__init__(fmt or self.DEFAULT_FORMAT, datefmt or "%Y-%m-%d %H:%M:%S")


class JsonFormatter(_RecordRequestId, logging.Formatter):
    """一行一条的 JSON：字段固定，异常栈单独放一个字段，便于采集端解析。"""

    def __init__(self, datefmt: Optional[str] = None) -> None:
        super().__init__(datefmt=datefmt or "%Y-%m-%dT%H:%M:%S%z")

    def format(self, record: logging.LogRecord) -> str:
        # JsonFormatter 自己实现了 format，不会走上面那个混入的版本
        self._ensure_request_id(record)
        payload: Dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _attach(logger_obj: logging.Logger) -> logging.Handler:
    """给一个 logger 装上带 request id 的处理器；已经有了就复用。"""

    for handler in logger_obj.handlers:
        if getattr(handler, _HANDLER_FLAG, False):
            return handler
    handler = logging.StreamHandler()
    ensure_utf8(getattr(handler, "stream", None))
    setattr(handler, _HANDLER_FLAG, True)
    logger_obj.addHandler(handler)
    return handler


def ensure_utf8(stream) -> None:
    """把输出流切到 UTF-8。

    Windows 上标准错误一旦被重定向到文件或管道，默认编码就变成 ANSI 代码页（中文
    环境是 cp936）。日志里的中文写进文件后，采集端按 UTF-8 读就是一串乱码，
    JSON 日志尤其不能这样。控制台流本来就是 UTF-8，切了等于没切，不会把中文字符
    显示坏；切不动的流（已经被包装过的）按原样用。命令行工具往管道里写中文也用
    同一个办法，所以这里是公开的。
    """

    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        return


def configure_logging(
    level: Optional[str] = None, log_format: Optional[str] = None
) -> logging.Handler:
    """装配根日志（以及 uvicorn 的日志）：带 request id，格式可切换。

    幂等：重复调用只更新格式，不会把同一行日志打印好几遍。create_app() 会调它，
    测试里每个用例都会建一次应用，所以幂等是硬要求。
    """

    level = level or os.getenv("RECIPE_LOG_LEVEL", "INFO")
    log_format = (log_format or os.getenv("RECIPE_LOG_FORMAT", "text")).strip().lower()
    formatter: logging.Formatter = (
        JsonFormatter() if log_format == "json" else TextFormatter()
    )

    root = logging.getLogger()
    root.setLevel(level)
    handler = _attach(root)
    handler.setFormatter(formatter)
    if not any(isinstance(item, RequestIdFilter) for item in handler.filters):
        handler.addFilter(RequestIdFilter())

    # uvicorn 的日志不往根上冒泡，单独换成同一套格式，否则一份日志两种长相
    for name in UVICORN_LOGGERS:
        target = logging.getLogger(name)
        if not target.handlers:
            continue
        target.setLevel(level)
        for existing in target.handlers:
            existing.setFormatter(formatter)
            ensure_utf8(getattr(existing, "stream", None))
            if not any(isinstance(item, RequestIdFilter) for item in existing.filters):
                existing.addFilter(RequestIdFilter())

    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler


def record_http_request(scope: Dict[str, Any], status_code: int, elapsed: float) -> None:
    """把一次请求记进 HTTP 通用指标。

    path 用路由模板（/api/recipes/{recipe_id}）而不是真实 URL：真实 URL 每来一个
    菜谱 id 就长出一条新时间序列。没匹配上路由的（404）统一记成 unmatched。
    /metrics 自己不计：否则采集一次就把延迟分布和 QPS 污染一次。
    OPTIONS 也不计：前端跨域时每个 POST 前面都跟一次预检，算进去会让请求量翻倍、
    p50 腰斩，读指标的人会以为服务比实际快。
    """

    method = scope.get("method", "GET")
    route = scope.get("route")
    path = getattr(route, "path", None) or "unmatched"
    if path == "/metrics" or method == "OPTIONS":
        return
    HTTP_REQUESTS.inc(method=method, path=path, status=str(status_code))
    HTTP_DURATION.observe(elapsed, method=method, path=path)


class RequestContextMiddleware:
    """纯 ASGI 中间件：注入 request id，顺带采集 HTTP 通用指标。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = sanitize_request_id(headers.get(REQUEST_ID_HEADER)) or new_request_id()
        # Request.state 读的就是 scope["state"]，路由里按需自取
        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id.set(request_id)
        started = time.perf_counter()
        status_code = {"value": 500}

        async def send_with_request_id(message) -> None:
            if message["type"] == "http.response.start":
                status_code["value"] = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            record_http_request(scope, status_code["value"], time.perf_counter() - started)
            _request_id.reset(token)
