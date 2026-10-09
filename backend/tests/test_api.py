"""接口契约测试（设计方案 5.3、5.4、10.2）。

覆盖：识别、菜谱解析（库内 / 生成 / 不可用）、类别清单、菜谱详情、健康检查，
以及 v1.4 起退役的两个旧接口确实已经下线。
"""

from __future__ import annotations

import urllib.parse

from sqlalchemy import func, select

# 类别承接表的数据决定这三个数：能给出库内菜谱的类别数（精确同名 + 别名映射）、
# 表里的总行数（含 5 行显式登记为无菜谱的缺口），以及菜谱库的规模
COVERED_CLASS_COUNT = 50
DISH_MAP_ROWS = 55
LIBRARY_RECIPE_COUNT = 75

# 别名映射的抽样：类别名 → (类别下标, 库内菜谱 id, 库内菜名)
# 全部 8 条见 seed_dish_map.json，这里挑覆盖三种成因的代表（同义词 / 异写 / 简称）
DISH_ALIASES = (
    ("西红柿炒鸡蛋", 50, 101, "番茄炒蛋"),      # 西红柿 = 番茄
    ("蒜茸粉丝蒸虾", 120, 111, "蒜蓉粉丝蒸虾"),  # 茸 = 蓉
    ("炒青菜", 19, 124, "清炒青菜"),            # 简称
    ("土豆炖牛腩", 81, 113, "土豆炖牛肉"),      # 同族
)

DISH_LIBRARY_NAME = "红烧肉"
DISH_LIBRARY_ID = 109
DISH_LIBRARY_INDEX = 77
DISH_UNCOVERED_NAME = "土豆肉末拌面"
DISH_UNCOVERED_INDEX = 157
# 生成结果落库后的 id（Settings.promoted_recipe_id_base），避开种子号段 101~175
PROMOTED_RECIPE_ID = 10000
# 演示图注没覆盖的类别：名称只能落到占位
DISH_PLACEHOLDER_INDEX = 0
DISH_PLACEHOLDER_RECIPE_ID = 106


# --------------------------------------------------------------- 健康检查

def test_health_reports_database_recipes_and_model(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["recipe_count"] == LIBRARY_RECIPE_COUNT
    assert body["model"] == {
        "loaded": False,
        "version": "mock-dish-convnext-base-208",
        "degraded": True,
        "class_count": 208,
        "labels_available": False,
        "providers": [],
    }
    assert body["generator"] == {"configured": False, "model": "unset"}


# --------------------------------------------------------------- 识别

def test_recognize_returns_contract_fields(client, png_bytes, upload):
    response = client.post("/api/recognize", files=upload(png_bytes))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "recognition_id",
        "model_version",
        "class_count",
        "labels_available",
        "degraded",
        "elapsed_ms",
        "candidates",
    }
    assert len(body["recognition_id"]) == 36
    assert body["degraded"] is True
    assert body["labels_available"] is False
    assert body["class_count"] == 208
    assert body["elapsed_ms"] >= 0
    assert len(body["candidates"]) == 5
    for item in body["candidates"]:
        assert set(item) == {"index", "name", "confidence", "provisional_name"}
        assert 0 <= item["index"] < 208
        assert 0.0 <= item["confidence"] <= 1.0
        assert item["provisional_name"] is True, "类别清单未交付时名称必须标记为待确认"
        assert item["name"]


def test_recognize_returns_topk_in_descending_confidence(client, png_bytes, upload):
    candidates = client.post("/api/recognize", files=upload(png_bytes)).json()["candidates"]
    confidences = [item["confidence"] for item in candidates]
    assert confidences == sorted(confidences, reverse=True)
    assert len({item["index"] for item in candidates}) == len(candidates)


def test_recognize_is_deterministic_for_same_image(client, png_bytes, upload):
    first = client.post("/api/recognize", files=upload(png_bytes)).json()
    second = client.post("/api/recognize", files=upload(png_bytes)).json()
    assert first["candidates"] == second["candidates"]
    assert first["recognition_id"] != second["recognition_id"]


def test_recognize_gives_different_candidates_for_different_images(
    client, png_bytes, png_bytes_alt, upload
):
    first = client.post("/api/recognize", files=upload(png_bytes)).json()
    second = client.post("/api/recognize", files=upload(png_bytes_alt)).json()
    assert first["candidates"] != second["candidates"]


