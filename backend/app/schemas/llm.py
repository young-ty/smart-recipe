"""模型凭据与凭据探活的请求/响应模型（设计方案 5.6.5、7.8）。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

MAX_API_KEY_LENGTH = 200
MAX_BASE_URL_LENGTH = 200
MAX_MODEL_LENGTH = 64


class LlmKeyRequest(BaseModel):
    """存进账号的模型凭据。服务端加密后才落库，明文只在调用生成层时出现在内存里。"""

    api_key: str = Field(min_length=1, max_length=MAX_API_KEY_LENGTH, description="模型 Key")
    base_url: Optional[str] = Field(
        default=None, max_length=MAX_BASE_URL_LENGTH, description="兼容 OpenAI 协议的服务地址"
    )
    model: Optional[str] = Field(default=None, max_length=MAX_MODEL_LENGTH, description="模型名")


class LlmKeyResponse(BaseModel):
    """账号里那份 Key 的现状：只回掩码，明文永不出服务端。"""

    configured: bool = Field(description="账号里是否存了 Key")
    masked: str = Field(default="", description="掩码后的 Key，没存过就是空串")
    base_url: Optional[str] = None
    model: Optional[str] = None
    updated_at: Optional[datetime] = None


class LlmProbeResponse(BaseModel):
    """一次最小调用的结果。

    `ok` 只回答「这套凭据现在能不能用」；`detail` 负责说清不行的话是哪里不行，
    前端直接把它显示在 Key 卡片上，不再翻译一遍。
    """

    ok: bool = Field(description="凭据是否可用")
    configured: bool = Field(description="是否拿到了 Key（服务端配置或请求头）")
    model: str = Field(description="本次探活使用的模型名")
    detail: str = Field(description="给人看的一句话说明")
