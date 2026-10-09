# -*- coding: utf-8 -*-
"""把仓库根目录的 .env 读进环境变量（不引入额外依赖）。

为什么不用 python-dotenv：运行依赖只有 fastapi / uvicorn / sqlalchemy / pydantic，
演示机常常离线装包，为读几行配置多一个依赖不划算。这里只支持 .env 的常用子集——
KEY=VALUE、# 注释、空行、值两侧的引号、行首的 export。

两条约定：

1. **真实环境变量优先**：.env 只补空缺（override=False），部署时用系统环境变量覆盖本地文件；
2. **只记名字，不记值**：返回的是加载到的变量名列表，便于日志排查，且永远不会把 Key 写进日志。

解析失败不抛异常，只跳过那一行——一个写错的配置项不该把服务打死。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

# backend/app/core/env.py → 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[3]

# 先读基础文件，再读本地覆盖文件（.env.local 优先级更高）
DEFAULT_FILES = (".env", ".env.local")

_QUOTES = ("'", '"')


def parse_env(text: str) -> Dict[str, str]:
    """把 .env 的文本解析成键值对；不认识的行走人不误。"""

    pairs: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
            value = value[1:-1]
        pairs[key] = value
    return pairs


def load_env_file(path: Optional[Path] = None, override: bool = False) -> List[str]:
    """读入一个 .env 文件，返回本次真正生效的变量名（不含值）。

    默认按 :data:`DEFAULT_FILES` 逐个读，不存在的文件直接跳过。
    """

    paths = [path] if path is not None else [REPO_ROOT / name for name in DEFAULT_FILES]
    loaded: List[str] = []
    for candidate in paths:
        try:
            text = Path(candidate).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for key, value in parse_env(text).items():
            if not override and key in os.environ:
                continue  # 真实环境变量优先
            os.environ[key] = value
            loaded.append(key)
    return loaded
