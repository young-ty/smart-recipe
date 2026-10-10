r"""生成层（大模型）接入自检：确认 Key 被读到，并可选真实调用一次。

为什么单独放一个脚本：生成分支的失败**一律降级**为 `unavailable`、接口永远返回 200，
于是「Key 根本没读进来」和「这次网络超时了」在页面上长得一模一样。这个脚本把两者分开说清楚。

用法（在 backend 目录下运行；带 -m 的形式从仓库根目录运行）：

    ..\.venv\Scripts\python.exe -m tools.check_generator              # 只看配置，不联网
    ..\.venv\Scripts\python.exe -m tools.check_generator --models     # 列出服务端可用模型 id
    ..\.venv\Scripts\python.exe -m tools.check_generator --live       # 真实生成一次并校验结构

退出码：0 = 自检通过；1 = 配置缺失或调用失败。**默认模式不发网络请求**，可以进 CI；
`--models` / `--live` 会向 `RECIPE_GENERATOR_BASE_URL` 发出真实请求，并消耗一次调用额度。

安全约定：脚本只打印 Key 的长度与前缀，永远不回显 Key 本身（与 `app/core/env.py` 同一取舍）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings, get_settings  # noqa: E402
from app.services.generator import build_generator  # noqa: E402

# 库外菜（菜谱库里没有承接记录），走生成分支才有意义
DEFAULT_DISH = "蛋包饭"

MODELS_PATH = "/models"


def mask_secret(value: str) -> str:
    """只回显长度与前缀，够判断「读到了哪一把 Key」，又不足以复原它。"""

    if not value:
        return "未配置"
    prefix = value[:3] if len(value) > 8 else ""
    return "已配置（%d 字符，前缀 %s…）" % (len(value), prefix)


def describe(settings: Settings) -> dict:
    """把自检要看的四项配置摊平成可断言的字典。"""

    return {
        "configured": settings.generator_configured,
        "base_url": settings.generator_base_url,
        "model": settings.generator_model,
        "api_key": mask_secret(settings.generator_api_key),
    }


def fetch_model_ids(settings: Settings, client=None) -> List[str]:
    """GET /models，返回服务端认得的模型 id 列表。

    用途是**确认模型名写对了**：供应商的模型 id 与宣传名常常不一样，写错的报错信息
    只会出现在 `choices` 缺失里，不看模型列表很难定位。
    """

    import httpx

    owns_client = client is None
    client = client or httpx.Client(timeout=settings.generator_timeout_seconds)
    try:
        response = client.get(
            "%s%s" % (settings.generator_base_url.rstrip("/"), MODELS_PATH),
            headers={"Authorization": "Bearer %s" % settings.generator_api_key},
        )
        response.raise_for_status()
        body = response.json()
    finally:
        if owns_client:
            client.close()
    return [item["id"] for item in body.get("data") or [] if item.get("id")]


def run_live(settings: Settings, dish: str) -> Tuple[bool, str]:
    """真实调一次生成层，返回 (是否通过, 人话说明)。"""

    # 本地自检工具显式用自己的 Key：请求链路已经不认配置里那份了（见 7.8）
    generator = build_generator(settings, api_key=settings.generator_api_key)
    if not generator.configured:
        return False, "未配置 API Key"
    payload = generator.generate(dish)
    if payload is None:
        # 具体原因（超时 / 非 2xx / 结构不符）由 services.generator 打在 WARNING 日志里
        return False, "调用失败或结构化校验未通过（原因见上方 WARNING 日志）"
    return True, "%s · %d 分钟 · %s · 主料 %d 项 · 步骤 %d 步" % (
        payload.name,
        payload.cook_time_minutes,
        payload.difficulty,
        len(payload.required),
        len(payload.steps),
    )


def _print_config(info: dict) -> None:
    print("[1] 生成层配置：")
    for key in ("configured", "base_url", "model", "api_key"):
        print("    %-11s = %s" % (key, info[key]))
    if not info["configured"]:
        print(
            "    没读到 Key：把 .env.example 复制成 .env 填好 RECIPE_GENERATOR_API_KEY，"
            "或设同名系统环境变量（.env 只补空缺，系统环境变量优先）。"
            "这份 Key 只给本机工具用，服务端不读它：页面上用的是用户在「我的模型 Key」里填的那把。"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生成层（大模型）接入自检")
    parser.add_argument("--models", action="store_true", help="列出服务端可用模型 id（联网）")
    parser.add_argument("--live", action="store_true", help="真实生成一次并校验结构化输出（联网）")
    parser.add_argument("--dish", default=DEFAULT_DISH, help="--live 时发给模型的菜名")
    args = parser.parse_args(argv)

    settings = get_settings()
    info = describe(settings)
    _print_config(info)
    if not info["configured"]:
        return 1

    if args.models:
        try:
            model_ids = fetch_model_ids(settings)
        except Exception as exc:  # 网络 / 鉴权 / 响应结构，统一告知并算失败
            print("[2] 取模型列表失败：%s" % exc)
            return 1
        print("[2] 服务端可用模型：%s" % (", ".join(model_ids) or "（返回为空）"))

    if args.live:
        ok, detail = run_live(settings, args.dish)
        print("[3] 真实生成「%s」：%s —— %s" % (args.dish, "通过" if ok else "失败", detail))
        if not ok:
            return 1

    if not (args.models or args.live):
        print("[2] 只做了配置自检；加 --models / --live 才会真实联网调用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
