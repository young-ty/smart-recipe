"""后端测试夹具：每个用例一套独立临时数据库，互不影响。

样例图片一律通过夹具提供，避免测试模块之间互相 import 造成包名歧义
（仓库根目录同样存在 tests 包）。
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.generator import GeneratedRecipePayload, ProbeOutcome  # noqa: E402

REPO_ROOT = BACKEND_DIR.parent
DEMO_DISHES_DIR = REPO_ROOT / "demo" / "dishes"

# 1x1 像素的合法 PNG，用于走通「上传 → 识别」链路
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
# 另一张真实图片（1x1 蓝色 PNG），用于验证「不同图片 → 不同结果」
PNG_1X1_BLUE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def png_bytes() -> bytes:
    return PNG_1X1


@pytest.fixture()
def png_bytes_alt() -> bytes:
    return PNG_1X1_BLUE


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """接口层测试统一走降级路径。

    把 models_dir 指向空目录，识别器必然回退为 MockDishRecognizer：接口测试因此
    与「权重是否已交付」解耦，既确定又不必每次加载 91 MB 模型。
    真实模型的加载与推理另见 test_model_integration.py。

    生成层同理显式置空：本机 .env 里配了 Key 也不该改变接口测试的预期（要测真实
    大模型必须先有自己的密钥与网络，那是人工验收，不是单元测试）。
    """

    return Settings(
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'test.db').as_posix()}",
        models_dir=tmp_path / "models-not-delivered",
        generator_api_key="",
        # 账号级 Key 的加解密主密钥也放进临时目录：用例之间互相看不到对方存的 Key
        secret_key_file=tmp_path / "secret.key",
    )


@pytest.fixture()
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def upload() -> Callable[..., dict]:
    """构造 multipart 上传参数：upload(payload, mime=..., name=...)。"""

    def _upload(payload: bytes, mime: str = "image/png", name: str = "shot.png") -> dict:
        return {"file": (name, payload, mime)}

    return _upload


@pytest.fixture()
def demo_photo() -> Callable[[str], bytes]:
    """取 demo/dishes 下的实拍图字节，供需要真实照片的用例使用。"""

    def _read(stem: str = "dish-braised-pork") -> bytes:
        path = DEMO_DISHES_DIR / f"{stem}.jpg"
        if not path.is_file():
            pytest.skip(f"演示图缺失：{path}")
        return path.read_bytes()

    return _read


class StubGenerator:
    """可控的生成层替身：记录调用次数，返回固定或指定结果。"""

    configured = True
    model = "stub-generator"

    def __init__(self, payload: GeneratedRecipePayload | None = None) -> None:
        self.calls: list[str] = []
        self.payload = payload or GeneratedRecipePayload(
            name="土豆肉末拌面",
            cook_time_minutes=30,
            difficulty="简单",
            servings=2,
            description="浇头要多留些汁，拌面才不干。",
            required=[{"name": "面条", "amount": "200 克"}],
            optional=[{"name": "葱", "amount": "1 根"}],
            steps=[
                "土豆切小丁煎至金黄焦香",
                "下肉末炒散后加生抽上色",
                "加水煮至软糯收汁",
                "面条煮熟后拌入浇头撒葱花",
            ],
        )

    def generate(self, dish: str):
        self.calls.append(dish)
        return self.payload

    def probe(self) -> ProbeOutcome:
        return ProbeOutcome(True, f"连接正常，模型 {self.model} 可用")


@pytest.fixture()
def stub_generator() -> StubGenerator:
    return StubGenerator()


@pytest.fixture()
def make_stub_generator() -> Callable[..., StubGenerator]:
    """按自定义 payload 造生成层替身，用于验证落库时的边界情况。"""

    def _make(payload: GeneratedRecipePayload | None = None) -> StubGenerator:
        return StubGenerator(payload)

    return _make


@pytest.fixture()
def session(app) -> Iterator[object]:
    """直接操作应用当前使用的数据库会话，用于校验落库结果。"""

    from app.core.database import get_session_factory

    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
