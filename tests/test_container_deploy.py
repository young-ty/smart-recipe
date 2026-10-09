# -*- coding: utf-8 -*-
"""容器化部署的契约测试。

容器化回答的是「换一台机器还能不能跑起来」。这里把搭建过程中真实踩到的坑钉住，
因为它们的共同点是**不会报错**：预处理实现漏进镜像只会让识别静默降级成模拟结果，
权重被误排除会让容器一直跑在模拟实现上，nginx 的上传上限比后端小会让大图
拿到一个看不懂的 413 页面。这些都不是崩溃，而是「看起来在跑，其实不对」。

运行：python -m unittest tests.test_container_deploy -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DOCKERFILE = REPO_ROOT / "Dockerfile.backend"
FRONTEND_DOCKERFILE = REPO_ROOT / "Dockerfile.frontend"
COMPOSE = REPO_ROOT / "docker-compose.yml"
NGINX = REPO_ROOT / "deploy" / "nginx.conf"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
REQUIREMENTS = REPO_ROOT / "backend" / "requirements.txt"
BACKEND_CONFIG = REPO_ROOT / "backend" / "app" / "core" / "config.py"
DEPLOY_DOC = REPO_ROOT / "docs" / "容器化部署.md"
README = REPO_ROOT / "README.md"


def strip_comments(text):
    """只看生效的配置行：注释里会提到 VITE_API_BASE_URL 这类名字，那是说明不是用法。"""

    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


class ContainerDeployTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.backend = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
        cls.frontend = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")
        cls.compose = COMPOSE.read_text(encoding="utf-8")
        cls.nginx = NGINX.read_text(encoding="utf-8")
        cls.ignore = DOCKERIGNORE.read_text(encoding="utf-8")
        cls.requirements = REQUIREMENTS.read_text(encoding="utf-8")
        cls.doc = DEPLOY_DOC.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_all_deploy_files_exist(self):
        for path in (
            BACKEND_DOCKERFILE,
            FRONTEND_DOCKERFILE,
            COMPOSE,
            NGINX,
            DOCKERIGNORE,
            DEPLOY_DOC,
        ):
            self.assertTrue(path.is_file(), "缺少 %s" % path)

    def test_backend_image_ships_the_canonical_preprocess(self):
        """识别器的预处理实现按路径加载（backend/app/services/dish_preprocess.py）。

        漏进镜像时不会报错：load_canonical_preprocess 抛 DishModelUnavailable，
        识别层按设计降级成模拟结果，接口照常返回 200。第一次搭镜像就是这么中招的，
        所以这里要求 Dockerfile 明确 COPY 那份实现。
        """

        self.assertIn("COPY prototype/preprocess.py", self.backend)
        self.assertIn("静默降级", self.backend, "要写清漏掉它的后果，否则下一个人还会删")

    def test_weights_are_mounted_not_baked_into_the_image(self):
        """权重 335 MB 且已被 gitignore，只能挂载进来，不能进构建上下文。"""

        self.assertIn("./models:/app/models:ro", self.compose, "权重要以只读方式挂载")
        self.assertIn("RECIPE_MODELS_DIR: /app/models", self.compose)
        self.assertNotIn("COPY models", self.backend, "权重不许打进镜像")
        self.assertIn("*.onnx", self.ignore)

    def test_dockerignore_anchors_directory_rules_to_the_context_root(self):
        """目录规则必须写成 /models 这种锚定形式。

        写成裸的 models 会连 backend/app/models 这个代码包一起排除掉，
        而且构建照样成功——镜像里少一个包，要到运行期 import 才炸。
        """

        packages = {
            path.name for path in (REPO_ROOT / "backend" / "app").iterdir() if path.is_dir()
        }
        dangerous = []
        for raw in self.ignore.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "!")) or "/" in line:
                continue
            if any(ch in line for ch in "*?["):
                continue
            if line in packages:
                dangerous.append(line)
        self.assertEqual(
            dangerous,
            [],
            "这些规则与 backend/app 下的代码包同名且没有锚定，会把代码一起排除：%s" % dangerous,
        )
        self.assertIn("/models", self.ignore)
        for anchored in ("/prototype/", "/training", "/docs", "/tests"):
            self.assertIn(anchored, self.ignore)

    def test_prototype_extras_are_kept_out_of_the_image(self):
        """同目录下的原型构建脚本与 HTML 不属运行期依赖，没必要进镜像。"""

        for name in ("build_dish_prototype.py", "dish.html", "index.html"):
            self.assertIn("/prototype/%s" % name, self.ignore)

    def test_database_lands_on_a_named_volume(self):
        """库放在卷里，`docker compose down` 之后生成过的菜谱还在。"""

        self.assertIn("RECIPE_DB_FILE: /data/recipe.db", self.compose)
        self.assertIn("recipe-data:/data", self.compose)
        self.assertIn("\nvolumes:\n  recipe-data:", self.compose, "缺少顶层 volumes 声明")

    def test_runtime_dependencies_are_pinned_to_the_verified_versions(self):
        """推理相关依赖按实测版本钉住，否则容器里的结果和本机对不上。"""

        for pin in ("onnxruntime==", "pillow==", "numpy=="):
            self.assertIn(pin, self.requirements, "缺少版本钉死：%s" % pin)
        self.assertIn("-r backend/requirements.txt", self.backend, "镜像要装同一份依赖清单")

    def test_backend_image_does_not_depend_on_apt(self):
        """实测 onnxruntime 1.19.2 在这个镜像里直接 import 就成功，不需要 libgomp1。

        引入 apt 会把构建绑在 apt 源上：Debian 13 的 Packages 索引在国内镜像上
        只有压缩版，apt-get update 会 404，加镜像加速也绕不过去。
        """

        self.assertIn("ARG PIP_INDEX_URL", self.backend)
        self.assertNotIn("RUN apt-get", self.backend)
        self.assertNotIn("apt-get install", self.backend)

    def test_frontend_is_built_then_served_by_nginx(self):
        self.assertIn("AS build", self.frontend, "前端要多阶段构建")
        self.assertIn("npm ci", self.frontend)
        self.assertIn("npm run build", self.frontend)
        self.assertIn("FROM nginx:", self.frontend)
        self.assertIn("--from=build /app/dist", self.frontend)

    def test_single_origin_keeps_the_recipe_memory_working(self):
        """浏览器只访问一个源：/api 由 nginx 转给后端，免 CORS，也保住 localStorage。

        菜谱记忆按 origin 隔离，宿主端口必须继续映射到 5173，否则换端口等于换一份记忆。
        """

        self.assertIn("proxy_pass http://backend:8000", self.nginx)
        self.assertIn("try_files $uri /index.html", self.nginx)
        self.assertIn("127.0.0.1:5173:80", self.compose)
        effective = strip_comments(self.frontend) + strip_comments(self.nginx)
        self.assertNotIn("VITE_API_BASE_URL", effective, "前端应继续用相对路径，不注入绝对地址")

    def test_nginx_upload_limit_is_not_smaller_than_the_backend_limit(self):
        """nginx 默认只收 1 MB，比后端的 5 MB 还小，大图会先一步被它挡掉。"""

        backend_limit = re.search(r"max_upload_bytes: int = (\d+) \* (\d+) \* (\d+)", BACKEND_CONFIG.read_text(encoding="utf-8"))
        self.assertIsNotNone(backend_limit, "没读到后端的上传上限")
        allowed = int(backend_limit.group(1)) * int(backend_limit.group(2)) * int(backend_limit.group(3))

        entry = re.search(r"client_max_body_size\s+(\d+)m;", self.nginx)
        self.assertIsNotNone(entry, "nginx 没配 client_max_body_size")
        self.assertGreaterEqual(int(entry.group(1)) * 1024 * 1024, allowed, "nginx 的上限不能小于后端")

    def test_generation_timeout_survives_the_reverse_proxy(self):
        """库外菜要现调大模型，后端超时 20 秒，代理不能比它先放弃。"""

        timeout = re.search(r"proxy_read_timeout\s+(\d+)s;", self.nginx)
        self.assertIsNotNone(timeout)
        self.assertGreaterEqual(int(timeout.group(1)), 20)
        self.assertIn("proxy_send_timeout", self.nginx)

    def test_compose_uses_cpu_execution_provider_explicitly(self):
        """容器镜像里没有 CUDA，写死执行提供程序，省得每次启动都去探一遍。"""

        self.assertIn("RECIPE_ONNX_PROVIDERS: cpu", self.compose)

    def test_frontend_waits_for_a_healthy_backend(self):
        """前端一上来就代理 /api，后端没就绪的话首屏就是报错。"""

        self.assertIn("condition: service_healthy", self.compose)
        self.assertIn("healthcheck:", self.compose)

    def test_missing_env_file_still_starts(self):
        """没有 .env 也要能起来：只是库外菜走「生成服务不可用」，识别不受影响。"""

        self.assertIn("path: .env", self.compose)
        self.assertIn("required: false", self.compose)
        self.assertNotIn("RECIPE_GENERATOR_API_KEY", self.compose, "Key 只能从 .env 注入，不写进 compose")

    def test_build_args_default_to_domestic_mirrors(self):
        """本机网络到 pypi.org / registry.npmjs.org 的 TLS 会被中断，默认值必须能直接构建。"""

        self.assertIn("ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple", self.backend)
        self.assertIn("ARG NPM_REGISTRY=https://registry.npmmirror.com", self.frontend)
        self.assertIn("PIP_INDEX_URL=https://pypi.org/simple", self.doc, "文档要写清怎么切回官方源")

    def test_readme_documents_the_container_entry(self):
        for keyword in ("docker compose up -d --build", "docs/容器化部署.md", "docker-compose.yml"):
            self.assertIn(keyword, self.readme, "README 未说明容器化入口：%s" % keyword)

    def test_deploy_doc_records_measured_numbers(self):
        """文档里的数字要来自实跑，不能只写「快」和「可用」。"""

        for keyword in ("6.8", "193.5", "161.0", "CPUExecutionProvider", "recipe-data"):
            self.assertIn(keyword, self.doc, "部署文档缺少实测记录：%s" % keyword)


if __name__ == "__main__":
    unittest.main()
