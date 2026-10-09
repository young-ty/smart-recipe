"""菜谱检索与生成分流测试（设计方案 7.4、7.5）。

重点守四条硬约束：
1. 先查库、后生成——库内存在时绝不调用大模型；
2. 类别名缺失时按类别下标的承接关系仍能命中库内菜谱；
3. 生成层不可用是正常降级路径，不是错误；
4. 生成成功的菜谱要落回菜谱库（origin=generated），第二次同名请求直接命中库内。
"""

from __future__ import annotations

import hashlib
import urllib.parse

import pytest
from sqlalchemy import select

from app.models import DishRecipeMap, GeneratedRecipe, Recipe
from app.services.generator import GeneratedRecipePayload
from app.services.recipe_resolver import (
    SOURCE_GENERATED,
    SOURCE_LIBRARY,
    SOURCE_UNAVAILABLE,
    UNAVAILABLE_REASON,
    build_video_url,
    cache_key,
    dish_key,
    ingredient_key,
    is_placeholder,
    normalize_dish,
    recipe_view,
    RecipeResolver,
)

LIBRARY_DISH = "红烧肉"
LIBRARY_ID = 109
LIBRARY_INDEX = 77
UNCOVERED_DISH = "土豆肉末拌面"
UNCOVERED_INDEX = 157

# seed_dish_map.json 里的 9 条别名映射：类别名与库内菜名不是同一个写法。
# 左侧是识别/手输会给出的名字，右侧是库内那道菜——两边都要能对上。
ALIAS_CASES = (
    ("炒青菜", 19, 124, "清炒青菜"),
    ("芹菜香干", 47, 138, "芹菜炒香干"),
    ("西红柿炒鸡蛋", 50, 101, "番茄炒蛋"),
    ("西红柿牛腩", 80, 103, "番茄牛腩"),
    ("土豆炖牛腩", 81, 113, "土豆炖牛肉"),
    ("蒜茸粉丝蒸虾", 120, 111, "蒜蓉粉丝蒸虾"),
    ("西红柿鸡蛋面", 152, 116, "番茄鸡蛋面"),
    ("西红柿鸡蛋汤", 180, 102, "番茄蛋花汤"),
    ("鱿鱼", 125, 134, "韭菜炒鱿鱼"),
)


@pytest.fixture()
def resolver(session, settings):
    return RecipeResolver(session, settings)


@pytest.fixture()
def no_promote_settings(settings):
    """关掉落库开关的配置：库里不留痕，重复请求只能靠缓存。"""

    return type(settings)(**{**settings.__dict__, "promote_generated": False})


# --------------------------- 归一化 ---------------------------

def test_normalize_strips_spaces_and_full_width():
    assert normalize_dish("  红烧肉  ") == LIBRARY_DISH
    assert normalize_dish("红烧肉") == LIBRARY_DISH
    assert normalize_dish("  土豆 肉末 拌面 ") == UNCOVERED_DISH
    assert normalize_dish("ＡＢＣ") == "ABC", "全角字母要折成半角"


def test_normalize_drops_noise_suffixes():
    for noisy in ("红烧肉的做法", "红烧肉做法", "红烧肉怎么做", "红烧肉的菜谱"):
        assert normalize_dish(noisy) == LIBRARY_DISH


def test_normalize_keeps_short_names_intact():
    assert normalize_dish("菜谱") == "菜谱", "整名就是后缀时不能削成空串"
    assert normalize_dish("") == ""
    assert normalize_dish(None) == ""


def test_normalize_drops_control_and_zero_width_characters():
    """手输菜名可能夹带看不见的字符：它们会污染缓存键，也没法当作菜名送进模型。"""

    assert normalize_dish("红烧\u200b肉") == LIBRARY_DISH
    assert normalize_dish("红烧\u0000肉") == LIBRARY_DISH
    assert normalize_dish("\ufeff红烧肉") == LIBRARY_DISH


