# -*- coding: utf-8 -*-
"""生成 prototype/dish.html —— 成品菜识别原型页。

页面上的候选和置信度不是手抄的，是现场跑 models/dish.onnx 算出来的；
能从菜谱库对上的那道菜（红烧肉）也是从 backend/app/data 的种子数据里读的。
所以这个脚本是"页面上的数字从哪来"的唯一答案，换了模型或改了菜谱库就重跑：

    .venv/Scripts/python.exe -m prototype.build_dish_prototype

208 类的类别清单已经就位（models/dish_labels.json，由训练产出的 CSV 转换而来），
所以页面上的菜名全部是真实类别名，不再有"待确认"占位。
tests/test_dish_prototype.py 守着三条：页面的候选与置信度等于模型现场输出、
类别名等于清单、库内能承接的候选直接给库内菜谱。
"""

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prototype.preprocess import (  # noqa: E402
    MODEL_PATH,
    create_session,
    manifest,
    measure_latency_ms,
    predict_topk,
)

PROTOTYPE_DIR = ROOT / "prototype"
OUTPUT_PATH = PROTOTYPE_DIR / "dish.html"
DATA_DIR = ROOT / "backend" / "app" / "data"
LABELS_PATH = ROOT / "models" / "dish_labels.json"

# 演示图。图片就放在 demo/dishes/，页面引用相对路径，离线打开也能显示。
# cluster_note 标记的那张图，模型在前三名上几乎分不出高下，需要额外解释一句。
PHOTOS = [
    {"id": "braised-pork", "file": "../demo/dishes/dish-braised-pork.jpg",
     "caption": "红烧肉", "library_recipe_id": 109},
    {"id": "omurice", "file": "../demo/dishes/dish-omurice.jpg",
     "caption": "蛋包饭"},
    {"id": "shrimp-greens", "file": "../demo/dishes/dish-shrimp-greens.jpg",
     "caption": "虾仁炒青笋"},
    {"id": "noodle-potato", "file": "../demo/dishes/dish-noodle-potato.jpg",
     "caption": "土豆肉末拌面", "cluster_note": True},
]

# 菜谱库里没有承接的菜，静态原型只能用演示菜谱顶上，页面上会注明"菜谱库暂缺"。
# FR-13（识别得到、库里没有的菜由大模型生成做法）已在正式前端落地并会把结果写回库内，
# 但本页是离线快照、跑不了大模型，所以这里只能替代成写死的演示菜谱。
# 注意：菜名必须等于模型给这张图判的 top-1 类别名（见 _demo_recipe 的校验），
# 换了模型就得回来改这里的菜谱内容，否则候选栏说 A、菜谱栏写 B。
FALLBACK_RECIPES = {
    "omurice": {
        "name": "蛋包饭", "inLibrary": False, "recipeId": None,
        "cookTimeMinutes": 25, "difficulty": "中等", "rating": 4.6, "servings": 1,
        "description": "蛋皮要摊得薄，炒饭先放凉更好炒散。",
        "required": [["鸡蛋", "3 个"], ["米饭", "1 碗"], ["卷心菜", "100 克"]],
        "optional": [["胡萝卜", "半根"], ["番茄酱", "2 勺"], ["盐", "适量"]],
        "steps": ["卷心菜与胡萝卜切丝，热油炒软",
                  "下米饭炒散，加盐调味后盛出",
                  "鸡蛋打散摊成蛋皮，边缘凝固即可",
                  "蛋皮盖在炒饭上，淋番茄酱"],
        "source": "菜谱库暂缺，演示菜谱（正式前端由 FR-13 生成后入库）",
    },
    "shrimp-greens": {
        "name": "莴笋肉丝", "inLibrary": False, "recipeId": None,
        "cookTimeMinutes": 15, "difficulty": "简单", "rating": 4.7, "servings": 2,
        "description": "肉丝上浆才不柴，莴笋最后大火快炒保持脆感。",
        "required": [["莴笋", "1 根"], ["猪里脊", "150 克"]],
        "optional": [["蒜", "2 瓣"], ["淀粉", "1 勺"], ["盐", "适量"]],
        "steps": ["里脊切细丝，加淀粉与少许清水抓匀腌 10 分钟",
                  "莴笋切丝，沸水焯 20 秒捞出过凉",
                  "热油下肉丝滑散至变色",
                  "下莴笋与蒜末大火快炒，加盐调味"],
        "source": "菜谱库暂缺，演示菜谱（正式前端由 FR-13 生成后入库）",
    },
    "noodle-potato": {
        "name": "担担面", "inLibrary": False, "recipeId": None,
        "cookTimeMinutes": 25, "difficulty": "简单", "rating": 4.5, "servings": 2,
        "description": "臊子要炒得干香，拌面时再补一勺红油。",
        "required": [["面条", "200 克"], ["猪肉末", "100 克"], ["芽菜", "30 克"]],
        "optional": [["花生碎", "2 勺"], ["辣椒油", "2 勺"], ["葱花", "适量"]],
        "steps": ["肉末下锅炒散，加芽菜与生抽炒成干香臊子",
                  "碗底调芝麻酱、辣椒油、生抽，兑一勺面汤",
                  "面条煮熟捞入碗中",
                  "铺上臊子，撒花生碎与葱花拌匀"],
        "source": "菜谱库暂缺，演示菜谱（正式前端由 FR-13 生成后入库）",
    },
}


