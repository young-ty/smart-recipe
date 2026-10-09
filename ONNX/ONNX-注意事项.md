# ONNX 模型使用注意事项

配套文件：`best.onnx`（335 MB）

这份文档说明**怎么正确调用这个 ONNX 模型**。里面每一条都是实测过的，
不是照抄网上的通用做法——**尤其是预处理那一节，比例写错会让准确率明显下降**。

---

## 1. 文件信息

| 项 | 值 |
|---|---|
| 文件 | `best.onnx` |
| 大小 | 335 MB |
| opset | 17 |
| 导出工具 | torch 2.6.0 + timm 1.0.26（**使用方不需要装这两个**） |
| 运行时 | onnxruntime 1.18 及以上（实测 1.23.2） |

模型是 `convnext_base.fb_in22k_ft_in1k` 在 ChineseFoodNet 上微调得到的
208 类中式菜品分类器。

## 2. 输入输出规格（最重要）

| | 名称 | 形状 | 类型 | 说明 |
|---|---|---|---|---|
| 输入 | `input` | **(N, 3, 288, 288)** | float32 | N 是动态的，可一次喂多张 |
| 输出 | `logits` | **(N, 208)** | float32 | **未归一化的原始 logits** |

### 三个容易搞错的点

1. **输出是 logits，不是概率**。要自己 softmax：
   `probs = softmax(logits)`。直接当概率用会得到荒谬的结果。

2. **空间尺寸固定是 288×288，不能改**。只有 **batch 维是动态的**。
   喂 224×224 会报错或得到无意义的结果。这个模型必须用 288。

3. **预处理不在模型里**。缩放、裁剪、归一化都得你在外面做——
   ONNX 只包含神经网络前向那一段。下面是精确做法。

## 3. 预处理（必须完全一致）

顺序是：**短边缩放到 329 → 中心裁剪 288 → 归一化**。

`329 = 288 × 256 / 224`，这个比例不能改。

### 推荐写法（不依赖 torch，只用 PIL + numpy）

```python
import numpy as np
from PIL import Image, ImageOps

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD  = np.array([0.229, 0.224, 0.225], np.float32)

def preprocess(path, size=288, resize=329):
    # EXIF 转正不能省：手机照片带旋转标记，
    # 不转正的话模型看到的是横躺的菜，会认错
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")

    w, h = im.size
    if w < h:
        nw, nh = resize, int(resize * h / w)   # 注意是 int() 截断，不是四舍五入
    else:
        nh, nw = resize, int(resize * w / h)
    im = im.resize((nw, nh), Image.BILINEAR)   # 必须用 BILINEAR

    top  = int(round((nh - size) / 2.0))       # 注意是 round()，不是整除
    left = int(round((nw - size) / 2.0))
    im = im.crop((left, top, left + size, top + size))

    x = np.asarray(im, np.float32) / 255.0
    x = (x - MEAN) / STD
    return x.transpose(2, 0, 1)[None]          # (1, 3, 288, 288)
```

> **这段代码经过逐像素核对**：和训练/评估时使用的 torchvision 变换
> （`Resize(329)` + `CenterCrop(288)` + `ToTensor` + `Normalize`）在 200 张
> 真实图片上**输出完全相同，最大像素差 0.000e+00**。
> 所以如果你的项目里有 torchvision，直接用它那两个变换也行，结果一样。

### 如果你用别的语言/库

上面的 `int()` 截断和 `round()` 取中，都是有意为之（对齐 torchvision 的语义）。
换库时注意两点：**缩放用双线性插值**、**裁剪要居中且按四舍五入取偏移**。
偏差 1 个像素不会让结果崩掉，但会带来细微差异。

## 4. 安装

```bash
pip install onnxruntime        # 约 20 MB，CPU 版
```

**不需要装 torch、不需要装 timm、不需要网络。** 这是 ONNX 最大的好处。

想用 GPU 的话装 `onnxruntime-gpu`，但它要求 CUDA/cuDNN 版本严格匹配，
有时比装 torch 还麻烦；本模型在 CPU 上一张图约 1 秒量级，做小工具够用，
**建议先用 CPU 版**。

## 5. 完整示例