def test_dish_key_folds_synonyms_but_ingredient_key_does_not():
    """同义折叠只用于菜名之间比对：食材名折了会让字典条目对不上（鸡蛋 ≠ 蛋）。"""

    assert dish_key("薯仔丝饼") == "土豆丝饼"
    assert ingredient_key("鸡蛋") == "鸡蛋"
    assert ingredient_key("马铃薯") == "马铃薯"


# --------------------------- 同义写法召回 ---------------------------

def test_synonym_writing_hits_the_library_without_generating(session, settings, stub_generator):
    """手输「薯仔丝饼」要命中库内的「土豆丝饼」：写法不同不该逼出一次生成。"""

    result = RecipeResolver(session, settings, stub_generator).resolve("薯仔丝饼")

    assert result.source == SOURCE_LIBRARY
    assert result.recipe.name == "土豆丝饼"
    assert stub_generator.calls == [], "库里能命中就不许调用大模型"


def test_synonym_writing_is_not_treated_as_a_wrong_answer(session, settings, make_stub_generator):
    """模型把「薯仔炖牛腩」写成「土豆炖牛腩」是同一道菜的写法差异，不算答非所问。"""

    payload = GeneratedRecipePayload(
        name="土豆炖牛腩",
        cook_time_minutes=90,
        difficulty="中等",
        required=[{"name": "牛腩", "amount": "600 克"}, {"name": "土豆", "amount": "2 个"}],
        steps=["牛腩切块冷水下锅焯透", "下土豆块与调料小火炖 60 分钟", "收汁后撒葱花上桌"],
    )
    result = RecipeResolver(session, settings, make_stub_generator(payload)).resolve("薯仔炖牛腩")

    assert result.source == SOURCE_GENERATED
    assert result.degraded is False


def test_placeholder_detection():
    assert is_placeholder("类别 #77")
    assert is_placeholder("类别#77")
    assert not is_placeholder(LIBRARY_DISH)
    assert not is_placeholder("")


# --------------------------- 缓存键与视频链接 ---------------------------

def test_cache_key_depends_on_dish_and_prompt_version():
    assert cache_key(LIBRARY_DISH, "v1") == cache_key(LIBRARY_DISH, "v1")
    assert cache_key(LIBRARY_DISH, "v1") != cache_key(LIBRARY_DISH, "v2")
    assert cache_key(LIBRARY_DISH, "v1") == hashlib.sha1(
        f"{LIBRARY_DISH}v1".encode("utf-8")
    ).hexdigest()


def test_video_url_is_built_from_dish_name(settings):
    keyword = urllib.parse.quote(f"{LIBRARY_DISH} 做法")
    assert build_video_url(LIBRARY_DISH, settings) == (
        f"https://search.bilibili.com/all?keyword={keyword}"
    )

# --------------------------- 库内分支 ---------------------------

def test_resolve_by_name_hits_library(resolver):
    result = resolver.resolve(LIBRARY_DISH)
    assert result.source == SOURCE_LIBRARY
    assert result.degraded is False
    assert result.recipe.id == LIBRARY_ID
    assert result.recipe.steps and result.recipe.required
    assert result.recipe.video_url.startswith("https://search.bilibili.com/all?keyword=")


def test_resolve_by_class_index_hits_library(resolver):
    result = resolver.resolve(f"类别 #{LIBRARY_INDEX}", LIBRARY_INDEX)
    assert result.source == SOURCE_LIBRARY
    assert result.recipe.id == LIBRARY_ID


def test_resolve_by_alias_hits_library(session, settings):
    session.add(DishRecipeMap(class_index=200, recipe_id=LIBRARY_ID, alias="东坡肉"))
    session.commit()
    result = RecipeResolver(session, settings).resolve("东坡肉")
    assert result.source == SOURCE_LIBRARY
    assert result.recipe.id == LIBRARY_ID


def test_seeded_alias_mappings_resolve_by_name(resolver):
    """手输或口述这个名字时要命中那道库内菜——别名通道不能只在数据里好看。"""

    for dish, _index, recipe_id, recipe_name in ALIAS_CASES:
        result = resolver.resolve(dish)
        assert result.source == SOURCE_LIBRARY, "%s 应命中库内" % dish
        assert result.recipe.id == recipe_id
        assert result.recipe.name == recipe_name
        assert result.degraded is False


