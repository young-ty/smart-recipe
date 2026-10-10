"""全局配置：上传限制、识别与生成参数、数据库与模型路径。

所有可调参数集中于此，路由与服务层不允许出现魔法值。
v2.0（成品菜方向）：模型换成 208 类菜品分类，识别参数一并从检测解码改为 top-k 分类。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.core.env import load_env_file

APP_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = APP_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = APP_DIR / "data"

# 启动时先把仓库根目录的 .env 读进环境变量（真实环境变量优先，.env 只补空缺）。
# 大模型 API Key 就走这里：本地放进 .env（已 gitignore），部署时用系统环境变量。
load_env_file()


def _env_flag(name: str, default: bool = True) -> bool:
    """布尔型环境变量：0 / false / no / off / 空串都算关，其余算开。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


def _default_database_url() -> str:
    override = os.getenv("RECIPE_DATABASE_URL")
    if override:
        return override
    db_file = os.getenv("RECIPE_DB_FILE", str(BACKEND_DIR / "recipe.db"))
    return "sqlite+pysqlite:///" + Path(db_file).as_posix()


@dataclass(frozen=True)
class Settings:
    """运行期配置。测试可通过构造临时实例覆盖，无需改环境变量。"""

    app_name: str = "基于深度学习的智能菜谱识别 API"
    api_version: str = "2.0.0"

    # 上传校验（设计方案 5.3.1）
    max_upload_bytes: int = 5 * 1024 * 1024
    allowed_mime_types: tuple = ("image/jpeg", "image/png", "image/webp")

    # 识别参数（设计方案 5.3.1、7.2）
    top_k: int = 5
    dish_class_count: int = 208
    # 同时在跑的推理数上限（设计方案 5.3.1 / NFR-01）。ONNX 是同步的 CPU 密集调用，
    # 不限并发时请求会一起抢核：实测 16 逻辑核上吞吐在并发 4 到顶，再往上只涨排队时间。
    recognize_max_concurrency: int = field(
        default_factory=lambda: max(1, int(os.getenv("RECIPE_RECOGNIZE_MAX_CONCURRENCY", "4")))
    )
    # 推理跑在哪块硬件上（设计方案 7.3）。auto = 有可用的加速就用，否则 CPU；
    # 也可以写 cpu / cuda / tensorrt 或逗号分隔的一串，顺序即优先级。
    # 名字认不认识、装没装，由 backend/app/services/onnx_providers.py 按事实报出来：
    # 装了 GPU 版运行时也可能因为缺 cuDNN 而静默回落到 CPU，那一步必须能被看见。
    recognize_providers: str = field(
        default_factory=lambda: os.getenv("RECIPE_ONNX_PROVIDERS", "auto")
    )
    # 启动时预热识别器（加载 ONNX 会话）。不预热的话这份开销落在第一个用户请求上：
    # 实测首请求墙钟 1324 ms 对预热后的 234 ms，而且它发生在依赖解析阶段，
    # 接口返回的 elapsed_ms 里根本看不到——排障时只会觉得「第一个请求怎么这么慢」。
    warm_up_on_start: bool = field(
        default_factory=lambda: _env_flag("RECIPE_WARMUP", True)
    )

    # 菜名与生成参数（设计方案 5.3.2、7.5）
    max_dish_length: int = 64
    prompt_version: str = "v3"  # v3：角色与契约移入 system 消息、菜名定界，旧缓存自然失效
    # 单次生成的超时上限。实测 deepseek-flash 出一份完整菜谱要 3~7 秒，
    # 早期写死的 6 秒会擦边超时，把正常生成误判成「生成服务不可用」；
    # 留出余量后定为 20 秒，仍然只是兜底——正常调用 7 秒内就返回了。
    generator_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("RECIPE_GENERATOR_TIMEOUT_SECONDS", "20"))
    )
    generator_model: str = field(
        # 默认值取 DeepSeek /models 里显式列出的 id（deepseek-chat 是对版本跟随的别名，
        # 也能调通，但不在列表里；换成别家只改这一个环境变量）
        default_factory=lambda: os.getenv("RECIPE_GENERATOR_MODEL", "deepseek-flash")
    )
    generator_base_url: str = field(
        default_factory=lambda: os.getenv(
            "RECIPE_GENERATOR_BASE_URL", "https://api.deepseek.com/v1"
        )
    )
    # 视频链接模板：由后端按菜名拼接，模型直出的 URL 一律不采信（FR-14）
    video_search_template: str = "https://search.bilibili.com/all?keyword={keyword}"

    # 数据与模型路径
    data_dir: Path = DATA_DIR
    models_dir: Path = field(
        default_factory=lambda: Path(os.getenv("RECIPE_MODELS_DIR", str(REPO_ROOT / "models")))
    )
    dish_onnx_filename: str = field(
        default_factory=lambda: os.getenv("RECIPE_DISH_ONNX_FILE", "dish.onnx")
    )
    dish_manifest_filename: str = "dish.manifest.json"
    # 类别清单：新方向单独命名；旧方向（103 类食材）的 labels.json 已随该方向的权重一起清理
    dish_labels_filename: str = field(
        default_factory=lambda: os.getenv("RECIPE_DISH_LABELS_FILE", "dish_labels.json")
    )
    dish_captions_filename: str = "dish_captions.json"
    dish_map_filename: str = "seed_dish_map.json"

    # 识别实现标识（/api/recognize 的 model_version 字段）
    mock_model_version: str = "mock-dish-convnext-base-208"
    onnx_model_version: str = "dish-convnext-base-208-onnx"

    # 本地工具（起草种子菜谱、凭据自检）用的 Key，**不在请求链路里**：
    # 线上生成只认调用方自己带来的 Key（请求头 > 账号里存的），没有就返回 unavailable。
    # 服务端不预置任何额度，也不替用户掏钱（设计方案 7.8）。
    generator_api_key: str = field(
        default_factory=lambda: os.getenv("RECIPE_GENERATOR_API_KEY", "")
    )
    # 生成结果写回菜谱库（设计方案 7.5）：库里没有的菜由大模型生成后落库，
    # 下次直接命中库内、结果稳定且不再消耗额度；关掉它可以让演示每次走生成分支。
    promote_generated: bool = field(
        default_factory=lambda: _env_flag("RECIPE_PROMOTE_GENERATED", True)
    )

    # 账号与会话（设计方案 6.5）
    session_ttl_days: int = field(
        default_factory=lambda: max(1, int(os.getenv("RECIPE_SESSION_TTL_DAYS", "30")))
    )
    session_cookie_name: str = "recipe_session"
    # HTTPS 部署时置 1：会话 cookie 只在加密连接上发送
    session_cookie_secure: bool = field(
        default_factory=lambda: _env_flag("RECIPE_COOKIE_SECURE", False)
    )
    # 账号级记忆的条数上限：与本机记忆同一个数，两边口径一致
    memory_sync_limit: int = field(
        default_factory=lambda: max(1, int(os.getenv("RECIPE_MEMORY_LIMIT", "12")))
    )
    # 登录失败限流：同一邮箱在窗口内失败到这个次数就锁一会儿（见 5.6.2）
    login_max_failures: int = field(
        default_factory=lambda: max(1, int(os.getenv("RECIPE_LOGIN_MAX_FAILURES", "5")))
    )
    login_lock_seconds: float = field(
        default_factory=lambda: float(os.getenv("RECIPE_LOGIN_LOCK_SECONDS", "300"))
    )
    # 自动入库的菜谱 id 从这里起编，避开种子数据（101~175）与未来补库的号段
    promoted_recipe_id_base: int = 10000
    # 账号级模型 Key 的落库加密主密钥（core/crypto.py）：优先取 RECIPE_SECRET_KEY，
    # 没配就用这个文件，文件不存在时自动生成一份。
    secret_key_file: Path = field(
        default_factory=lambda: Path(
            os.getenv("RECIPE_SECRET_KEY_FILE", str(BACKEND_DIR / "secret.key"))
        )
    )

    database_url: str = field(default_factory=_default_database_url)

    @property
    def dish_onnx_path(self) -> Path:
        return self.models_dir / self.dish_onnx_filename

    @property
    def dish_manifest_path(self) -> Path:
        return self.models_dir / self.dish_manifest_filename

    @property
    def dish_labels_path(self) -> Path:
        return self.models_dir / self.dish_labels_filename

    @property
    def dish_captions_path(self) -> Path:
        return self.data_dir / self.dish_captions_filename

    @property
    def dish_map_path(self) -> Path:
        return self.data_dir / self.dish_map_filename

    @property
    def generator_configured(self) -> bool:
        """本地工具是否拿到了可用 Key；请求链路不看这个值（见 7.8）。"""

        return bool(self.generator_api_key)


_settings = Settings()


def get_settings() -> Settings:
    return _settings
