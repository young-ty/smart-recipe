"""账号级模型 Key 与凭据探活（设计方案 5.6.5、7.8）。

用户自带 Key 时，前端只有一个「保存」按钮，保存成功不代表 Key 能用：错的 Key 要等到
某道库外菜生成失败才暴露，而那时界面只说「生成服务不可用」，用户分不清是 Key、地址
还是模型名的问题。这个接口用一次 max_tokens=1 的最小调用把原因说清楚。

Key 存进账号后是加密落库的，读取只回掩码；服务端自己不预置任何 Key。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.crypto import SecretBox
from ..core.database import get_db
from ..models import User
from ..schemas import LlmKeyRequest, LlmKeyResponse, LlmProbeResponse
from ..services import accounts
from ..services.generator import RecipeGenerator
from .deps import get_generator, get_secret_box, require_user

router = APIRouter(prefix="/api", tags=["llm"])


def _to_response(view: accounts.LlmKeyView) -> LlmKeyResponse:
    return LlmKeyResponse(
        configured=view.configured,
        masked=view.masked,
        base_url=view.base_url,
        model=view.model,
        updated_at=view.updated_at,
    )


@router.get("/llm/key", response_model=LlmKeyResponse)
def get_llm_key(
    user: User = Depends(require_user),
    secret_box: SecretBox = Depends(get_secret_box),
) -> LlmKeyResponse:
    """账号里那份 Key 的掩码；换设备登录后照旧能用，就是这个接口在兜着。"""

    return _to_response(accounts.llm_key_view(user, secret_box))


@router.put("/llm/key", response_model=LlmKeyResponse)
def put_llm_key(
    payload: LlmKeyRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
    secret_box: SecretBox = Depends(get_secret_box),
) -> LlmKeyResponse:
    """把 Key 记到账号上：加密后落库，同一账号只留一份，再存就是覆盖。"""

    view = accounts.store_llm_key(
        session,
        user,
        api_key=payload.api_key,
        base_url=payload.base_url,
        model=payload.model,
        box=secret_box,
    )
    return _to_response(view)


@router.delete("/llm/key", status_code=204)
def delete_llm_key(
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
) -> None:
    """忘掉账号里的 Key：库内菜谱照常取，库外菜回到「生成服务不可用」。"""

    accounts.clear_llm_key(session, user)


@router.post("/llm/probe", response_model=LlmProbeResponse)
def probe_llm(generator: RecipeGenerator = Depends(get_generator)) -> LlmProbeResponse:
    """探活用的就是本次调用真正会用到的那份凭据（请求头优先，其次账号）。"""

    outcome = generator.probe()
    return LlmProbeResponse(
        ok=outcome.ok,
        configured=bool(generator.configured),
        model=str(getattr(generator, "model", "unset")),
        detail=outcome.detail,
    )
