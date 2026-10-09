"""识别日志表（设计方案 6.2）。

隐私约束：不落盘保存用户上传的原图，只保存摘要与识别结果快照。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RecognitionLog(Base):
    __tablename__ = "recognition_logs"
    __table_args__ = (Index("ix_recognition_logs_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recognition_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    class_count: Mapped[int] = mapped_column(Integer, nullable=False)
    top1_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    top1_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    image_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)