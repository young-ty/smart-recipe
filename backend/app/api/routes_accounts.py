"""账号、会话与账号级记忆接口（设计方案 5.6）。

会话走 httpOnly cookie：令牌不落在前端脚本能读到的地方，XSS 也偷不走。
未登录时 `/api/auth/me` 返回 `{"user": null}` 而不是 401——前端靠它区分
「没登录」与「这份后端没有账号功能」，静态演示站据此把账号入口藏起来。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.database import get_db
from ..core.errors import ApiError, too_many_requests
from ..models import User
from ..schemas import (
    LoginRequest,
    MeResponse,
    MemoryEntriesRequest,
    MemoryEntryOut,
    MemoryListResponse,
    RegisterRequest,
    UserEnvelope,
    UserOut,
)
from ..services import accounts
from .deps import get_app_settings, optional_user, require_user

router = APIRouter(prefix="/api", tags=["accounts"])

# 登录失败限流：进程内计数，配置项在 core/config.py
_throttle = accounts.LoginThrottle()


def _entry_out(record: accounts.MemoryRecord) -> MemoryEntryOut:
    return MemoryEntryOut(
        dish=record.dish,
        name=record.name,
        source=record.source,
        recipe=record.recipe,
        at=record.at,
        favorite=record.favorite,
    )


def _entry_list(records: List[accounts.MemoryRecord]) -> MemoryListResponse:
    return MemoryListResponse(entries=[_entry_out(record) for record in records])


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


@router.post("/auth/register", response_model=UserEnvelope, status_code=201)
def register(
    payload: RegisterRequest,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> UserEnvelope:
    """注册并直接登录：少一步「注册完再去登录」。"""

    user = accounts.register(
        session,
        settings,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    token = accounts.start_session(session, settings, user)
    _set_session_cookie(response, settings, token)
    return UserEnvelope(user=UserOut.model_validate(user))


@router.post("/auth/login", response_model=UserEnvelope)
def login(
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> UserEnvelope:
    key = accounts.normalize_email(payload.email)
    waiting = _throttle.retry_after(key)
    if waiting > 0:
        raise too_many_requests(f"登录尝试过于频繁，请 {int(waiting) + 1} 秒后再试")
    try:
        user = accounts.authenticate(session, email=payload.email, password=payload.password)
    except ApiError:
        _throttle.record_failure(key)
        raise
    _throttle.clear(key)
    token = accounts.start_session(session, settings, user)
    _set_session_cookie(response, settings, token)
    return UserEnvelope(user=UserOut.model_validate(user))


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """只退掉当前这条会话；其他设备上的登录不受影响。"""

    accounts.end_session(session, request.cookies.get(settings.session_cookie_name))
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/auth/me", response_model=MeResponse)
def me(user: Optional[User] = Depends(optional_user)) -> MeResponse:
    return MeResponse(user=UserOut.model_validate(user) if user is not None else None)


@router.get("/memories", response_model=MemoryListResponse)
def get_memories(
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MemoryListResponse:
    """账号里存的菜谱记忆，排序与本机记忆一致（收藏在前、各自最近在前）。"""

    return _entry_list(accounts.list_memories(session, user, settings.memory_sync_limit))


@router.put("/memories", response_model=MemoryListResponse)
def put_memories(
    payload: MemoryEntriesRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> MemoryListResponse:
    """整表覆盖式同步，返回落库后的结果（前端拿它回写本机，两边就一致了）。"""

    records = [
        accounts.MemoryRecord(
            dish=item.dish,
            name=item.name,
            source=item.source,
            recipe=item.recipe,
            at=item.at,
            favorite=item.favorite,
        )
        for item in payload.entries
    ]
    kept = accounts.replace_memories(session, user, records, settings.memory_sync_limit)
    return _entry_list(kept)