def test_recognize_persists_log_without_storing_image(client, session, png_bytes, upload):
    from app.models import RecognitionLog

    body = client.post("/api/recognize", files=upload(png_bytes)).json()
    log = session.scalar(
        select(RecognitionLog).where(RecognitionLog.recognition_id == body["recognition_id"])
    )
    assert log is not None
    assert log.degraded is True
    assert log.class_count == 208
    assert log.top1_index == body["candidates"][0]["index"]
    assert log.top1_confidence == body["candidates"][0]["confidence"]
    assert log.image_sha256 and len(log.image_sha256) == 64
    assert log.result_json.startswith("[")
    assert log.created_at is not None


def test_recognize_rejects_unsupported_mime(client, png_bytes, upload):
    response = client.post("/api/recognize", files=upload(png_bytes, mime="text/plain"))
    assert response.status_code == 415
    assert response.json()["error"]["code"] == 41501


def test_recognize_rejects_fake_image_with_valid_mime(client, upload):
    response = client.post("/api/recognize", files=upload(b"this is not an image"))
    assert response.status_code == 415
    assert response.json()["error"]["code"] == 41501


def test_recognize_rejects_empty_payload(client, upload):
    response = client.post("/api/recognize", files=upload(b""))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 40001


def test_recognize_rejects_oversized_file(client, upload):
    oversize = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024 + 1)
    response = client.post("/api/recognize", files=upload(oversize))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == 41301


# --------------------------------------------------------------- 菜谱解析

def test_resolve_hits_library_by_class_index(client):
    body = client.post(
        "/api/recipes/resolve", json={"dish": DISH_LIBRARY_NAME, "index": DISH_LIBRARY_INDEX}
    ).json()
    assert body["source"] == "library"
    assert body["degraded"] is False
    recipe = body["recipe"]
    assert recipe["id"] == DISH_LIBRARY_ID
    assert recipe["name"] == DISH_LIBRARY_NAME
    assert recipe["steps"] and recipe["required"]
    assert recipe["cook_time_minutes"] > 0


def test_resolve_hits_library_by_name_without_index(client):
    body = client.post("/api/recipes/resolve", json={"dish": DISH_LIBRARY_NAME}).json()
    assert body["source"] == "library"
    assert body["recipe"]["id"] == DISH_LIBRARY_ID


def test_resolve_hits_library_through_alias_mapping(client):
    """别名映射：类别名与库内菜名不是同一个写法，仍要命中那道菜。

    这类映射只在 seed_dish_map.json 里用 alias 登记（西红柿/番茄、茸/蓉 异写），
    所以别名丢了就会退化成「库外菜 → 走生成分支」，且页面上看不出区别。
    """

    for dish_name, index, recipe_id, recipe_name in DISH_ALIASES:
        body = client.post(
            "/api/recipes/resolve", json={"dish": dish_name, "index": index}
        ).json()
        assert body["source"] == "library", "%s 应命中库内" % dish_name
        assert body["degraded"] is False
        assert body["recipe"]["id"] == recipe_id
        assert body["recipe"]["name"] == recipe_name


def test_resolve_hits_library_through_class_map_when_name_is_placeholder(client):
    """类别清单没到位时，页面上的候选名可能还是「类别 #N」，仍要能对上库内菜谱。"""

    body = client.post(
        "/api/recipes/resolve", json={"dish": f"类别 #{DISH_LIBRARY_INDEX}", "index": DISH_LIBRARY_INDEX}
    ).json()
    assert body["source"] == "library"
    assert body["recipe"]["id"] == DISH_LIBRARY_ID


def test_resolve_normalizes_noisy_dish_name(client):
    for noisy in ("红烧肉的做法", " 红烧肉 ", "红烧肉怎么做"):
        body = client.post("/api/recipes/resolve", json={"dish": noisy}).json()
        assert body["source"] == "library", noisy
        assert body["dish"] == DISH_LIBRARY_NAME


def test_resolve_builds_video_url_from_dish_name(client):
    recipe = client.post(
        "/api/recipes/resolve", json={"dish": DISH_LIBRARY_NAME}
    ).json()["recipe"]
    keyword = urllib.parse.quote(f"{DISH_LIBRARY_NAME} 做法")
    assert recipe["video_url"] == f"https://search.bilibili.com/all?keyword={keyword}"


def test_resolve_degrades_when_library_misses_and_generator_is_off(client):
    body = client.post(
        "/api/recipes/resolve", json={"dish": DISH_UNCOVERED_NAME, "index": DISH_UNCOVERED_INDEX}
    ).json()
    assert body["source"] == "unavailable"
    assert body["degraded"] is True
    assert body["recipe"] is None
    assert body["reason"], "不可用时必须给出原因，前端据此渲染提示态"


def test_resolve_refuses_to_generate_from_placeholder_name(client):
    body = client.post("/api/recipes/resolve", json={"dish": "类别 #99"}).json()
    assert body["source"] == "unavailable"
    assert body["recipe"] is None


