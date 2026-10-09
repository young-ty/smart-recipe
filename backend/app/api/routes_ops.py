# -*- coding: utf-8 -*-
"""运维面接口（设计方案 5.5）：指标暴露。

业务接口都在 /api 下面，这里单独一个分组，部署时可以按路径决定要不要对外开放
（指标里带着流量结构和耗时分布，生产上一般只给内网或采集端）。

不写进 OpenAPI 文档：它是给采集端抓的，不是给前端调的。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ..core.metrics import REGISTRY

router = APIRouter(tags=["ops"])

# Prometheus 文本暴露格式，版本号写在 Content-Type 里
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics() -> PlainTextResponse:
    """当前进程的指标快照。每次抓取都是即时渲染，不带缓存。"""

    return PlainTextResponse(REGISTRY.render(), media_type=PROMETHEUS_CONTENT_TYPE)
