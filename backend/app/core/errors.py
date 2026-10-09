"""统一错误响应与错误码（设计方案 5.4）。"""

from __future__ import annotations

from typing import Any, Optional

# 错误码 -> 默认中文提示
ERROR_MESSAGES = {
    40001: "请求参数校验失败",
    40401: "资源不存在",
    41301: "图片超过 5 MB，请压缩后重试",
    41501: "不支持的图片格式，请上传 jpg / png / webp",
    50001: "服务内部错误，请稍后重试",
}


class ApiError(Exception):
    """业务异常：由路由层抛出，统一转换为 5.4 节错误体。"""

    def __init__(
        self,
        status_code: int,
        code: int,
        message: Optional[str] = None,
        detail: Any = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "未知错误")
        self.detail = detail
        super().__init__(self.message)

    def body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}


def payload_too_large(limit_bytes: int) -> ApiError:
    return ApiError(413, 41301, f"图片超过 {limit_bytes // (1024 * 1024)} MB，请压缩后重试")


def unsupported_media_type(mime: Optional[str]) -> ApiError:
    return ApiError(415, 41501, None, {"received": mime})


def bad_request(message: Optional[str] = None, detail: Any = None) -> ApiError:
    return ApiError(400, 40001, message, detail)


def not_found(message: str = "资源不存在", detail: Any = None) -> ApiError:
    return ApiError(404, 40401, message, detail)