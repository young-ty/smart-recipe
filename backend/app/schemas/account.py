"""账号、会话与账号级记忆的请求/响应模型（设计方案 5.6）。"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255, description="登录邮箱，大小写不敏感")
    password: str = Field(min_length=8, max_length=128, description="至少 8 位")
    display_name: Optional[str] = Field(default=None, max_length=32)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    """对外的账号信息：不含密码哈希，也不含任何令牌。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    created_at: datetime


class UserEnvelope(BaseModel):
    user: UserOut


class MeResponse(BaseModel):
    """未登录时 user 为 null，前端据此决定顶部显示登录还是账号名。"""

    user: Optional[UserOut] = None


class MemoryEntryIn(BaseModel):
    """一条记忆。字段与前端 `MemoryEntry` 一一对应（见设计方案 4.4）。"""

    dish: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    source: Literal["library", "generated"]
    recipe: Dict
    at: int = Field(ge=0, description="记录时间，epoch 毫秒")
    favorite: bool = False


class MemoryEntryOut(MemoryEntryIn):
    pass


class MemoryEntriesRequest(BaseModel):
    """整表覆盖式同步：前端合并完再提交，服务端只做校验与落库。"""

    entries: List[MemoryEntryIn] = Field(default_factory=list, max_length=200)


class MemoryListResponse(BaseModel):
    entries: List[MemoryEntryOut]
