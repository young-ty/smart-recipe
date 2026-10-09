"""模型资产与类别映射的数据契约测试（设计方案 7.1、7.2）。

只读 JSON 资产，不需要 91 MB 权重，因此可以在任何环境跑。
真实权重的加载与推理见 test_model_integration.py。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.recognizer import ClassLabels, load_class_labels

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = REPO_ROOT / "models"
DATA_DIR = REPO_ROOT / "backend" / "app" / "data"

DISH_MANIFEST = MODELS_DIR / "dish.manifest.json"
LEGACY_LABELS = MODELS_DIR / "labels.json"
DISH_LABELS = MODELS_DIR / "dish_labels.json"
CAPTIONS = DATA_DIR / "dish_captions.json"
DISH_MAP = DATA_DIR / "seed_dish_map.json"
LABELS_CSV = MODELS_DIR / "dish_labels.csv"

CLASS_COUNT = 208

# 设计方案 2.4 的基准用例：4 张演示图的期望 top-1（截图里的数字就是这几个）。
# 2026-09-20 换成第二代 convnext 权重后，这两张图的判定变了：
# 莴笋炒虾仁 #121 → #100 莴笋肉丝；土豆肉末拌面 #157 → #156 担担面。
DEMO_TOP1 = {77: 0.885, 100: 0.643, 131: 0.709, 156: 0.459}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_dish_manifest_records_contract():
    manifest = _load(DISH_MANIFEST)
    assert manifest["arch"] == "convnext_base.fb_in22k_ft_in1k"
    assert manifest["arch_source"] == "timm", "timm 预训练权重，重建必须走 timm"
    assert manifest["num_classes"] == CLASS_COUNT
    assert manifest["onnx_file"] == "dish.onnx"
    assert len(manifest["onnx_sha256"]) == 64
    assert manifest["input"]["shape"] == ["batch", 3, 288, 288]
    assert manifest["output"]["shape"] == ["batch", CLASS_COUNT]


def test_dish_manifest_records_preprocess_contract():
    preprocess = _load(DISH_MANIFEST)["preprocess"]
    assert preprocess["input_size"] == [3, 288, 288]
    assert preprocess["interpolation"] == "bilinear", "训练说明写的是 Image.BILINEAR"
    assert preprocess["crop_pct"] == 0.875
    # 短边缩放目标 = int(input / crop_pct)：288 / 0.875 = 329，训练说明里的 329 就是这么来的
    assert preprocess["resize_short_side"] == int(preprocess["input_size"][-1] / preprocess["crop_pct"]) == 329
    assert preprocess["crop_mode"] == "center"
    assert preprocess["mean"] == [0.485, 0.456, 0.406]
    assert preprocess["std"] == [0.229, 0.224, 0.225]
    assert preprocess["layout"] == "NCHW"
    assert preprocess["color_order"] == "RGB"


def test_manifest_points_at_the_delivered_labels():
    """权重里没有类别名，所以资产里必须写清名称从哪来，别让后来人以为能从模型还原。"""

    assert "dish_labels.json" in _load(DISH_MANIFEST)["labels"]
    assert _load(DISH_MANIFEST)["num_classes"] == CLASS_COUNT


def test_manifest_records_the_delivered_onnx_and_the_superseded_weight():
    """换成第二代权重后，两代资产都要有出处：在用的记校验和，退役的留档可追溯。"""

    manifest = _load(DISH_MANIFEST)
    assert manifest["source_kind"] == "onnx_delivered", "第二代是直接给 ONNX 的，没有 .pt 转换环节"
    assert manifest["source_sha256"] == manifest["onnx_sha256"], "交付件与本仓库权重应是同一份字节"
    assert manifest["onnx_size_bytes"] == manifest["source_size_bytes"] == 351270213

    superseded = manifest["superseded"]
    assert superseded["arch"] == "resnet50d"
    assert superseded["onnx_file"] == "dish-resnet50d-208.onnx"
    retired = MODELS_DIR / superseded["onnx_file"]
    if retired.is_file():
        import hashlib

        digest = hashlib.sha256(retired.read_bytes()).hexdigest()
        assert digest == superseded["onnx_sha256"], "留档权重的校验和与 manifest 不一致"


def test_settings_point_at_the_dish_assets_not_the_retired_ones():
    settings = Settings()
    assert settings.models_dir == MODELS_DIR
    assert settings.dish_onnx_path == MODELS_DIR / "dish.onnx"
    assert settings.dish_manifest_path == DISH_MANIFEST
    assert settings.dish_labels_path == DISH_LABELS
    assert settings.dish_labels_path != LEGACY_LABELS, "新清单不能与旧方向的名字混用"
    assert not LEGACY_LABELS.exists(), "旧方向的类别清单已随该方向的权重一起清理"


def test_retired_ingredient_assets_are_cleaned_up():
    """v1.3 旧方向（FoodInsSeg 103 类食材分割）的权重与交付元数据已清理。

    models/ 只保留成品菜这条线的资产：在用的 dish.onnx 与第一代留档 best.pt、
    dish-resnet50d-208.onnx。旧方向的分析结论不再随仓库分发。
    """

    for name in ("best.onnx", "labels.json", "preprocess.json", "model_manifest.json"):
        assert not (MODELS_DIR / name).exists(), "旧方向资产又回到 models/：%s" % name


def test_captions_cover_the_demo_classes_and_the_registered_gaps():
    """图注只作降级兜底，但要盖住两类下标：演示图当前判到的类别，以及承接表里登记为缺口的类别。

    前者保证清单缺失时演示流程还能显示菜名，后者保证「既没菜谱也没名字」的类别不存在。
    """

    captions = _load(CAPTIONS)
    dish_map = _load(DISH_MAP)
    keys = {int(key) for key in captions}

    assert set(DEMO_TOP1) <= keys, "演示图判到的类别必须能兜底命名"
    assert keys <= {int(key) for key in dish_map}, "图注只能覆盖承接表登记过的类别"

    uncovered = {int(key) for key, entry in dish_map.items() if entry.get("recipe_id") is None}
    assert uncovered <= keys, "登记为库内无菜谱的类别必须同时有图注名"

    assert captions["77"] == "红烧肉"
    assert captions["156"] == "担担面"


def test_delivered_labels_resolve_without_a_provisional_mark():
    """清单已交付：解析出来的是正式名，不能再带待确认标记。"""

    labels = load_class_labels(Settings(data_dir=DATA_DIR, models_dir=MODELS_DIR))
    assert labels.available is True
    assert labels.source == "labels"
    for index in DEMO_TOP1:
        name, provisional = labels.resolve(index)
        assert name
        assert provisional is False, f"#{index} 已经拿到正式名，不该再标待确认"


def test_labels_outside_the_delivered_range_still_fall_back_to_placeholder():
    """清单没有覆盖到的下标仍退回占位名，而不是编一个出来。"""

    labels = load_class_labels(Settings(data_dir=DATA_DIR, models_dir=MODELS_DIR))
    name, provisional = labels.resolve(CLASS_COUNT + 5)
    assert name == f"类别 #{CLASS_COUNT + 5}"
    assert provisional is True


def test_delivered_labels_file_is_complete():
    payload = _load(DISH_LABELS)
    assert sorted(int(key) for key in payload) == list(range(CLASS_COUNT))
    names = [payload[str(index)] for index in range(CLASS_COUNT)]
    assert all(name.strip() for name in names)
    assert len(set(names)) == CLASS_COUNT, "类别名存在重复，顺序可能错位"


def test_delivered_labels_match_the_handed_over_csv():
    """JSON 是从训练产出的 CSV 转出来的，两处必须逐项一致。

    转换脚本是 backend/tools/import_labels.py；这里再转一次做交叉校验，
    防止有人手改了 JSON 却忘了改 CSV（或反过来）。
    """

    if not LABELS_CSV.is_file():
        pytest.skip("原始 CSV 未随仓库保存：%s" % LABELS_CSV)

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from tools.import_labels import parse_csv  # noqa: E402

    parsed, _english = parse_csv(LABELS_CSV.read_text(encoding="utf-8-sig"))
    assert {str(index): name for index, name in sorted(parsed.items())} == _load(DISH_LABELS)


def test_captions_are_synced_with_the_delivered_labels():
    """图注只作降级兜底（清单缺失时才用），取值必须与正式清单一致。"""

    captions = _load(CAPTIONS)
    labels = _load(DISH_LABELS)
    for index, name in captions.items():
        assert labels[index] == name, f"#{index} 的图注与正式清单不一致"


def test_dish_map_covers_the_demo_classes_and_stays_in_range():
    dish_map = _load(DISH_MAP)
    indices = sorted(int(key) for key in dish_map)
    assert all(0 <= index < CLASS_COUNT for index in indices)
    assert set(DEMO_TOP1) <= set(indices), "演示用的 4 个类别都要在承接表里显式登记"
    assert dish_map["77"]["recipe_id"] == 109


def test_dish_map_recipe_ids_exist_in_the_seed_library():
    known = {item["id"] for item in _load(DATA_DIR / "seed_recipes.json")}
    for index, entry in _load(DISH_MAP).items():
        recipe_id = entry.get("recipe_id")
        if recipe_id is not None:
            assert recipe_id in known, f"类别 #{index} 承接了不存在的菜谱 {recipe_id}"


def test_uncovered_classes_are_recorded_instead_of_dropped():
    """设计方案 7.1：缺口要显式记录，而不是静默丢弃。"""

    dish_map = _load(DISH_MAP)
    uncovered = [key for key, entry in dish_map.items() if entry.get("recipe_id") is None]
    assert uncovered, "生成分支的输入集合必须能在承接表里查出来"
    for key in uncovered:
        index = int(key)
        assert str(index) in _load(CAPTIONS), f"类别 #{index} 既没菜谱也没图注名"


def test_coverage_follows_the_labels_and_the_library():
    """承接表里出现 recipe_id 的类别，只允许有两条来路：

    1. 类名与库内菜名**精确相同**——直接登记；
    2. 类名与库内菜名是同一道菜的不同叫法（西红柿/番茄、茸/蓉 这类）——除了登记 recipe_id，
       还必须把**类别名本身**写进 alias，这样识别出的名字与用户手输都能命中同一道菜
       （resolver 的 _by_alias 就是照这个键查的）。

    除此之外不许有别的 recipe_id：手塞的行会让覆盖数虚高，而识别命中的是另一道菜。
    """

    labels = _load(DISH_LABELS)
    library_names = {item["name"] for item in _load(DATA_DIR / "seed_recipes.json")}
    exact = {index for index, name in labels.items() if name in library_names}

    dish_map = _load(DISH_MAP)
    covered = {
        key for key, entry in dish_map.items() if entry.get("recipe_id") is not None
    }
    assert exact <= covered, "清单里精确同名的类别必须都登记上"

    for key in sorted(covered - exact, key=int):
        alias = dish_map[key].get("alias")
        assert alias, "类别 #%s 的类名与库内菜名不同，必须用 alias 登记它的叫法" % key
        assert alias == labels.get(key), (
            "类别 #%s 的 alias 必须就是类别清单里的名字，否则识别结果命不中" % key
        )
    assert len(covered) < CLASS_COUNT, "208 类不可能全部落库，缺口要留在承接表里"


def test_dish_map_aliases_are_unambiguous():
    """alias 是检索键，重复或与库内菜名指向不同菜谱都会让结果不确定。"""

    recipes = _load(DATA_DIR / "seed_recipes.json")
    dish_map = _load(DISH_MAP)
    aliases = [entry["alias"] for entry in dish_map.values() if entry.get("alias")]
    assert len(aliases) == len(set(aliases)), "alias 重复：%s" % aliases

    by_name = {item["name"]: item["id"] for item in recipes}
    for key, entry in dish_map.items():
        alias = entry.get("alias")
        if not alias or alias not in by_name:
            continue
        assert by_name[alias] == entry["recipe_id"], (
            "类别 #%s 的 alias「%s」在库内指向另一道菜，检索会命中错的那条" % (key, alias)
        )


def test_class_labels_helper_round_trips_the_caption_file():
    labels = ClassLabels({}, {77: "红烧肉"})
    assert labels.resolve(78)[0].startswith("类别 #")
