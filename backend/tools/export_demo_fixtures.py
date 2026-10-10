# -*- coding: utf-8 -*-
'''把静态演示站要用的数据固化下来（演示站 = 挂在 GitHub Pages 上的纯前端快照）。

线上演示只能放静态文件：335 MB 的 ONNX 跑不动，没有后端也就没有大模型 Key。
所以演示站不假装自己在推理，而是把「真跑过一次」的结果固化进
frontend/src/demo/fixtures.json：

- 候选与置信度：现场跑 models/dish.onnx。预处理复用 prototype/preprocess.py，
  与后端识别层是同一份代码；菜名取自 models/dish_labels.json（208 类清单）。
- 菜谱：取自 demo/recipes.json。库内菜谱与 backend/app/data/seed_recipes.json 一致；
  库外菜是运行期由大模型真实生成、落库之后导出的快照（origin = generated），
  页面上照样标「大模型生成后自动入库」。

用法（仓库根目录）：

    .venv\\Scripts\\python.exe -m backend.tools.export_demo_fixtures
    .venv\\Scripts\\python.exe -m backend.tools.export_demo_fixtures --refresh-recipes

--refresh-recipes 重新导出 demo/recipes.json，需要本地有 backend/recipe.db
（也就是生成结果已经落库的那份库）。它走的是真后端：起 app、按识别出的候选逐个调
/api/recipes/resolve，所以导出来的菜谱和线上返回的是同一份。跑之前会把大模型 Key
清空——库里已有的菜直接命中，不该顺手再生成一遍、白烧额度。

换了模型、加了演示图、改了菜谱，都要重跑本脚本，否则页面上的数字就是编的；
tests/test_demo_fixtures.py 守着这件事。
'''

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT / 'backend'
for _entry in (str(ROOT), str(BACKEND_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

DEMO_DIR = ROOT / 'demo'
DISH_DIR = DEMO_DIR / 'dishes'
RECIPES_PATH = DEMO_DIR / 'recipes.json'
FIXTURES_PATH = ROOT / 'frontend' / 'src' / 'demo' / 'fixtures.json'
LABELS_PATH = ROOT / 'models' / 'dish_labels.json'

# 每张图固化前 k 个候选：前端候选栏按这份清单展示，点谁就查谁的菜谱
CANDIDATE_COUNT = 5

# 演示图。caption 是画面里那道菜（人写的），候选与置信度一律现场算，不手抄。
# note 解释这张图想演示什么，刻意不写百分比：换了模型数字就会漂。
PHOTOS = [
    {
        'id': 'braised-pork',
        'file': 'dish-braised-pork.jpg',
        'caption': '红烧肉',
        'note': '库内命中：识别对了，菜谱直接取自库内种子数据，不联网。',
    },
    {
        'id': 'omurice',
        'file': 'dish-omurice.jpg',
        'caption': '蛋包饭',
        'note': '库里本来没有这道菜：大模型生成做法后写回库内，此后同名请求直接命中。',
    },
    {
        'id': 'shrimp-greens',
        'file': 'dish-shrimp-greens.jpg',
        'caption': '虾仁炒青笋',
        'note': '模型把主料判反了：画面里是虾仁，它认成了莴笋肉丝。候选栏可以切到第二项看差别。',
    },
    {
        'id': 'noodle-potato',
        'file': 'dish-noodle-potato.jpg',
        'caption': '土豆肉末拌面',
        'note': '同族类别咬得很紧：前几名都是面食，置信度拉不开，这类样本更适合人工在候选栏里挑。',
    },
    {
        'id': 'mapo-tofu',
        'file': 'dish-mapo-tofu.jpg',
        'caption': '麻婆豆腐',
        'note': '库内命中：豆腐系列里最早进种子菜谱的一道。',
    },
    {
        'id': 'home-style-tofu',
        'file': 'dish-home-style-tofu.jpg',
        'caption': '家常豆腐',
        'note': '识别对了，也是库内命中：补库后家常豆腐进了种子菜谱；第二名是同族的煎豆腐。',
    },
    {
        'id': 'braised-tofu',
        'file': 'dish-braised-tofu.jpg',
        'caption': '酱烧豆腐块',
        'note': '同一族的另一张：top-1 仍是家常豆腐，第二名煎豆腐追近了不少，两道都已在库里。',
    },
    {
        'id': 'douhua',
        'file': 'dish-douhua.jpg',
        'caption': '豆腐花',
        'note': '红油汁里的内酯豆腐，识别置信度最高的一张。',
    },
    {
        'id': 'pan-fried-tofu',
        'file': 'dish-pan-fried-tofu.jpg',
        'caption': '煎豆腐块',
        'note': '误判：认成了臭豆腐，其实更接近同族的煎豆腐。',
    },
]


def class_labels():
    '''读 208 类菜名清单，键统一成 int。'''

    if not LABELS_PATH.is_file():
        raise SystemExit(
            '缺少类别清单 %s；先跑 backend/tools/import_labels.py "ONNX/Food list - Menu.csv"'
            % LABELS_PATH
        )
    payload = json.loads(LABELS_PATH.read_text(encoding='utf-8'))
    return {int(key): value for key, value in payload.items()}


def load_recipes():
    '''读 demo/recipes.json；还没有就先按 --refresh-recipes 生成一次。'''

    if not RECIPES_PATH.is_file():
        raise SystemExit(
            '缺少 %s；先跑 backend/tools/export_demo_fixtures.py --refresh-recipes' % RECIPES_PATH
        )
    return json.loads(RECIPES_PATH.read_text(encoding='utf-8'))


def build_candidates(session, labels, image_path):
    '''现场跑一遍模型，得到候选列表。'''

    from prototype.preprocess import predict_topk

    return [
        {
            'index': index,
            'name': labels[index],
            'confidence': round(probability, 6),
            'provisional_name': False,
        }
        for index, probability in predict_topk(session, image_path, k=CANDIDATE_COUNT)
    ]


def collect_samples():
    '''九张演示图各自的候选，全部来自模型现场输出。'''

    from prototype.preprocess import create_session

    labels = class_labels()
    session = create_session()
    samples = []
    for photo in PHOTOS:
        image_path = DISH_DIR / photo['file']
        if not image_path.is_file():
            raise SystemExit('演示图不存在：%s' % image_path)
        samples.append(
            {
                'id': photo['id'],
                'image': 'demo/dishes/' + photo['file'],
                'caption': photo['caption'],
                'note': photo['note'],
                'candidates': build_candidates(session, labels, image_path),
            }
        )
    return samples


def model_info():
    from app.core.config import get_settings

    settings = get_settings()
    return {'version': settings.onnx_model_version, 'class_count': settings.dish_class_count}


def refresh_recipes(samples):
    '''按识别出的候选逐个调真后端，导出 demo/recipes.json。'''

    # 清空 Key：库里已有的菜直接命中，不该顺手再生成一遍
    os.environ['RECIPE_GENERATOR_API_KEY'] = ''

    from fastapi.testclient import TestClient

    from app.main import create_app

    recipes = {}
    with TestClient(create_app()) as client:
        for sample in samples:
            for candidate in sample['candidates']:
                dish = candidate['name']
                if dish in recipes:
                    continue
                response = client.post(
                    '/api/recipes/resolve',
                    json={'dish': dish, 'index': candidate['index']},
                )
                response.raise_for_status()
                body = response.json()
                if body.get('recipe') is not None:
                    recipes[dish] = body
    return recipes


def render_fixtures(samples, recipes):
    return {
        'model': model_info(),
        'samples': samples,
        'recipes': recipes,
    }


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
    # 统一写成 LF：这两个文件要进版本库，Windows 上默认的 CRLF 会让 diff 全红
    with io.open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    print('written %s' % path.relative_to(ROOT).as_posix())


def main():
    parser = argparse.ArgumentParser(description='导出静态演示站的数据')
    parser.add_argument(
        '--refresh-recipes',
        action='store_true',
        help='重新从本地库导出 demo/recipes.json（需要 backend/recipe.db）',
    )
    args = parser.parse_args()

    samples = collect_samples()
    recipes = refresh_recipes(samples) if args.refresh_recipes else load_recipes()
    if args.refresh_recipes:
        write_json(RECIPES_PATH, recipes)
    write_json(FIXTURES_PATH, render_fixtures(samples, recipes))


if __name__ == '__main__':
    main()
