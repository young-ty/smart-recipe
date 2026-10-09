# -*- coding: utf-8 -*-
"""手机端与 PWA 资源的契约测试。

手机端不另起一套代码：同一份 React 应用在窄屏叠成单栏，配上 manifest 与 service worker
就能「加到主屏」当应用用。这几份资源都不是 Python 能跑起来验证的，所以按原文读进来校验：

- manifest 的必需字段与图标齐全，声明的尺寸必须和真实 PNG 一致（写错尺寸不会报错，只会
  在添加到主屏时图标糊掉或者干脆不出现）；
- service worker 必须放过 /api/ 请求，否则识别与菜谱会拿到缓存里的旧结果；
- service worker 只能在生产构建里注册，开发期注册会把 HMR 的中间产物缓存下来；
- 窄屏断点里要有刘海屏内边距，否则 iPhone 上页头和右下角的按钮会被状态栏与小白条压住。

运行：python -m unittest tests.test_pwa_assets -v
"""

import json
import struct
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"
MANIFEST = FRONTEND / "public" / "manifest.webmanifest"
SERVICE_WORKER = FRONTEND / "public" / "sw.js"
INDEX_HTML = FRONTEND / "index.html"
MAIN_TSX = FRONTEND / "src" / "main.tsx"
APP_CSS = FRONTEND / "src" / "styles" / "app.css"

# 加到主屏至少要这两个尺寸：192 给普通图标，512 给启动画面与商店
REQUIRED_ICON_SIZES = ("192x192", "512x512")


def png_size(path: Path) -> tuple:
    """读 PNG 的 IHDR，拿到真实宽高。不引 Pillow，测试跑起来不需要额外依赖。"""

    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("不是 PNG 文件：%s" % path)
    width, height = struct.unpack(">II", header[16:24])
    return width, height


class PwaAssetContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.sw = SERVICE_WORKER.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")
        cls.main = MAIN_TSX.read_text(encoding="utf-8")
        cls.css = APP_CSS.read_text(encoding="utf-8")

    def test_manifest_declares_installable_fields(self):
        """字段缺一个，浏览器就不会给「添加到主屏幕」的入口。"""

        for field in ("name", "short_name", "description", "start_url", "scope", "display"):
            self.assertIn(field, self.manifest, "manifest 缺少字段：%s" % field)
        self.assertEqual(self.manifest["display"], "standalone", "不是 standalone 打开时还带地址栏")
        self.assertEqual(self.manifest["start_url"], "./", "start_url 要相对，才能放在子路径下")
        self.assertEqual(self.manifest["scope"], "./", "scope 要相对，才能放在子路径下")
        self.assertIn("菜谱", self.manifest["name"], "manifest 名称与实际应用对不上")

    def test_declared_icon_sizes_match_the_real_files(self):
        """声明尺寸和真实 PNG 不一致时不会报错，只会让图标在手机上糊掉。"""

        icons = self.manifest.get("icons") or []
        self.assertTrue(icons, "manifest 没声明任何图标")

        declared = []
        for icon in icons:
            path = FRONTEND / "public" / icon["src"]
            self.assertTrue(path.is_file(), "manifest 声明的图标不存在：%s" % icon["src"])
            width, height = png_size(path)
            declared.append("%dx%d" % (width, height))
            self.assertEqual(
                "%dx%d" % (width, height),
                icon["sizes"],
                "图标声明尺寸与真实文件不一致：%s" % icon["src"],
            )

        for size in REQUIRED_ICON_SIZES:
            self.assertIn(size, declared, "缺少可安装所需的图标尺寸：%s" % size)
        self.assertTrue(
            any(icon.get("purpose") == "maskable" for icon in icons),
            "缺少 maskable 图标，安卓上会被裁成圆形",
        )

    def test_page_wires_the_pwa_assets(self):
        for keyword in ('rel="manifest"', "theme-color", "apple-touch-icon", "viewport-fit=cover"):
            self.assertIn(keyword, self.html, "index.html 缺少 PWA 接线：%s" % keyword)
        self.assertIn("%BASE_URL%", self.html, "静态资源要走 %BASE_URL%，否则子路径下 404")

    def test_service_worker_never_caches_api_requests(self):
        """识别与菜谱走缓存会让页面显示过期结果，这条最要紧。"""

        self.assertIn("/api/", self.sw, "service worker 没有显式放过 /api/ 请求")
        self.assertIn("request.method !== 'GET'", self.sw, "非 GET 请求不该被 service worker 接管")
        self.assertIn("caches.keys()", self.sw, "缺少旧缓存清理，升级后会一直吃到旧脚本")

    def test_service_worker_only_registers_in_production(self):
        self.assertIn("serviceWorker", self.main, "生产构建没有注册 service worker")
        self.assertIn("import.meta.env.PROD", self.main, "开发期也会注册，会把 HMR 产物缓存下来")
        self.assertIn("BASE_URL", self.main, "注册路径要走 BASE_URL，否则子路径下找不到 sw.js")

    def test_narrow_screen_block_handles_the_notch(self):
        """窄屏断点里要有安全区内边距，否则页头与右下角按钮会被状态栏压住。"""

        start = self.css.index("@media (max-width: 640px)")
        block = self.css[start:]
        for keyword in ("env(safe-area-inset-top)", "env(safe-area-inset-bottom)", "min-height: 48px"):
            self.assertIn(keyword, block, "手机端缺少适配：%s" % keyword)


if __name__ == "__main__":
    unittest.main()
