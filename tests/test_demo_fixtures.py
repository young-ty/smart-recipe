# -*- coding: utf-8 -*-
'''静态演示站（GitHub Pages 上那份）的数据契约测试。

演示站没有后端，页面上的候选与置信度全部来自 frontend/src/demo/fixtures.json。
这份快照最容易出的问题是「悄悄过期」：换了模型、改了菜谱库，页面还照着旧的数字显示，
看起来正常，其实全是编的。所以这里守四件事：

1. 快照里的候选与置信度，必须还能被 models/dish.onnx 现场重算出来；
2. 类别名必须逐项等于 models/dish_labels.json，不许自己编名字；
3. 每张示例图的 top-1 都要能直接拿出菜谱，点进去不能是空页；
4. 库内菜谱必须能对回 backend/app/data/seed_recipes.json，不许在快照里另写一份。

权重或运行环境缺失时自动跳过实模型比对，不阻塞其他人跑测试。

运行：python -m unittest tests.test_demo_fixtures -v
'''

import importlib
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_PATH = REPO_ROOT / 'frontend' / 'src' / 'demo' / 'fixtures.json'
RECIPES_PATH = REPO_ROOT / 'demo' / 'recipes.json'
DEMO_DIR = REPO_ROOT / 'demo'
MODEL_PATH = REPO_ROOT / 'models' / 'dish.onnx'
LABELS_PATH = REPO_ROOT / 'models' / 'dish_labels.json'
SEED_RECIPES_PATH = REPO_ROOT / 'backend' / 'app' / 'data' / 'seed_recipes.json'
VITE_CONFIG_PATH = REPO_ROOT / 'frontend' / 'vite.config.ts'
DEMO_MODULE_PATH = REPO_ROOT / 'frontend' / 'src' / 'demo' / 'index.ts'
STUB_PATH = REPO_ROOT / 'frontend' / 'src' / 'demo' / 'fixtures.stub.json'
CLIENT_PATH = REPO_ROOT / 'frontend' / 'src' / 'api' / 'client.ts'
PACKAGE_PATH = REPO_ROOT / 'frontend' / 'package.json'
WORKFLOW_PATH = REPO_ROOT / '.github' / 'workflows' / 'deploy-pages.yml'
BUILDER_MODULE = 'backend.tools.export_demo_fixtures'

# 换模型后允许的置信度漂移：候选顺序不对是硬错误，第六位小数不同不算
CONFIDENCE_TOLERANCE = 0.002
GENERATOR_TOLERANCE = 1e-5


def read_text(path):
    return path.read_text(encoding='utf-8')


def softmax_topk(logits, k=5):
    import numpy as np

    shifted = np.exp(logits - logits.max())
    probs = shifted / shifted.sum()
    return [(int(index), float(probs[index])) for index in probs.argsort()[::-1][:k]]


class DemoFixtureContractTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads(read_text(FIXTURES_PATH))
        cls.recipes = json.loads(read_text(RECIPES_PATH))
        cls.samples = cls.fixtures['samples']

    def test_snapshot_covers_every_demo_photo(self):
        '''demo/dishes 里的实拍图一张都不能漏，漏了演示站就少一张可点的图。'''

        on_disk = sorted(item.name for item in (DEMO_DIR / 'dishes').glob('*.jpg'))
        in_snapshot = sorted(Path(sample['image']).name for sample in self.samples)

        self.assertEqual(in_snapshot, on_disk, '快照里的演示图与 demo/dishes 不一致')
        for sample in self.samples:
            self.assertTrue(
                (REPO_ROOT / sample['image']).is_file(), '演示图缺失：%s' % sample['image']
            )
            self.assertTrue(sample['caption'], '%s 缺少画面说明' % sample['id'])
            self.assertTrue(sample['note'], '%s 缺少演示说明' % sample['id'])

    def test_candidates_shape(self):
        class_count = self.fixtures['model']['class_count']
        self.assertEqual(class_count, 208, '类别数应与交付的 208 类一致')

        for sample in self.samples:
            candidates = sample['candidates']
            self.assertTrue(candidates, '%s 没有任何候选' % sample['id'])
            self.assertLessEqual(len(candidates), 5, '%s 的候选超过 5 个' % sample['id'])

            confidences = [item['confidence'] for item in candidates]
            self.assertEqual(
                confidences,
                sorted(confidences, reverse=True),
                '%s 的候选未按置信度降序' % sample['id'],
            )
            for item in candidates:
                self.assertTrue(0 <= item['index'] < class_count, '类别下标越界')
                self.assertTrue(0 < item['confidence'] <= 1, '置信度不在 (0, 1] 内')
                self.assertFalse(item['provisional_name'], '清单已交付，不该再带待确认标记')

    def test_class_names_match_the_delivered_labels(self):
        if not LABELS_PATH.is_file():
            self.skipTest('类别清单缺失：%s' % LABELS_PATH)
        labels = json.loads(read_text(LABELS_PATH))

        for sample in self.samples:
            for item in sample['candidates']:
                self.assertEqual(
                    item['name'],
                    labels[str(item['index'])],
                    '%s 的候选 #%d 名称与清单不一致' % (sample['id'], item['index']),
                )

    def test_every_sample_opens_a_recipe(self):
        '''演示站的核心承诺：点任意一张示例图，选 top-1 都能看到菜谱。'''

        for sample in self.samples:
            top1 = sample['candidates'][0]['name']
            entry = self.recipes.get(top1)
            self.assertIsNotNone(entry, '%s 的 top-1（%s）没有预置菜谱' % (sample['id'], top1))
            recipe = entry['recipe']
            self.assertIsNotNone(recipe, '%s 的菜谱为空' % sample['id'])
            self.assertTrue(recipe['required'], '%s 的菜谱缺少主料' % sample['id'])
            self.assertTrue(recipe['steps'], '%s 的菜谱缺少步骤' % sample['id'])
            self.assertIn(recipe['difficulty'], ('简单', '中等', '困难'), '难度取值非法')

    def test_seed_recipes_trace_back_to_the_committed_seed_data(self):
        '''库内菜谱不许在快照里另抄一份：必须与 seed_recipes.json 的同 id 记录一致。'''

        seed = json.loads(read_text(SEED_RECIPES_PATH))
        by_id = {item['id']: item for item in seed}

        checked = 0
        for dish, entry in self.recipes.items():
            recipe = entry['recipe']
            self.assertEqual(entry['dish'], dish, 'key 与 dish 字段不一致：%s' % dish)
            if recipe['origin'] != 'seed':
                continue
            checked += 1
            source = by_id.get(recipe['id'])
            self.assertIsNotNone(source, '%s 指向了种子数据里没有的 id %s' % (dish, recipe['id']))
            self.assertEqual(recipe['name'], source['name'])
            self.assertEqual(recipe['steps'], source['steps'])
            self.assertEqual(recipe['cook_time_minutes'], source['cook_time_minutes'])
            self.assertEqual(recipe['difficulty'], source['difficulty'])
            self.assertEqual(recipe['rating'], source['rating'])
            self.assertEqual(recipe['servings'], source['servings'])
            self.assertEqual(len(recipe['required']), len(source['required']))
        self.assertGreater(checked, 0, '预置菜谱里一道库内菜都没有，快照明显不对')

    def test_generated_recipes_are_labelled_and_unrated(self):
        '''生成后落库的那批要保留 origin = generated，页面才标得出「自动入库」。'''

        generated = 0
        for dish, entry in self.recipes.items():
            recipe = entry['recipe']
            if recipe['origin'] == 'seed':
                continue
            self.assertEqual(recipe['origin'], 'generated', '%s 的来源标记不认识' % dish)
            generated += 1
            self.assertGreaterEqual(recipe['id'], 10000, '%s 的生成结果 id 应 >= 10000' % dish)
            self.assertIsNone(recipe['rating'], '%s 是生成结果，不该有评分' % dish)
            self.assertEqual(entry['source'], 'library', '落库后应命中库内，不再走生成')
        self.assertGreater(generated, 0, '快照里没有生成后落库的菜，演示不出这条链路')

    def test_demo_module_and_recipe_snapshot_agree(self):
        '''fixtures.json 与 demo/recipes.json 必须一致，否则页面用的是另一份菜谱。'''

        self.assertEqual(
            self.fixtures['recipes'], self.recipes, '两份快照已经不同步，重跑导出脚本'
        )

    @unittest.skipUnless(MODEL_PATH.is_file(), '未转换 models/dish.onnx，跳过实模型比对')
    def test_topk_matches_real_model_output(self):
        '''快照里的候选与置信度必须来自 models/dish.onnx 的真实输出。'''

        from prototype.preprocess import create_session, load_image, manifest

        preprocess = manifest()['preprocess']
        session = create_session(MODEL_PATH)

        for sample in self.samples:
            image_path = REPO_ROOT / sample['image']
            batch = load_image(image_path, preprocess['mean'], preprocess['std'])
            logits = session.run(None, {'input': batch})[0][0]

            expected = softmax_topk(logits, len(sample['candidates']))
            actual = [(item['index'], item['confidence']) for item in sample['candidates']]

            self.assertEqual(
                [index for index, _ in actual],
                [index for index, _ in expected],
                '%s 的候选顺序与模型输出不一致' % sample['id'],
            )
            for (_, shown), (_, real) in zip(actual, expected):
                self.assertAlmostEqual(
                    shown,
                    real,
                    delta=CONFIDENCE_TOLERANCE,
                    msg='%s 的置信度与模型输出不一致（快照 %.4f / 模型 %.4f）'
                    % (sample['id'], shown, real),
                )

    @unittest.skipUnless(MODEL_PATH.is_file(), '未转换 models/dish.onnx，跳过重新生成比对')
    def test_committed_snapshot_matches_generator(self):
        '''快照必须还能由导出脚本原样重算出来，否则它就是一份会过期的快照。'''

        builder = importlib.import_module(BUILDER_MODULE)
        rendered = builder.render_fixtures(builder.collect_samples(), builder.load_recipes())

        self.assertEqual(rendered['model'], self.fixtures['model'])
        self.assertEqual(
            [
                (item['id'], item['image'], item['caption'], item['note'])
                for item in rendered['samples']
            ],
            [
                (item['id'], item['image'], item['caption'], item['note'])
                for item in self.samples
            ],
            '示例图清单与导出脚本不一致，请重跑 export_demo_fixtures.py',
        )
        for rebuilt, committed in zip(rendered['samples'], self.samples):
            self.assertEqual(
                [item['index'] for item in rebuilt['candidates']],
                [item['index'] for item in committed['candidates']],
                '%s 的候选与导出脚本不一致' % committed['id'],
            )
            for fresh, old in zip(rebuilt['candidates'], committed['candidates']):
                self.assertAlmostEqual(
                    fresh['confidence'],
                    old['confidence'],
                    delta=GENERATOR_TOLERANCE,
                    msg='%s 的置信度与导出脚本不一致，请重跑 export_demo_fixtures.py'
                    % committed['id'],
                )