```python
import numpy as np
import onnxruntime as ort

sess = ort.InferenceSession("best.onnx", providers=["CPUExecutionProvider"])
in_name  = sess.get_inputs()[0].name     # "input"
out_name = sess.get_outputs()[0].name    # "logits"

x = preprocess("your_dish.jpg")                       # 见上面第 3 节
logits = sess.run([out_name], {in_name: x})[0][0]     # (208,)

# 自己做 softmax：输出是 logits，不是概率
logits = logits - logits.max()
probs = np.exp(logits) / np.exp(logits).sum()

names = {}                                            # 菜名表
import csv
with open("Food list - Menu.csv", encoding="utf-8") as f:
    for row in list(csv.reader(f))[2:]:               # 前两行是标题和表头
        if row and row[0].strip().isdigit():
            names[int(row[0])] = row[1]

for i in probs.argsort()[::-1][:5]:
    print(f"{probs[i]*100:6.2f}%  {names[int(i)]}")
```

**批量推理**：把多张图的 `x` 沿第 0 轴拼起来（`np.concatenate`）一次喂进去即可，
batch 维是动态的。批量能显著提高吞吐。

## 6. 菜名表的三个坑

1. **必须用 `csv` 模块解析**。有些英文名里带逗号
   （如 `"Fried Potato, Green Pepper & Eggplant"`），用 `split(",")` 会整列错位，
   导致输出**静默**对应到错误的菜名——**映射错了比没有映射更糟**。

2. **模型输出下标 = 表里的 `List No.` 列**（0~207 顺序一致，已核对）。不要重排序。

3. 表的前两行是标题和表头，数据从第 3 行开始。

## 7. 与 `.pt` 版本的差异

| | `best.onnx` | `best.pt` |
|---|---|---|
| 体积 | 335 MB | 1005 MB（含训练用的优化器状态，推理用不到） |
| 依赖 | 只要 onnxruntime | torch + timm（2GB+） |
| 能否训练 | **不能**，只有前向 | 可以继续训练 |
| 数值 | 与 .pt 一致（见验证记录） | 基准 |

**这个 ONNX 是推理专用的**。要改模型结构、继续训练、做微调，得用回 `.pt`。

## 8. 性能提示

- **输入是固定的 288**，不要为了"更快"改成 224——准确率会掉 1 个多点。
- 想再快只能靠批量推理，或者用 `onnxruntime-gpu`。
- 会话（`InferenceSession`）创建一次就重复用，**不要每张图都新建**，那会慢几十倍。
- 多线程服务的注意：onnxruntime 的 session 本身是线程安全的，
  多个请求可以并发调用（这和 PyTorch 模块不同）。

## 9. 已知限制

- **只做 208 类中式菜品分类**，不在这个范围内的图片也会被强行归到某一类。
  建议对 softmax 后的最高概率设个阈值（比如低于 35% 就提示"不太确定"）。
- **不要加水平翻转 TTA**：实测只提升 0.15 个点，推理成本翻倍。
- **不要试图用 320/352 提高成绩**：实测没有收益。
- 数据集本身存在**错标签**（例如类别 154「茄汁拌面」里混进了实际是西红柿鸡蛋汤的图），
  近义类（大米粥/小米粥、饼干/曲奇）互相混淆较多。
  **模型答错时，先考虑是不是标注本身有问题，不必急着怀疑模型。**
- 未做量化。量化能让体积降到约 90 MB，但会掉点，需要重新验证后再用。

## 10. 验证记录

导出后做过两项验证，结果如下（可复现）：

**数值一致性**——200 张 val 图，同一批输入分别喂给 `.pt` 和 `.onnx`（都在 CPU 上）：

```
logits 最大绝对差     3.34e-05        ← float32 舍入级别
top-1 完全一致        200/200 (100%)
top-5 集合一致        200/200 (100%)
```

**预处理等价性**——第 3 节那段 PIL 代码 vs torchvision 变换，200 张图：

```
像素最大绝对差        0.000e+00       ← 完全相同
预测完全一致          200/200 (100%)
```

**模型精度**（在完整测试集 20,310 张上，`.pt` 版本的成绩，ONNX 与之一致）：
top-1 **84.54%** / top-5 97.91% / macro-F1 0.8308。

> 注意：这个成绩**无法在本地复算**，因为测试集（数据集本体）没有随模型提供。
> 请不要声称"重新验证过"。

## 11. 如何重新导出

如果以后要重新导出（换 opset、换模型），用 `export_onnx.py`：

```bash
python export_onnx.py                             # 导出 + 200 张图一致性验证
python export_onnx.py --verify 1000               # 验证样本多一点
python export_onnx.py --opset 18                  # 换 opset
```

**导出后务必看验证结果**：ONNX 转换可能静默改变数值，
`export_onnx.py` 会自动比对 top-1/top-5 是否完全一致，不一致会明确告警。