def test_resolve_returns_generated_recipe_when_generator_answers(app, stub_generator):
    from fastapi.testclient import TestClient

    app.dependency_overrides = {}
    from app.api.deps import get_generator

    app.dependency_overrides[get_generator] = lambda: stub_generator
    with TestClient(app) as client:
        body = client.post(
            "/api/recipes/resolve", json={"dish": DISH_UNCOVERED_NAME, "index": DISH_UNCOVERED_INDEX}
        ).json()
    assert body["source"] == "generated"
    assert body["degraded"] is False
    assert body["recipe"]["name"] == DISH_UNCOVERED_NAME
    assert body["recipe"]["id"] == PROMOTED_RECIPE_ID, "生成结果当场落库，返回库内新 id"
    assert body["recipe"]["origin"] == "generated"
    assert body["recipe"]["rating"] is None, "生成结果不给评分"
    assert body["recipe"]["steps"]
    assert stub_generator.calls == [DISH_UNCOVERED_NAME]


def test_second_call_hits_the_promoted_recipe(app, stub_generator, session):
    """落库之后第二次请求走库内分支：source 由 generated 变 library，大模型只调一次。"""

    from fastapi.testclient import TestClient

    from app.api.deps import get_generator
    from app.models import GeneratedRecipe

    app.dependency_overrides[get_generator] = lambda: stub_generator
    with TestClient(app) as client:
        first = client.post("/api/recipes/resolve", json={"dish": DISH_UNCOVERED_NAME}).json()
        second = client.post("/api/recipes/resolve", json={"dish": DISH_UNCOVERED_NAME}).json()
    assert first["source"] == "generated"
    assert second["source"] == "library", "第二次应直接命中刚入库的菜谱"
    assert second["recipe"]["id"] == first["recipe"]["id"] == PROMOTED_RECIPE_ID
    assert second["recipe"]["origin"] == "generated"
    assert second["recipe"] == first["recipe"]
    assert stub_generator.calls == [DISH_UNCOVERED_NAME], "第二次应命中库内，不再调用大模型"
    cached = session.scalars(select(GeneratedRecipe)).all()
    assert len(cached) == 1
    assert cached[0].prompt_version == "v3"
    assert cached[0].model == "stub-generator"


def test_second_call_is_served_from_cache_when_promotion_is_off(settings, stub_generator):
    """关掉落库开关时缓存仍然兜底：不落库，也不重复计费。"""

    from fastapi.testclient import TestClient

    from app.api.deps import get_generator
    from app.core.database import get_session_factory
    from app.main import create_app
    from app.models import GeneratedRecipe

    no_promote = type(settings)(**{**settings.__dict__, "promote_generated": False})
    app = create_app(no_promote)
    app.dependency_overrides[get_generator] = lambda: stub_generator
    with TestClient(app) as client:
        first = client.post("/api/recipes/resolve", json={"dish": DISH_UNCOVERED_NAME}).json()
        second = client.post("/api/recipes/resolve", json={"dish": DISH_UNCOVERED_NAME}).json()
    assert first["source"] == second["source"] == "generated"
    assert first["recipe"]["id"] is None, "不落库就没有库内 id"
    assert first["recipe"] == second["recipe"]
    assert stub_generator.calls == [DISH_UNCOVERED_NAME]
    with get_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(GeneratedRecipe)) == 1


def test_resolve_never_calls_generator_when_library_has_the_dish(app, stub_generator):
    """先查库、后生成是硬约束：库内存在时不许让大模型的结果盖过来。"""

    from fastapi.testclient import TestClient

    from app.api.deps import get_generator

    app.dependency_overrides[get_generator] = lambda: stub_generator
    with TestClient(app) as client:
        body = client.post("/api/recipes/resolve", json={"dish": DISH_LIBRARY_NAME}).json()
    assert body["source"] == "library"
    assert stub_generator.calls == []


def test_resolve_ignores_retired_preferences_field(app, stub_generator):
    """口味偏好已从契约里去掉：老客户端多送的字段按忽略处理，不影响解析。"""

    from fastapi.testclient import TestClient

    from app.api.deps import get_generator

    app.dependency_overrides[get_generator] = lambda: stub_generator
    with TestClient(app) as client:
        body = client.post(
            "/api/recipes/resolve",
            json={"dish": DISH_UNCOVERED_NAME, "preferences": ["不吃辣"]},
        ).json()

    assert body["source"] == "generated"
    assert stub_generator.calls == [DISH_UNCOVERED_NAME]


def test_resolve_rejects_empty_dish(client):
    response = client.post("/api/recipes/resolve", json={"dish": ""})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == 40001


# --------------------------------------------------------------- 类别清单