def test_seeded_alias_mappings_resolve_by_class_index(resolver):
    """识别链路给的是类别下标 + 类别名，两条路都要落到同一道菜。"""

    for dish, index, recipe_id, _name in ALIAS_CASES:
        result = resolver.resolve(dish, index)
        assert result.source == SOURCE_LIBRARY, "类别 #%d 应命中库内" % index
        assert result.recipe.id == recipe_id


def test_seeded_alias_cases_cover_every_alias_in_the_table(resolver, session):
    """上面那份名单必须与承接表里的别名一一对应，漏一条就等于少了条用例。"""

    rows = session.scalars(
        select(DishRecipeMap).where(DishRecipeMap.alias.is_not(None))
    ).all()
    assert {row.alias for row in rows} == {case[0] for case in ALIAS_CASES}


def test_resolve_index_without_recipe_falls_through(session, settings):
    """承接表里 recipe_id 为 NULL 的行是生成分支的输入集合，不能当成命中。"""

    result = RecipeResolver(session, settings).resolve(UNCOVERED_DISH, UNCOVERED_INDEX)
    assert result.source == SOURCE_UNAVAILABLE
    assert result.recipe is None


def test_recipe_view_carries_library_fields(session, settings):
    from app.models import Recipe

    recipe = session.get(Recipe, LIBRARY_ID)
    view = recipe_view(recipe, settings)
    assert view.id == LIBRARY_ID
    assert view.name == recipe.name
    assert view.rating == recipe.rating
    assert view.origin == "seed", "人工整理的种子菜谱要能被前端认出来"
    assert [line.name for line in view.required] == [
        link.ingredient.name for link in recipe.ingredients if link.is_required
    ]


# --------------------------- 生成分支 ---------------------------

def test_generated_recipe_is_promoted_and_cached(session, settings, stub_generator):
    """生成成功即落库：本次仍按生成结果回答，库里同时留下一条 origin=generated 的菜谱。"""

    resolver = RecipeResolver(session, settings, stub_generator)
    result = resolver.resolve(UNCOVERED_DISH, UNCOVERED_INDEX)
    assert result.source == SOURCE_GENERATED
    assert result.degraded is False
    assert result.recipe.id == settings.promoted_recipe_id_base
    assert result.recipe.rating is None
    assert result.recipe.origin == "generated"
    assert result.recipe.video_url == build_video_url(result.recipe.name, settings)

    promoted = session.get(Recipe, settings.promoted_recipe_id_base)
    assert promoted is not None
    assert promoted.name == UNCOVERED_DISH
    assert promoted.rating is None
    assert [step.content for step in promoted.steps] == list(stub_generator.payload.steps)
    expected_lines = {
        line.name for line in stub_generator.payload.required + stub_generator.payload.optional
    }
    assert {link.ingredient.name for link in promoted.ingredients} == expected_lines, (
        "主辅料都要挂到食材表上，否则详情页是个空壳"
    )
    assert session.get(DishRecipeMap, UNCOVERED_INDEX).recipe_id == promoted.id, (
        "原本显式留白的类别要由这条菜谱认领"
    )

    row = session.scalar(select(GeneratedRecipe))
    assert row is not None
    assert row.cache_key == cache_key(UNCOVERED_DISH, settings.prompt_version)
    assert row.dish == UNCOVERED_DISH
    assert row.model == "stub-generator"
    assert stub_generator.calls == [UNCOVERED_DISH]


def test_second_resolve_hits_the_promoted_library_row(session, settings, stub_generator):
    """落库之后第二次请求走库内分支：不再调用大模型，连缓存都不必碰。"""

    resolver = RecipeResolver(session, settings, stub_generator)
    first = resolver.resolve(UNCOVERED_DISH, UNCOVERED_INDEX)
    second = resolver.resolve(UNCOVERED_DISH, UNCOVERED_INDEX)
    assert second.source == SOURCE_LIBRARY
    assert second.recipe.id == first.recipe.id
    assert second.recipe.origin == "generated"
    assert stub_generator.calls == [UNCOVERED_DISH], "第二次应命中库内，不再调用大模型"