def _seed(file_name):
    return json.loads((DATA_DIR / file_name).read_text(encoding="utf-8"))


def class_labels():
    """类别名一律取自训练产出的清单。

    清单缺失时直接失败，不再退化成「类别 #N」：原型的价值就是所见即所得，
    一份名字对不上号的快照比没有快照更容易误导。
    """

    if not LABELS_PATH.is_file():
        raise SystemExit(
            "缺少类别清单 %s；先跑 backend/tools/import_labels.py "
            "\"ONNX/Food list - Menu.csv\"" % LABELS_PATH
        )
    payload = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    return {int(key): value for key, value in payload.items()}


def library_recipe_for_class(index):
    """类别承接表里登记了菜谱的类别，直接给库内菜谱。

    和后端 /api/recipes/resolve 的库内分支同源（先查 dish_recipe_map 再取菜谱），
    所以页面上点候选看到的菜谱与接口返回的是同一条记录。
    """

    entry = _seed("seed_dish_map.json").get(str(index))
    if not entry or entry.get("recipe_id") is None:
        return None
    return library_recipe(entry["recipe_id"])


def library_recipe(recipe_id):
    """把菜谱库的种子数据翻成页面要的结构（食材 id 换成名称）。"""

    recipes = _seed("seed_recipes.json")
    names = {item["id"]: item["name"] for item in _seed("seed_ingredients.json")}
    recipe = next((item for item in recipes if item.get("id") == recipe_id), None)
    if recipe is None:
        raise SystemExit("菜谱库 seed_recipes.json 里没有 id=%s" % recipe_id)

    def pairs(entries):
        return [[names[entry["id"]], entry["amount"]] for entry in entries]

    return {
        "name": recipe["name"],
        "inLibrary": True,
        "recipeId": recipe["id"],
        "cookTimeMinutes": recipe["cook_time_minutes"],
        "difficulty": recipe["difficulty"],
        "rating": recipe["rating"],
        "servings": recipe["servings"],
        "description": recipe["description"],
        "required": pairs(recipe["required"]),
        "optional": pairs(recipe["optional"]),
        "steps": list(recipe["steps"]),
        "source": "菜谱库 seed_recipes.json #%d" % recipe["id"],
    }


def cluster_note(topk, labels):
    """前三名置信度咬在一起时的提示；清单到手后可以直接点出是哪几个类别。"""

    ranks = " / ".join("%s（#%d）" % (labels.get(index, "?"), index) for index, _ in topk[:3])
    shares = " · ".join("%.0f%%" % (confidence * 100) for _, confidence in topk[:3])
    return ("前三名 %s 的置信度咬得很紧（%s）。三个类别都属于面条类，"
            "属于近义类别互相混淆，不是认成了不相干的菜。" % (ranks, shares))


