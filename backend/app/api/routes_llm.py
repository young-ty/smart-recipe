"""模型凭据探活（设计方案 7.8）。

用户自带 Key 时，前端只有一个「保存」按钮，保存成功不代表 Key 能用：错的 Key 要等到
某道库外菜生成失败才暴露，而那时界面只说「生成服务不可用」，用户分不清是 Key、地址
还是模型名的问题。这个接口用一次 max_tokens=1 的最小调用把原因说清楚。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..schemas import LlmProbeResponse
from ..services.generator import RecipeGenerator
from .deps import get_generator

router = APIRouter(prefix="/api", tags=["llm"])


@router.post("/llm/probe", response_model=LlmProbeResponse)
def probe_llm(generator: RecipeGenerator = Depends(get_generator)) -> LlmProbeResponse:
    """探活用的就是本次请求的凭据：请求头带了用户 Key 就探它，否则探服务端配置。"""

    outcome = generator.probe()
    return LlmProbeResponse(
        ok=outcome.ok,
        configured=bool(generator.configured),
        model=str(getattr(generator, "model", "unset")),
        detail=outcome.detail,
    )