def test_promotion_can_be_switched_off(session, no_promote_settings, stub_generator):
    """关掉落库开关就只写缓存：库里不留痕，类别行也不会被认领。"""

    resolver = RecipeResolver(session, no_promote_settings, stub_generator)
    result = resolver.resolve(UNCOVERED_DISH, UNCOVERED_INDEX)
    assert result.source == SOURCE_GENERATED
    assert result.recipe.id is None
    assert result.recipe.origin == "generated"
    assert session.get(Recipe, no_promote_settings.promoted_recipe_id_base) is None
    assert session.get(DishRecipeMap, UNCOVERED_INDEX).recipe_id is None
    assert session.scalar(select(GeneratedRecipe)) is not None, "缓存仍要写，重复请求不再计费"


def test_promotion_skips_ingredients_outside_the_dictionary(
    session, settings, make_stub_generator
):
    """食材名对不上就跳过那一行：不能因为一样东西不认识就整条菜谱不落地。"""

    payload = GeneratedRecipePayload(
        name="外星炒饭",
        cook_time_minutes=10,
        difficulty="简单",
        required=[{"name": "面条", "amount": "200 克"}, {"name": "陨石", "amount": "1 块"}],
        steps=["热锅下油把饭炒散", "加陨石碎同炒出香", "起锅装盘撒上葱花"],
    )
    result = RecipeResolver(session, settings, make_stub_generator(payload)).resolve("外星炒饭")
    assert result.recipe.id == settings.promoted_recipe_id_base
    assert [line.name for line in result.recipe.required] == ["面条"], (
        "不认识的食材在当次返回前就被剔除，不能等到入库"
    )
    promoted = session.get(Recipe, result.recipe.id)
    assert [link.ingredient.name for link in promoted.ingredients] == ["面条"], "不认识的食材要跳过"


def test_promotion_maps_dry_goods_and_spices(session, settings, make_stub_generator):
    """梅干菜、腐乳、八角、香叶这类干货与香料也要在字典里，梅菜扣肉才不会落库成缺料的菜谱。"""

    payload = GeneratedRecipePayload(
        name="梅菜扣肉",
        cook_time_minutes=120,
        difficulty="中等",
        required=[
            {"name": "五花肉", "amount": "700 克"},
            {"name": "梅干菜", "amount": "80 克"},
        ],
        optional=[
            {"name": "八角", "amount": "2 颗"},
            {"name": "香叶", "amount": "2 片"},
            {"name": "腐乳", "amount": "1 块"},
        ],
        steps=["梅干菜泡发洗净", "五花肉焯水后上色", "上锅蒸 90 分钟"],
    )
    result = RecipeResolver(session, settings, make_stub_generator(payload)).resolve("梅菜扣肉")
    promoted = session.get(Recipe, result.recipe.id)
    assert {link.ingredient.name for link in promoted.ingredients} == {
        "五花肉",
        "梅干菜",
        "八角",
        "香叶",
        "腐乳",
    }


def test_promotion_matches_ingredient_aliases(session, settings, make_stub_generator):
    """别名也要能对上：模型写「梅菜」时不能当成陌生食材丢掉。"""

    payload = GeneratedRecipePayload(
        name="梅菜蒸肉饼",
        cook_time_minutes=40,
        difficulty="简单",
        required=[{"name": "梅菜", "amount": "80 克"}],
        steps=["梅干菜泡发后洗净剁碎", "肉末加调料摔打上劲", "铺上梅干菜上锅蒸 20 分钟"],
    )
    result = RecipeResolver(session, settings, make_stub_generator(payload)).resolve("梅菜蒸肉饼")
    promoted = session.get(Recipe, result.recipe.id)
    assert [link.ingredient.name for link in promoted.ingredients] == ["梅干菜"]