def _demo_recipe(item, top1_index, labels):
    """这张图的菜谱：库内有承接就用库内，否则用演示菜谱。

    库外时**菜名必须等于模型判定的类别名**，不一致就直接失败：
    页面左栏说「莴笋肉丝」、右栏却写「清炒虾仁」，比没有演示菜谱更糟，
    而且换模型后最容易踩到，所以宁可让生成脚本当场报错。
    """

    library = library_recipe_for_class(top1_index)
    if library is not None:
        return library

    recipe = dict(FALLBACK_RECIPES[item["id"]])
    expected = labels.get(top1_index)
    if recipe["name"] != expected:
        raise SystemExit(
            "演示菜谱与模型判定不一致：%s 的 top-1 是 #%d %s，演示菜谱写的是「%s」；"
            "请更新 FALLBACK_RECIPES[%r]"
            % (item["id"], top1_index, expected, recipe["name"], item["id"])
        )
    return recipe


def build_demo_data():
    """现场跑一遍模型，组装页面上那块内嵌 JSON。"""

    meta = manifest()
    labels = class_labels()
    session = create_session(MODEL_PATH)
    photos = []
    for item in PHOTOS:
        image_path = (PROTOTYPE_DIR / item["file"]).resolve()
        if not image_path.is_file():
            raise SystemExit("演示图缺失：%s" % image_path)
        topk = predict_topk(session, image_path, k=5)
        photo = {
            "id": item["id"],
            "file": item["file"],
            "caption": item["caption"],
            "topk": [{"index": index, "confidence": round(value, 3)} for index, value in topk],
            # 每个候选都记下「这个类别库里有没有菜谱」，点候选即取，和后端一致
            "classRecipes": {
                str(index): library_recipe_for_class(index) for index, _ in topk
            },
            # top-1 的兜底菜谱：库内没承接时用演示菜谱顶上（正式前端由 FR-13 生成后入库）
            "resolved": _demo_recipe(item, topk[0][0], labels),
        }
        if item.get("cluster_note"):
            photo["note"] = cluster_note(topk, labels)
        photos.append(photo)

    return {
        "labels": {str(index): name for index, name in sorted(labels.items())},
        "model": {
            "arch": meta["arch"],
            "classes": meta["num_classes"],
            "input": "%dx%d" % (meta["preprocess"]["input_size"][-2:][0],
                                meta["preprocess"]["input_size"][-1]),
            # 训练阶段记录（测试集未保留，无法在本地复算），页面只当背景信息，不冒充实测
            "top1SelfReported": round(meta["training_meta"]["top1"], 4),
            "latencyMs": measure_latency_ms(session, (PROTOTYPE_DIR / PHOTOS[0]["file"]).resolve()),
        },
        "photos": photos,
    }


