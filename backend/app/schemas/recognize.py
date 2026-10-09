"""识别接口的请求/响应模型（设计方案 5.3.1、7.2）。"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class DishCandidateOut(BaseModel):
    index: int = Field(ge=0, description="类别下标，取值 0 ~ class_count-1")
    name: str = Field(description="菜名；类别清单未就位时为「类别 #N」")
    confidence: float = Field(ge=0.0, le=1.0, description="softmax 概率")
    provisional_name: bool = Field(description="名称是否为临时占位")


class RecognizeResponse(BaseModel):
    recognition_id: str
    model_version: str
    class_count: int
    labels_available: bool
    degraded: bool
    elapsed_ms: int
    candidates: List[DishCandidateOut]