def test_promotion_does_not_invent_a_class_mapping(session, settings, make_stub_generator):
    """手输菜名没有对应类别行时只入库，不去凭空占用一个类别下标。"""

    payload = GeneratedRecipePayload(
        name="手输的新菜",
        cook_time_minutes=20,
        difficulty="简单",
        required=[{"name": "面条", "amount": "200 克"}],
        steps=["面条下锅煮熟后捞出", "浇上炒好的料汁", "拌匀后撒上葱花"],
    )
    result = RecipeResolver(session, settings, make_stub_generator(payload)).resolve(
        "手输的新菜", 999
    )
    assert result.recipe.id == settings.promoted_recipe_id_base
    assert session.get(DishRecipeMap, 999) is None


# --------------------------- 生成结果的当次闸门 ---------------------------

def test_generated_answer_must_name_the_requested_dish(session, settings, stub_generator):
    """答非所问一律降级：要「红烧肉」却返回一份别的菜，比说不出更糟。"""

    result = RecipeResolver(session, settings, stub_generator).resolve("手输的别的菜", 999)

    assert result.source == SOURCE_UNAVAILABLE
    assert result.degraded is True
    assert result.recipe is None
    assert result.reason == UNAVAILABLE_REASON
    assert session.scalar(select(GeneratedRecipe)) is None, "不合格的结果不写缓存"
    assert session.get(Recipe, settings.promoted_recipe_id_base) is None, "也不落库"


def test_generated_recipe_drops_unknown_ingredients_from_the_answer(
    session, settings, make_stub_generator
):
    """不在字典里的食材在当次返回前就剔除：当次看到的与入库后查到的是同一份。"""

    payload = GeneratedRecipePayload(
        name="陨石拌面",
        cook_time_minutes=15,
        difficulty="简单",
        required=[{"name": "面条", "amount": "200 克"}, {"name": "陨石", "amount": "1 块"}],
        optional=[{"name": "葱", "amount": "1 根"}, {"name": "月尘", "amount": "少许"}],
        steps=["面条下锅煮熟后捞出", "拌入调料与陨石碎", "撒上葱花即可上桌"],
    )
    resolver = RecipeResolver(session, settings, make_stub_generator(payload))

    first = resolver.resolve("陨石拌面")
    assert [line.name for line in first.recipe.required] == ["面条"]
    assert [line.name for line in first.recipe.optional] == ["葱"]

    again = resolver.resolve("陨石拌面")
    assert again.source == SOURCE_LIBRARY, "第一次就落了库，第二次直接命中"
    assert again.recipe == first.recipe, "当次返回与入库后查到的必须是同一份菜谱"


def test_generated_recipe_without_any_known_required_ingredient_is_rejected(
    session, settings, make_stub_generator
):
    """主料一样都对不上食材字典：这菜谱用不了，按失败处理，而不是给一份没主料的。"""

    payload = GeneratedRecipePayload(
        name="陨石汤",
        cook_time_minutes=30,
        difficulty="中等",
        required=[{"name": "陨石", "amount": "2 块"}],
        steps=["陨石洗净后下锅", "加水大火烧开", "转小火炖 40 分钟"],
    )
    result = RecipeResolver(session, settings, make_stub_generator(payload)).resolve("陨石汤")

    assert result.source == SOURCE_UNAVAILABLE
    assert result.degraded is True
    assert session.scalar(select(GeneratedRecipe)) is None


