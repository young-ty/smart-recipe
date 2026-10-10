# 演示用实拍图

本目录里的东西有三个去处：手工联调时选图、`prototype/dish.html` 的离线快照、
以及 GitHub Pages 上的静态演示站（`npm run build:demo` 会把 `dishes/` 整目录复制进构建产物）。

`recipes.json` 是静态演示站用的菜谱快照。库内那几道与 `backend/app/data/seed_recipes.json`
逐字段一致，库外的（蛋包饭、莴笋肉丝、豆腐花、臭豆腐等）是运行期由大模型真实生成、
落库之后再从库里导出的，`origin` 保留为 `generated`，演示站上照旧标「大模型生成后自动入库」。
导出与刷新方式见 `backend/tools/export_demo_fixtures.py`，契约由 `tests/test_demo_fixtures.py` 守。

## demo/dishes/ —— 成品菜识别演示图（当前方向）

手工联调从本目录选图：启动后端与前端后，在「拍照识别」区选一张 → 点「开始识别」。
页面**不再内置示例图入口**，选图只有「本地文件」一个入口。目录里现有 9 张，分两批：

- **第一批（2026-09-20，4 张）**：已接入 `prototype/dish.html` 快照，同时是 `backend/tests/` 与
  `frontend/tests/fixtures.ts` 的 fixture 来源——改了模型或预处理，要重跑
  `python -m prototype.build_dish_prototype` 并同步 fixture。
- **第二批（2026-09-23，豆腐系列 5 张）**：现场备用图，只留档，不进原型快照、不进 fixture，
  换模型不必重跑原型。

两批的候选与置信度都是 `models/dish.onnx` 的真实输出，数值为同图同机的实测值。
「实测 top-1」一列写的是**模型判出的类别**（下标与菜名取自 `models/dish_labels.json`）；
画面里的菜与判定不一致时标（误判），免得把模型给的类别当成这张图拍的菜。

### 第一批

| 文件 | 画面内容 | 实测 top-1 | 菜谱来源 |
| --- | --- | --- | --- |
| `dishes/dish-braised-pork.jpg` | 红烧肉 | #77 红烧肉 88.5% | 库内命中（`seed_recipes.json` #109） |
| `dishes/dish-omurice.jpg` | 蛋包饭 | #131 蛋包饭 70.9% | 库外 → 大模型生成后落库 |
| `dishes/dish-shrimp-greens.jpg` | 虾仁炒青笋 | #100 莴笋肉丝 64.3%（误判） | 库外 → 生成后落库；#121 清炒虾仁以 7.4% 退居第二，是主料判反的现场例子 |
| `dishes/dish-noodle-potato.jpg` | 土豆肉末拌面 | #156 担担面 45.9%（误判） | 库外 → 生成后落库；前三名 #156 / #157 / #149 咬得很紧，用于演示「同族类别」提示 |

### 第二批：豆腐系列

同一食材族的连续样本，适合现场演示两件事：近义类别之间的置信度差距，以及「库里没有的菜由大模型生成」。
按 `backend/app/data/seed_dish_map.json`，#0 麻婆豆腐、#1 家常豆腐、#2 煎豆腐都已承接
（`recipe_id` 106 / 176 / 177）；表里的 #3 豆腐花、#4 臭豆腐还是留白（未登记），
与登记过的显式缺口一样走生成分支。

| 文件 | 画面内容 | 实测 top-1 | 菜谱来源 |
| --- | --- | --- | --- |
| `dishes/dish-mapo-tofu.jpg` | 麻婆豆腐（豆腐块、红油、葱花） | #0 麻婆豆腐 87.0% | 库内命中（`seed_recipes.json` #106） |
| `dishes/dish-home-style-tofu.jpg` | 家常豆腐（豆腐片配青椒、木耳、胡萝卜） | #1 家常豆腐 90.6% | 库内命中（`seed_recipes.json` #176）；#2 煎豆腐以 1.9% 退居第二 |
| `dishes/dish-braised-tofu.jpg` | 酱烧豆腐块，配青椒、胡萝卜与木耳 | #1 家常豆腐 74.1% | 库内命中（`seed_recipes.json` #176）；#2 煎豆腐以 13.8% 退居第二 |
| `dishes/dish-douhua.jpg` | 豆花（内酯豆腐泡在红油汁里，撒花生与香菜） | #3 豆腐花 95.9% | 库外 → 生成后落库 |
| `dishes/dish-pan-fried-tofu.jpg` | 煎豆腐块蘸红油汁，筷子夹起 | #4 臭豆腐 89.5% | 库外 → 生成后落库；#2 煎豆腐以 8.7% 退居第二 |

## ingredients-*.webp —— 已退役（旧方向留档）

这两张是「生食材识别」方向（FoodInsSeg 103 类）时期收集的，当前链路不再使用，仅作留档：
早期版本用它们演示过食材勾选与匹配度排序，识别结果由 `models/best.onnx` 推理得到。

| 文件 | 尺寸 | 画面内容 |
| --- | --- | --- |
| `ingredients-tomato-egg.webp` | 755×504 | 番茄（整颗与切块）、鸡蛋、大蒜 |
| `ingredients-mixed.webp` | 500×750 | 土豆、胡萝卜、五花肉、番茄、茄子、青椒、大蒜 |
