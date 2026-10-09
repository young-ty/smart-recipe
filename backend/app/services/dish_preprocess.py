"""成品菜识别的预处理契约加载器。

识别精度完全取决于预处理是否与训练一致，所以这里**不做第二份拷贝**：
按文件路径加载仓库里唯一那份 prototype/preprocess.py，保证接口返回的置信度
与 prototype/dish.html、tests/test_dish_prototype.py 里的数字出自同一套代码。

找不到那一份实现时抛 DishModelUnavailable，识别层自然降级为模拟实现——
宁可明确降级，也不要悄悄换一套可能对不上的预处理。
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType

from ..core.config import REPO_ROOT

CANONICAL_PREPROCESS_PATH = REPO_ROOT / "prototype" / "preprocess.py"
_MODULE_NAME = "dish_canonical_preprocess"
_cached: ModuleType | None = None


class DishModelUnavailable(RuntimeError):
    """模型权重、manifest 或预处理实现缺失，无法启用真实识别。"""


def load_canonical_preprocess() -> ModuleType:
    """加载并缓存仓库里那份预处理实现。"""

    global _cached
    if _cached is not None:
        return _cached
    if not CANONICAL_PREPROCESS_PATH.is_file():
        raise DishModelUnavailable(f"找不到预处理实现：{CANONICAL_PREPROCESS_PATH}")

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, CANONICAL_PREPROCESS_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - 路径存在时不会发生
        raise DishModelUnavailable(f"无法加载预处理实现：{CANONICAL_PREPROCESS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(_MODULE_NAME, module)
    spec.loader.exec_module(module)
    _cached = module
    return module