def test_loosely_written_ingredient_names_still_map_to_the_dictionary(
    session, settings, make_stub_generator
):
    """模型不会总写标准食材名：「干黄豆」「清水（打浆用）」也要能挂上字典。

    豆腐花那次就栽在这：生成的主料是干黄豆 / 清水（打浆用）/ 葡萄糖酸内酯，
    三样一样都挂不上，闸门整份判失败，用户看到的是「生成服务不可用」。
    """

    payload = GeneratedRecipePayload(
        name="陨石豆腐花",
        cook_time_minutes=40,
        difficulty="中等",
        required=[
            {"name": "干黄豆", "amount": "150 克"},
            {"name": "清水（打浆用）", "amount": "1200 毫升"},
            {"name": "葡萄糖酸内酯", "amount": "3 克"},
        ],
        steps=["黄豆泡发后打成豆浆并过滤", "豆浆煮沸后降到 85 度", "冲入内酯水静置 15 分钟"],
    )
    resolver = RecipeResolver(session, settings, make_stub_generator(payload))

    result = resolver.resolve("陨石豆腐花")

    assert result.source == SOURCE_GENERATED
    assert [line.name for line in result.recipe.required] == [
        "黄豆",
        "清水",
        "葡萄糖酸内酯",
    ]
    again = RecipeResolver(session, settings, make_stub_generator(payload)).resolve("陨石豆腐花")
    assert again.source == SOURCE_LIBRARY, "落库后同名请求直接命中库内，不再调生成"


def test_promotion_keeps_one_row_when_an_ingredient_repeats(session, settings, make_stub_generator):
    """主料与辅料写了同一样东西时只留一条：页面上不该出现两遍「葱」。"""

    payload = GeneratedRecipePayload(
        name="葱花拌面",
        cook_time_minutes=15,
        difficulty="简单",
        required=[{"name": "面条", "amount": "200 克"}, {"name": "葱", "amount": "1 根"}],
        optional=[{"name": "葱", "amount": "少许"}],
        steps=["面条下锅煮熟后捞出", "葱切花后用热油激香", "拌入面条即可上桌"],
    )
    first = RecipeResolver(session, settings, make_stub_generator(payload)).resolve("葱花拌面")

    assert [line.name for line in first.recipe.required] == ["面条", "葱"]
    assert first.recipe.optional == []
    promoted = session.get(Recipe, first.recipe.id)
    assert [link.ingredient.name for link in promoted.ingredients] == ["面条", "葱"]


def test_promotion_failure_still_answers_from_the_generated_result(
    session, settings, stub_generator, monkeypatch
):
    """落库是锦上添花：写库失败照样返回生成结果，只是库里没留下这条记录。"""

    def boom(*args, **kwargs):
        raise RuntimeError("库写不进去")

    resolver = RecipeResolver(session, settings, stub_generator)
    monkeypatch.setattr(resolver, "_insert_promoted", boom)

    result = resolver.resolve(UNCOVERED_DISH, UNCOVERED_INDEX)
    assert result.source == SOURCE_GENERATED
    assert result.degraded is False
    assert result.recipe.id is None, "没落库就没有 id"
    assert session.scalar(select(GeneratedRecipe)) is not None, "缓存仍要写，重复请求不再计费"


def test_stale_cache_with_a_wrong_name_is_treated_as_a_miss(session, settings, stub_generator):
    """缓存也要过闸：旧缓存里存着别的菜名时当未命中重新生成，否则一道菜两个口径。"""

    payload = GeneratedRecipePayload(
        name="名字对不上",
        cook_time_minutes=30,
        difficulty="简单",
        required=[{"name": "面条", "amount": "200 克"}],
        steps=["肉末下锅炒散出香", "加调料煮成浇头", "面条煮熟后拌入浇头"],
    )
    session.add(
        GeneratedRecipe(
            cache_key=cache_key(UNCOVERED_DISH, settings.prompt_version),
            dish=UNCOVERED_DISH,
            payload_json=payload.model_dump_json(),
            model="old-generator",
            prompt_version=settings.prompt_version,
        )
    )
    session.commit()

    result = RecipeResolver(session, settings, stub_generator).resolve(UNCOVERED_DISH)
    assert result.source == SOURCE_GENERATED
    assert result.recipe.name == UNCOVERED_DISH, "过闸后返回的是重新生成的结果"


def test_link_class_index_never_overwrites_a_mapped_class(session, settings):
    """已承接别的菜谱的类别行不能被后来的生成结果抢走。"""

    RecipeResolver(session, settings)._link_class_index(LIBRARY_INDEX, 12345)
    assert session.get(DishRecipeMap, LIBRARY_INDEX).recipe_id == LIBRARY_ID