def test_dishes_reports_class_space_and_library_coverage(client):
    body = client.get("/api/dishes").json()
    assert body["class_count"] == 208
    # 接口测试的 models_dir 指向空目录，正式清单不在；承接情况来自种子数据，与清单无关
    assert body["labels_available"] is False
    assert body["library_recipe_count"] == LIBRARY_RECIPE_COUNT
    assert body["covered_class_count"] == len(body["items"]) == COVERED_CLASS_COUNT
    item = next(entry for entry in body["items"] if entry["index"] == DISH_LIBRARY_INDEX)
    assert item["library_recipe_id"] == DISH_LIBRARY_ID
    assert item["provisional_name"] is True
    # 清单缺席时名称退回演示图注（图注已与正式清单对齐，所以字面相同，但标记必须仍是临时）
    assert item["name"] == DISH_LIBRARY_NAME
    # 图注没覆盖的类别只能落到「类别 #N」占位
    placeholder = next(entry for entry in body["items"] if entry["index"] == DISH_PLACEHOLDER_INDEX)
    assert placeholder["provisional_name"] is True
    assert placeholder["name"] == f"类别 #{DISH_PLACEHOLDER_INDEX}"
    assert placeholder["library_recipe_id"] == DISH_PLACEHOLDER_RECIPE_ID


# --------------------------------------------------------------- 菜谱详情

def test_recipe_detail_shape(client):
    body = client.get(f"/api/recipes/{DISH_LIBRARY_ID}").json()
    assert body["recipe_id"] == DISH_LIBRARY_ID
    assert body["name"] == DISH_LIBRARY_NAME
    assert set(body) == {
        "recipe_id",
        "name",
        "cook_time_minutes",
        "difficulty",
        "rating",
        "servings",
        "description",
        "image_url",
        "origin",
        "required",
        "optional",
        "steps",
        "video_url",
    }
    assert body["steps"] and body["required"]
    assert body["origin"] == "seed"


def test_recipe_detail_serves_promoted_recipe_without_rating(app, stub_generator):
    """生成后入库的菜谱没有评分，详情接口不能因为 rating 为空而报错。"""

    from fastapi.testclient import TestClient

    from app.api.deps import get_generator

    app.dependency_overrides[get_generator] = lambda: stub_generator
    with TestClient(app) as client:
        client.post("/api/recipes/resolve", json={"dish": DISH_UNCOVERED_NAME})
        body = client.get(f"/api/recipes/{PROMOTED_RECIPE_ID}").json()
    assert body["name"] == DISH_UNCOVERED_NAME
    assert body["origin"] == "generated"
    assert body["rating"] is None
    assert body["steps"] and body["required"]


def test_promoted_recipe_closes_a_known_coverage_gap(app, stub_generator):
    """落库顺带把显式留白的类别补上：可承接类别 50 → 51，库内菜谱 75 → 76。"""

    from fastapi.testclient import TestClient

    from app.api.deps import get_generator

    app.dependency_overrides[get_generator] = lambda: stub_generator
    with TestClient(app) as client:
        client.post(
            "/api/recipes/resolve",
            json={"dish": DISH_UNCOVERED_NAME, "index": DISH_UNCOVERED_INDEX},
        )
        body = client.get("/api/dishes").json()
    assert body["library_recipe_count"] == LIBRARY_RECIPE_COUNT + 1
    assert body["covered_class_count"] == COVERED_CLASS_COUNT + 1
    item = next(e for e in body["items"] if e["index"] == DISH_UNCOVERED_INDEX)
    assert item["library_recipe_id"] == PROMOTED_RECIPE_ID


def test_recipe_detail_404_for_unknown_id(client):
    response = client.get("/api/recipes/999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == 40401


# --------------------------------------------------------------- 退役接口

def test_retired_recommend_recommendations_endpoint_is_gone(client):
    response = client.post("/api/recommendations", json={"ingredients": ["番茄"]})
    assert response.status_code == 404


def test_retired_ingredients_endpoint_is_gone(client):
    assert client.get("/api/ingredients").status_code == 404


# --------------------------------------------------------------- 落库副作用

def test_seed_is_idempotent_across_restarts(app):
    """种子数据重复灌入不应产生重复行（设计方案 6.4）。"""

    from app.models import DishRecipeMap, Recipe
    from app.services.seed import seed_database
    from app.core.database import session_scope

    with session_scope() as db:
        before = db.scalar(select(func.count()).select_from(Recipe))
        summary = seed_database(db)
    assert summary["inserted"] is False
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Recipe)) == before
        assert db.scalar(select(func.count()).select_from(DishRecipeMap)) == DISH_MAP_ROWS
