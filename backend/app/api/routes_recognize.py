"""识别接口（设计方案 5.3.1）。"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.database import get_db
from ..core.errors import ApiError, bad_request, payload_too_large, unsupported_media_type
from ..core.metrics import (
    RECOGNIZE_IN_FLIGHT,
    RECOGNIZE_INFERENCE,
    RECOGNIZE_QUEUE,
    RECOGNIZE_REQUESTS,
)
from ..models import RecognitionLog
from ..schemas import DishCandidateOut, RecognizeResponse
from ..services.recognizer import FallbackDishRecognizer
from starlette.concurrency import run_in_threadpool
from .deps import get_app_settings, get_recognizer


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recognize"])

# 图片文件的魔数前缀：防止仅靠 Content-Type 冒充（浏览器可伪造该头）
_MAGIC_PREFIXES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def sniff_image_mime(payload: bytes) -> str:
    """按文件头判断真实类型，未识别返回空串。"""

    for prefix, mime in _MAGIC_PREFIXES:
        if payload.startswith(prefix):
            return mime
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return ""


@router.post("/recognize", response_model=RecognizeResponse)
async def recognize_dish(
    request: Request,
    file: UploadFile = File(..., description="成品菜照片"),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    recognizer: FallbackDishRecognizer = Depends(get_recognizer),
) -> RecognizeResponse:
    declared = (file.content_type or "").split(";")[0].strip().lower()
    if declared not in settings.allowed_mime_types:
        raise unsupported_media_type(declared)

    payload = await file.read()
    if not payload:
        raise bad_request("上传的图片为空")
    if len(payload) > settings.max_upload_bytes:
        raise payload_too_large(settings.max_upload_bytes)

    actual = sniff_image_mime(payload)
    if actual not in settings.allowed_mime_types:
        raise unsupported_media_type(declared)

    gate = request.app.state.recognize_gate
    queued_at = time.perf_counter()
    async with gate.slot():
        queued_seconds = time.perf_counter() - queued_at
        queued_ms = int(round(queued_seconds * 1000))
        RECOGNIZE_QUEUE.observe(queued_seconds)
        RECOGNIZE_IN_FLIGHT.inc()
        started = time.perf_counter()
        try:
            # 线程池里跑：同步推理不能占着事件循环不放，否则健康检查与菜谱接口一起被堵住
            result = await run_in_threadpool(recognizer.recognize, payload)
        except ApiError:
            RECOGNIZE_REQUESTS.inc(outcome="error")
            raise
        except Exception as exc:  # 兜底：任何识别异常都不应变成 500 未处理崩溃
            RECOGNIZE_REQUESTS.inc(outcome="error")
            raise ApiError(500, 50001, None, {"reason": str(exc)}) from exc
        finally:
            RECOGNIZE_IN_FLIGHT.dec()
            RECOGNIZE_INFERENCE.observe(time.perf_counter() - started)
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))

    # 走到这里说明识别本身没抛异常：降级与正常各记一档，两者都是接口契约内的结果
    RECOGNIZE_REQUESTS.inc(outcome="degraded" if result.degraded else "ok")
    recognition_id = str(uuid.uuid4())
    candidates = [
        DishCandidateOut(
            index=item.index,
            name=item.name,
            confidence=item.confidence,
            provisional_name=item.provisional_name,
        )
        for item in result.candidates
    ]
    top1 = candidates[0] if candidates else None
    logger.info(
        "识别完成：top1=%s 置信度=%.3f 推理=%d ms 排队=%d ms",
        top1.name if top1 else "-",
        top1.confidence if top1 else 0.0,
        elapsed_ms,
        queued_ms,
    )
    session.add(
        RecognitionLog(
            recognition_id=recognition_id,
            model_version=result.model_version,
            degraded=result.degraded,
            class_count=result.class_count,
            top1_index=top1.index if top1 else None,
            top1_confidence=top1.confidence if top1 else None,
            elapsed_ms=elapsed_ms,
            result_json=json.dumps(
                [item.model_dump() for item in candidates], ensure_ascii=False
            ),
            image_sha256=hashlib.sha256(payload).hexdigest(),
        )
    )

    return RecognizeResponse(
        recognition_id=recognition_id,
        model_version=result.model_version,
        class_count=result.class_count,
        labels_available=result.labels_available,
        degraded=result.degraded,
        elapsed_ms=elapsed_ms,
        candidates=candidates,
    )