def test_cache_key_changes_with_prompt_version(session, no_promote_settings, stub_generator):
    """换提示词版本就换缓存键：不能拿「版本号刚好等于新默认值」来验证这件事。"""

    RecipeResolver(session, no_promote_settings, stub_generator).resolve(UNCOVERED_DISH)
    session.commit()
    bumped_version = no_promote_settings.prompt_version + "-next"
    bumped = type(no_promote_settings)(
        **{**no_promote_settings.__dict__, "prompt_version": bumped_version}
    )
    result = RecipeResolver(session, bumped, stub_generator).resolve(UNCOVERED_DISH)
    assert result.source == SOURCE_GENERATED
    assert stub_generator.calls == [UNCOVERED_DISH, UNCOVERED_DISH], "提示词换版本就该重新生成"


def test_cache_hit_is_promoted_too(session, settings, stub_generator):
    """历史缓存（落库能力上线前生成的）也要补进库，否则能不能进库全看它有没有旧缓存。"""

    from app.models import GeneratedRecipe

    payload = GeneratedRecipePayload(
        name=UNCOVERED_DISH,
        cook_time_minutes=30,
        difficulty="简单",
        required=[{"name": "面条", "amount": "200 克"}],
        steps=["肉末下锅炒散出香", "加调料煮成浇头", "面条煮熟后拌入浇头"],
    )
    session.add(
        GeneratedRecipe(
            cache_key=cache_key(UNCOVERED_DISH, settings.prompt_version),
            dish=UNCOVERED_DISH,
            payload_json=payload.model_dump_json(),
            model="old-generator",
            prompt_version=settings.prompt_version,
        )
    )
    session.commit()

    result = RecipeResolver(session, settings, stub_generator).resolve(
        UNCOVERED_DISH, UNCOVERED_INDEX
    )
    assert result.source == SOURCE_GENERATED
    assert result.recipe.id == settings.promoted_recipe_id_base, "缓存结果也要落库"
    assert result.recipe.origin == "generated"
    assert stub_generator.calls == [], "命中缓存不该调用大模型"
    assert session.get(Recipe, settings.promoted_recipe_id_base) is not None
    assert session.get(DishRecipeMap, UNCOVERED_INDEX).recipe_id == result.recipe.id


def test_stale_cache_is_treated_as_a_miss(session, settings):
    session.add(
        GeneratedRecipe(
            cache_key=cache_key(UNCOVERED_DISH, settings.prompt_version),
            dish=UNCOVERED_DISH,
            payload_json='{"name": "缺字段"}',
            model="old",
            prompt_version=settings.prompt_version,
        )
    )
    session.commit()
    result = RecipeResolver(session, settings).resolve(UNCOVERED_DISH)
    assert result.source == SOURCE_UNAVAILABLE, "老缓存解析不了就当未命中"


def test_resolve_degrades_without_generator(resolver):
    result = resolver.resolve(UNCOVERED_DISH, UNCOVERED_INDEX)
    assert result.source == SOURCE_UNAVAILABLE
    assert result.degraded is True
    assert result.reason


def test_library_wins_over_generator(session, settings, stub_generator):
    resolver = RecipeResolver(session, settings, stub_generator)
    result = resolver.resolve(LIBRARY_DISH, LIBRARY_INDEX)
    assert result.source == SOURCE_LIBRARY
    assert stub_generator.calls == []


def test_placeholder_is_never_sent_to_the_model(session, settings, stub_generator):
    result = RecipeResolver(session, settings, stub_generator).resolve("类别 #99", 99)
    assert result.source == SOURCE_UNAVAILABLE
    assert stub_generator.calls == [], "占位菜名没有语义，不能拿去问大模型"


def test_blank_dish_is_unavailable(resolver):
    result = resolver.resolve("   ")
    assert result.source == SOURCE_UNAVAILABLE
    assert result.recipe is None