def render_dish_html(data):

    CSS = r"""
      :root {
        --color-bg: #F5F5F5;
        --color-surface: #FFFFFF;
        --color-text: #111111;
        --color-text-muted: #6B7280;
        --color-text-faint: #9CA3AF;
        --color-border: #E5E7EB;
        --color-border-strong: #111111;
        --color-primary: #111111;
        --color-accent-bg: #E8F5E9;
        --color-accent-text: #2E7D32;
        --color-warn-bg: #FFF7ED;
        --color-warn-text: #B45309;
        --color-muted-bg: #F3F4F6;
        --color-progress: #2563EB;
        --color-progress-track: #E5E7EB;
        --radius-card: 16px;
        --radius-chip: 10px;
        --radius-tag: 8px;
        --shadow-card: 0 1px 2px rgba(0, 0, 0, .04);
        --font-family: system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
      }

      * { box-sizing: border-box; }
      html, body { height: 100%; }

      body {
        margin: 0;
        background: var(--color-bg);
        color: var(--color-text);
        font-family: var(--font-family);
        font-size: 14px;
        line-height: 1.6;
        -webkit-font-smoothing: antialiased;
      }

      .topbar {
        max-width: 1360px;
        margin: 0 auto;
        padding: 24px 24px 0;
      }

      .app-title { margin: 0; font-size: 20px; font-weight: 600; }
      .app-sub { margin: 4px 0 0; font-size: 13px; color: var(--color-text-muted); }

      .banner {
        max-width: 1360px;
        margin: 12px auto 0;
        padding: 10px 16px;
        border-radius: 10px;
        background: var(--color-warn-bg);
        color: var(--color-warn-text);
        font-size: 12.5px;
        line-height: 1.7;
      }

      .banner code {
        font-family: ui-monospace, Consolas, monospace;
        background: rgba(0, 0, 0, .05);
        padding: 1px 5px;
        border-radius: 4px;
      }

      .app {
        display: grid;
        grid-template-columns: minmax(280px, 1fr) minmax(300px, 1.05fr) minmax(360px, 1.35fr);
        gap: 16px;
        max-width: 1360px;
        margin: 0 auto;
        padding: 16px 24px 24px;
        height: calc(100vh - 118px);
        min-height: 620px;
      }

      .col { display: flex; flex-direction: column; gap: 16px; min-height: 0; }

      .card {
        background: var(--color-surface);
        border: 1px solid var(--color-border);
        border-radius: var(--radius-card);
        box-shadow: var(--shadow-card);
        padding: 20px;
        min-height: 0;
      }

      .card--grow { display: flex; flex-direction: column; flex: 1; }

      .panel-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 14px;
      }

      .panel-title { margin: 0; font-size: 17px; font-weight: 600; }
      .badge { font-size: 12px; color: var(--color-text-faint); }
      .hint { font-size: 12px; color: var(--color-text-faint); white-space: nowrap; }

      /* ---------- 拍照识别 ---------- */

      .dropzone {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 10px;
        min-height: 210px;
        padding: 14px;
        border: 1.5px dashed #D1D5DB;
        border-radius: 12px;
        background: #FAFAFA;
        cursor: pointer;
        text-align: center;
        transition: border-color .15s, background .15s;
      }

      .dropzone:hover, .dropzone:focus-visible {
        border-color: var(--color-border-strong);
        background: #F5F5F5;
        outline: none;
      }

      .dropzone.is-dragover { border-color: var(--color-progress); background: #EFF6FF; }
      .dropzone-text { margin: 0; color: var(--color-text-muted); }
      .dropzone-text strong { color: var(--color-text); font-weight: 600; }

      .preview { max-width: 100%; max-height: 232px; border-radius: 8px; object-fit: contain; }

      .samples { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 12px; }

      .sample {
        padding: 0;
        border: 1.5px solid var(--color-border);
        border-radius: 8px;
        background: #FFF;
        overflow: hidden;
        cursor: pointer;
        line-height: 0;
      }

      .sample img { width: 100%; height: 46px; object-fit: cover; display: block; }
      .sample:hover { border-color: var(--color-text-muted); }
      .sample.is-active { border-color: var(--color-border-strong); }

      .actions { display: flex; gap: 12px; margin-top: 14px; }

      .btn {
        flex: 1;
        padding: 13px 18px;
        border-radius: var(--radius-chip);
        border: 1px solid transparent;
        font-family: inherit;
        font-size: 15px;
        font-weight: 500;
        cursor: pointer;
        transition: opacity .15s, border-color .15s;
      }

      .btn--primary { background: var(--color-primary); color: #FFF; }
      .btn--primary:hover:not(:disabled) { opacity: .85; }
      .btn--secondary { background: #FFF; color: var(--color-text); border-color: var(--color-border); }
      .btn--secondary:hover:not(:disabled) { border-color: var(--color-border-strong); }
      .btn:disabled { opacity: .45; cursor: not-allowed; }

      .caption { margin: 12px 0 0; font-size: 12px; color: var(--color-text-faint); text-align: center; }

      /* ---------- 识别结果 ---------- */

      .candidates { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; min-height: 0; }

      .cand {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 11px 14px;
        border: 1.5px solid var(--color-border);
        border-radius: var(--radius-chip);
        background: #FFF;
        cursor: pointer;
        text-align: left;
        font-family: inherit;
        font-size: 15px;
        color: var(--color-text-faint);
        transition: border-color .15s, color .15s;
      }

      .cand:hover { border-color: var(--color-text-muted); }
      .cand:focus-visible { outline: 2px solid var(--color-progress); outline-offset: 2px; }
      .cand.is-selected { border-color: var(--color-border-strong); color: var(--color-text); }

      .cand-name { white-space: nowrap; }
      .cand-name small { color: var(--color-text-faint); font-size: 12px; margin-left: 6px; }

      .bar { flex: 1; height: 6px; min-width: 30px; border-radius: 999px; background: var(--color-progress-track); overflow: hidden; }
      .bar-fill { display: block; height: 100%; border-radius: 999px; background: var(--color-progress); }
      .cand-pct { font-size: 12px; color: var(--color-text-faint); font-variant-numeric: tabular-nums; white-space: nowrap; }
      .cand.is-selected .cand-pct { color: var(--color-text-muted); }

      .note {
        margin: 12px 0 0;
        padding: 10px 12px;
        border-radius: 8px;
        background: var(--color-warn-bg);
        color: var(--color-warn-text);
        font-size: 12.5px;
        line-height: 1.6;
      }

      /* ---------- 菜谱 ---------- */

      .recipe-scroll { flex: 1; min-height: 0; overflow-y: auto; padding-right: 6px; margin-right: -6px; }
      .recipe-name { margin: 0; font-size: 20px; font-weight: 600; }
      .recipe-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
      .recipe-meta { margin: 8px 0 0; font-size: 13px; color: var(--color-text-muted); }
      .recipe-desc { margin: 8px 0 0; font-size: 13.5px; color: var(--color-text-muted); }

      .section-label {
        margin: 20px 0 10px;
        font-size: 12px;
        font-weight: 600;
        letter-spacing: .04em;
        color: var(--color-text-faint);
      }

      .tags { display: flex; flex-wrap: wrap; gap: 8px; }

      .tag { padding: 4px 11px; border-radius: var(--radius-tag); font-size: 13px; background: var(--color-accent-bg); color: var(--color-accent-text); }
      .tag--optional { background: var(--color-muted-bg); color: var(--color-text-muted); }

      .steps { margin: 0; padding: 0 0 0 22px; font-size: 14px; }
      .steps li { padding-left: 2px; }
      .steps li + li { margin-top: 8px; }

      .video {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-top: 22px;
        padding: 14px 16px;
        border: 1px solid var(--color-border);
        border-radius: 12px;
      }

      .video-text { min-width: 0; }
      .video-title { margin: 0; font-size: 14px; font-weight: 600; }
      .video-url { margin: 2px 0 0; font-size: 11.5px; color: var(--color-text-faint); word-break: break-all; }

      .video-btn {
        flex: none;
        padding: 9px 16px;
        border-radius: var(--radius-chip);
        border: 1px solid var(--color-border-strong);
        background: #FFF;
        color: var(--color-text);
        font-family: inherit;
        font-size: 13.5px;
        cursor: pointer;
      }

      .video-btn:hover { background: var(--color-primary); color: #FFF; }

      .empty { margin: 0; padding: 32px 8px; text-align: center; color: var(--color-text-faint); font-size: 13px; }

      @media (max-width: 1100px) {
        .app { grid-template-columns: 1fr; height: auto; }
        .candidates, .recipe-scroll { overflow: visible; }
      }
    """

    JS = r"""
    (function () {
      "use strict";

      var DATA = JSON.parse(document.getElementById("demo-dishes").textContent);
      var VIDEO_TEMPLATE = "https://search.bilibili.com/all?keyword=";

      var state = { photo: null, candidate: null, customFile: false, busy: false };

      var elDropzone = document.getElementById("dropzone");
      var elFile = document.getElementById("file-input");
      var elSamples = document.getElementById("samples");
      var elCandidates = document.getElementById("candidates");
      var elRecipe = document.getElementById("recipe");
      var elCaption = document.getElementById("model-caption");
      var elRecipeSource = document.getElementById("recipe-source");
      var btnRun = document.getElementById("btn-run");
      var btnReset = document.getElementById("btn-reset");

      elCaption.textContent = "模型: " + DATA.model.arch + " · " + DATA.model.classes + " 类 · 输入 " +
        DATA.model.input + " · 单张约 " + DATA.model.latencyMs + " ms";

      function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, function (ch) {
          return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
        });
      }

      function videoUrl(name) {
        return VIDEO_TEMPLATE + encodeURIComponent(name + " 做法");
      }

      function labelOf(index) {
        return DATA.labels[String(index)] || ("类别 #" + index);
      }

      function renderSamples() {
        elSamples.innerHTML = DATA.photos.map(function (photo) {
          return '<button class="sample' + (state.photo === photo.id ? " is-active" : "") +
            '" data-photo="' + photo.id + '" type="button" title="' + escapeHtml(photo.caption) + '">' +
            '<img src="' + photo.file + '" alt="' + escapeHtml(photo.caption) + '"></button>';
        }).join("");
      }

      function currentPhoto() {
        return DATA.photos.filter(function (item) { return item.id === state.photo; })[0] || null;
      }

      function renderPreview() {
        var photo = currentPhoto();
        if (!photo) {
          elDropzone.innerHTML = '<p class="dropzone-text"><strong>点击选择</strong> 或拖拽成品菜照片</p>' +
            '<span class="badge">jpg / png / webp · ≤ 5 MB</span>';
          return;
        }
        elDropzone.innerHTML = '<img class="preview" src="' + photo.file + '" alt="' +
          escapeHtml(photo.caption) + '">' +
          '<span class="badge">已选择示例图 · ' + escapeHtml(photo.caption) + "</span>";
      }

      function renderCandidates() {
        var photo = currentPhoto();
        if (!photo) {
          elCandidates.innerHTML = '<p class="empty">识别后这里显示候选菜品与置信度</p>';
          return;
        }
        elCandidates.innerHTML = photo.topk.map(function (hit, rank) {
          var selected = state.candidate === hit.index;
          return '<button class="cand' + (selected ? " is-selected" : "") + '" data-index="' + hit.index +
            '" type="button"><span class="cand-name">' + escapeHtml(labelOf(hit.index)) +
            "<small>#" + hit.index + "</small></span>" +
            '<span class="bar"><span class="bar-fill" style="width:' + (hit.confidence * 100).toFixed(1) + '%"></span></span>' +
            '<span class="cand-pct">' + (hit.confidence * 100).toFixed(1) + "%</span></button>";
        }).join("") +
          (photo.note ? '<p class="note">' + escapeHtml(photo.note) + "</p>" : "");
      }

      function renderRecipe() {
        var photo = currentPhoto();
        if (!photo || state.candidate === null) {
          elRecipeSource.textContent = "";
          elRecipe.innerHTML = '<p class="empty">识别出菜品后，这里显示菜谱与做法</p>';
          return;
        }
        var index = state.candidate;
        var r = photo.classRecipes[String(index)] || photo.resolved;
        if (!photo.classRecipes[String(index)] && index !== photo.topk[0].index) {
          elRecipeSource.textContent = "库外菜";
          elRecipe.innerHTML = '<p class="empty">' + escapeHtml(labelOf(index)) + "（类别 #" + index +
            "）暂无菜谱数据<br>这一类还没补进菜谱库，正式前端会由 FR-13 调用大模型生成做法并入库</p>";
          return;
        }
        elRecipeSource.textContent = r.source;

        var required = r.required.map(function (pair) {
          return '<span class="tag">' + escapeHtml(pair[0]) + " · " + escapeHtml(pair[1]) + "</span>";
        }).join("");
        var optional = r.optional.map(function (pair) {
          return '<span class="tag tag--optional">' + escapeHtml(pair[0]) + " · " + escapeHtml(pair[1]) + "</span>";
        }).join("");
        var steps = r.steps.map(function (text) {
          return "<li>" + escapeHtml(text) + "</li>";
        }).join("");
        var url = videoUrl(r.name);
        var displayUrl = VIDEO_TEMPLATE + r.name + " 做法";

        elRecipe.innerHTML =
          '<div class="recipe-head"><h3 class="recipe-name">' + escapeHtml(r.name) + "</h3></div>" +
          '<p class="recipe-meta">' + r.cookTimeMinutes + " 分钟 · 难度 " + escapeHtml(r.difficulty) +
          " · ★ " + r.rating + " · " + r.servings + " 人份</p>" +
          '<p class="recipe-desc">' + escapeHtml(r.description) + "</p>" +
          '<p class="section-label">主料</p><div class="tags">' + required + "</div>" +
          '<p class="section-label">辅料</p><div class="tags">' + optional + "</div>" +
          '<p class="section-label">做法</p><ol class="steps">' + steps + "</ol>" +
          '<div class="video"><div class="video-text"><p class="video-title">视频教程 · ' +
          escapeHtml(r.name) + " 做法</p>" +
          '<p class="video-url">' + escapeHtml(displayUrl) + "</p></div>" +
          '<button class="video-btn" id="btn-video" type="button">打开视频</button></div>';

        document.getElementById("btn-video").addEventListener("click", function () {
          window.open(url, "_blank", "noopener");
        });
      }

      function renderAll() {
        renderSamples();
        renderPreview();
        renderCandidates();
        renderRecipe();
        btnRun.disabled = state.busy || !state.photo;
        btnReset.disabled = !state.photo;
      }

      function selectPhoto(id) {
        state.photo = id;
        state.candidate = null;
        state.customFile = false;
        renderAll();
      }

      function runRecognition() {
        if (!state.photo || state.busy) return;
        if (state.customFile) {
          state.candidate = null;
          renderAll();
          elCandidates.innerHTML =
            '<p class="empty">识别需要后端模型服务<br>演示环境只内置了下方 4 张示例图</p>';
          return;
        }
        state.busy = true;
        btnRun.textContent = "识别中…";
        renderAll();
        window.setTimeout(function () {
          state.busy = false;
          state.candidate = currentPhoto().topk[0].index;
          btnRun.textContent = "开始识别";
          renderAll();
        }, 420);
      }

      function reset() {
        state.photo = null;
        state.candidate = null;
        state.customFile = false;
        renderAll();
      }

      elSamples.addEventListener("click", function (event) {
        var button = event.target.closest("[data-photo]");
        if (button) selectPhoto(button.dataset.photo);
      });

      elCandidates.addEventListener("click", function (event) {
        var button = event.target.closest("[data-index]");
        if (!button) return;
        state.candidate = Number(button.dataset.index);
        renderAll();
      });

      elDropzone.addEventListener("click", function () { elFile.click(); });
      elFile.addEventListener("change", function () {
        var file = elFile.files && elFile.files[0];
        if (!file) return;
        state.customFile = true;
        state.photo = null;
        state.candidate = null;
        var reader = new FileReader();
        reader.onload = function () {
          elDropzone.innerHTML = '<img class="preview" src="' + reader.result +
            '" alt="已选择图片"><span class="badge">已选择本地图片 · ' + escapeHtml(file.name) + "</span>";
          renderSamples();
          renderCandidates();
          renderRecipe();
          btnRun.disabled = false;
          btnReset.disabled = false;
        };
        reader.readAsDataURL(file);
      });

      ["dragenter", "dragover"].forEach(function (type) {
        elDropzone.addEventListener(type, function (event) {
          event.preventDefault();
          elDropzone.classList.add("is-dragover");
        });
      });
      ["dragleave", "drop"].forEach(function (type) {
        elDropzone.addEventListener(type, function (event) {
          event.preventDefault();
          elDropzone.classList.remove("is-dragover");
        });
      });

      btnRun.addEventListener("click", runRecognition);
      btnReset.addEventListener("click", reset);

      renderAll();
    })();
    """

    html = []
    html.append('<!DOCTYPE html>')
    html.append('<html lang="zh-CN">')
    html.append('<head>')
    html.append('<meta charset="utf-8">')
    html.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    html.append('<title>基于深度学习的智能菜谱识别</title>')
    html.append('<style>' + CSS + '</style>')
    html.append('</head>')
    html.append('<body>')
    html.append('<header class="topbar">')
    html.append('  <h1 class="app-title">基于深度学习的智能菜谱识别</h1>')
    html.append('  <p class="app-sub">上传成品菜照片 → 识别菜品 → 查看菜谱与做法</p>')
    html.append('</header>')
    html.append('<div class="banner">演示数据 · 候选与置信度是 <code>models/dish.onnx</code> 的真实输出；'
                '类别名取自随模型交付的 <code>models/dish_labels.json</code>（208 类，'
                '由 <code>backend/tools/import_labels.py</code> 从 CSV 转换，下标一一对应）。'
                '菜谱优先取自库内种子菜谱（<code>backend/app/data/seed_recipes.json</code>）；'
                '库外菜在正式前端 <code>frontend/</code> 里由大模型生成做法并写回库内（FR-13），'
                '本页是离线快照、跑不了大模型，只能用演示菜谱占位并注明来源。</div>')
    html.append('<div class="app">')
    html.append('  <div class="col">')
    html.append('    <section class="card">')
    html.append('      <div class="panel-head"><h2 class="panel-title">拍照识别</h2>'
                '<span class="badge">菜品分类</span></div>')
    html.append('      <div class="dropzone" id="dropzone" tabindex="0"></div>')
    html.append('      <input id="file-input" type="file" accept="image/jpeg,image/png,image/webp" hidden>')
    html.append('      <div class="samples" id="samples"></div>')
    html.append('      <div class="actions">')
    html.append('        <button class="btn btn--primary" id="btn-run" type="button">开始识别</button>')
    html.append('        <button class="btn btn--secondary" id="btn-reset" type="button">重置</button>')
    html.append('      </div>')
    html.append('      <p class="caption" id="model-caption"></p>')
    html.append('    </section>')
    html.append('  </div>')
    html.append('  <div class="col">')
    html.append('    <section class="card card--grow">')
    html.append('      <div class="panel-head"><h2 class="panel-title">识别结果</h2>'
                '<span class="hint">点击切换候选</span></div>')
    html.append('      <div class="candidates" id="candidates"></div>')
    html.append('    </section>')
    html.append('  </div>')
    html.append('  <div class="col">')
    html.append('    <section class="card card--grow">')
    html.append('      <div class="panel-head"><h2 class="panel-title">菜谱与做法</h2>'
                '<span class="badge" id="recipe-source"></span></div>')
    html.append('      <div class="recipe-scroll" id="recipe"></div>')
    html.append('    </section>')
    html.append('  </div>')
    html.append('</div>')
    html.append('<script id="demo-dishes" type="application/json">')
    html.append(json.dumps(data, ensure_ascii=False, indent=2))
    html.append('</script>')
    html.append('<script>' + JS + '</script>')
    html.append('</body>')
    html.append('</html>')

    return "\n".join(html) + "\n"


def main():
    html = render_dish_html(build_demo_data())
    io.open(OUTPUT_PATH, "w", encoding="utf-8", newline="").write(html)
    print("written %s" % OUTPUT_PATH.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
