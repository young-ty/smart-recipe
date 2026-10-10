"""模型凭据探活的响应模型（设计方案 7.8）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LlmProbeResponse(BaseModel):
    """一次最小调用的结果。

    `ok` 只回答「这套凭据现在能不能用」；`detail` 负责说清不行的话是哪里不行，
    前端直接把它显示在 Key 卡片上，不再翻译一遍。
    """

    ok: bool = Field(description="凭据是否可用")
    configured: bool = Field(description="是否拿到了 Key（服务端配置或请求头）")
    model: str = Field(description="本次探活使用的模型名")
    detail: str = Field(description="给人看的一句话说明")