class DemoSiteWiringTest(unittest.TestCase):
    '''演示站是靠构建配置拼出来的，接线断了页面会静默变回「连不上后端」。'''

    def test_demo_mode_is_a_build_mode_not_a_runtime_flag(self):
        module = read_text(DEMO_MODULE_PATH)
        self.assertIn('import.meta.env.MODE', module, '演示模式没有读构建模式')
        self.assertIn('\'demo\'', module, '演示模式没有接到 demo 这个构建模式')
        self.assertIn('BASE_URL', module, '示例图地址要走 BASE_URL，否则子路径下 404')

    def test_api_client_never_calls_the_backend_in_demo_mode(self):
        client = read_text(CLIENT_PATH)
        self.assertIn('DEMO_MODE', client, '接口客户端没有接演示模式')
        for call in ('recognizeImage', 'resolveRecipe', 'fetchDishes', 'fetchHealth'):
            body = client.split('export function %s' % call, 1)[1].split('export function', 1)[0]
            self.assertIn('DEMO_MODE', body, '%s 在演示模式下仍会打后端' % call)

    def test_vite_copies_the_demo_photos_only_in_demo_builds(self):
        config = read_text(VITE_CONFIG_PATH)
        self.assertIn('config.mode !==', config, '演示图复制没有按构建模式开关')
        self.assertIn('\'demo\'', config, '演示图复制没有接到 demo 这个构建模式')
        self.assertIn('../demo/dishes', config, '演示图没有从 demo/dishes 复制')
        self.assertIn('.nojekyll', config, '缺少 .nojekyll，Pages 会把下划线开头的文件丢掉')

    def test_demo_snapshot_never_ships_in_a_normal_build(self):
        '''演示快照只在演示构建与测试里进包，常规构建换成空壳，别多发约 9 kB（gzip）。'''

        config = read_text(VITE_CONFIG_PATH)
        self.assertIn('virtual:demo-fixtures', config, '演示快照没有走虚拟模块')
        self.assertIn('fixtures.stub.json', config, '常规构建没有换成空壳，会把演示数据打进包')
        self.assertTrue(STUB_PATH.is_file(), '缺少空壳快照：%s' % STUB_PATH)

        stub = json.loads(read_text(STUB_PATH))
        self.assertEqual(stub['samples'], [], '空壳快照里不该有数据')
        self.assertEqual(stub['recipes'], {}, '空壳快照里不该有数据')

        module = read_text(DEMO_MODULE_PATH)
        self.assertIn('virtual:demo-fixtures', module, '演示模块没有从虚拟 id 取数据')

    def test_npm_script_builds_in_demo_mode(self):
        package = json.loads(read_text(PACKAGE_PATH))
        script = package['scripts'].get('build:demo', '')
        self.assertIn('--mode demo', script, 'build:demo 没有切换到演示模式')
        self.assertIn('tsc', script, 'build:demo 跳过了类型检查')

    def test_pages_workflow_publishes_the_static_build(self):
        self.assertTrue(WORKFLOW_PATH.is_file(), '缺少 GitHub Pages 发布工作流')
        workflow = read_text(WORKFLOW_PATH)
        for keyword in ('npm run build:demo', 'upload-pages-artifact', 'deploy-pages', 'frontend/dist'):
            self.assertIn(keyword, workflow, '发布工作流缺少：%s' % keyword)


if __name__ == '__main__':
    unittest.main()
