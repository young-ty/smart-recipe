"""inspect_checkpoint 工具的契约测试。

训练产出的 .pt 有四种来源，能不能脱离训练源码加载、能不能转 ONNX 各不相同。
这里用 torchvision 的 resnet18 现场造出这四种存档，逐个验证判定结果——
判错会让人拿着错误的结论去要求返工，代价比多跑几秒测试高得多。

另外守住一条安全底线：判定过程只看 pickle 指令流，不反序列化。拿到来路不明的
权重时，这一步不应该执行文件里的任意代码。

运行：pytest backend/tests/test_inspect_checkpoint.py
"""

from __future__ import annotations

import importlib.util
import pickle
import sys
import zipfile
from pathlib import Path

import pytest

torch = pytest.importorskip("torch", reason="判定 .pt 格式需要 torch")
torchvision = pytest.importorskip("torchvision", reason="构造样例需要 torchvision")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOOL_PATH = REPO_ROOT / "backend" / "tools" / "inspect_checkpoint.py"

_tool_cache = {}


def load_tool():
    if "module" not in _tool_cache:
        spec = importlib.util.spec_from_file_location("inspect_checkpoint", TOOL_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["inspect_checkpoint"] = module
        spec.loader.exec_module(module)
        _tool_cache["module"] = module
    return _tool_cache["module"]


def _marker_probe(target: str) -> str:
    """供恶意样本调用：真的被反序列化就会落下一个文件。"""

    Path(target).write_text("executed", encoding="utf-8")
    return target


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    """四种 .pt 各造一份，尺寸取 resnet18 以控制测试耗时。"""

    root = tmp_path_factory.mktemp("checkpoints")
    model = torchvision.models.resnet18(weights=None)
    model.fc = torch.nn.Linear(model.fc.in_features, 5)
    model.eval()

    torch.save(model.state_dict(), root / "state_dict.pt")
    torch.save(model, root / "whole_model.pt")
    torch.jit.save(
        torch.jit.trace(model, torch.randn(1, 3, 224, 224)), root / "torchscript.pt"
    )
    torch.save(
        {
            "epoch": 3,
            "model": model.state_dict(),
            # optimizer 也是字典，且键名排在 model 之后但常被误取；这里刻意带上
            "optimizer": {"state": {}, "param_groups": [{"lr": 0.001}]},
            "best_top1": 0.91,
        },
        root / "checkpoint.pt",
    )
    return load_tool(), root


@pytest.fixture(scope="module")
def timm_sample(tmp_path_factory):
    """timm 架构的存档：真实交付的 best.pt 就是这一类（resnet50d）。"""

    pytest.importorskip("timm", reason="重建 timm 架构需要 timm")
    import timm

    root = tmp_path_factory.mktemp("timm")
    model = timm.create_model("resnet18", pretrained=False, num_classes=7)
    torch.save({"epoch": 2, "model": model.state_dict()}, root / "timm.pt")
    return load_tool(), root


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("state_dict.pt", "state_dict"),
        ("whole_model.pt", "whole_model"),
        ("torchscript.pt", "torchscript"),
        ("checkpoint.pt", "training_checkpoint"),
    ],
)
def test_save_format_is_identified(samples, filename, expected):
    tool, root = samples
    assert tool.inspect(root / filename)["kind"] == expected


def test_state_dict_exposes_no_architecture(samples):
    """只有张量时不能谎称能还原结构，否则会漏掉"再要一份模型定义"这一步。"""

    tool, root = samples
    info = tool.inspect(root / "state_dict.pt")
    assert info["has_tensors"] is True
    assert info["module_classes"] == []


def test_whole_model_exposes_architecture(samples):
    tool, root = samples
    info = tool.inspect(root / "whole_model.pt")
    assert any("torchvision.models.resnet" in name for name in info["module_classes"])


def test_torchscript_is_reported_loadable(samples):
    tool, root = samples
    assert tool.inspect(root / "torchscript.pt")["torchscript_loadable"] is True
    assert tool.inspect(root / "state_dict.pt")["torchscript_loadable"] is False


def test_every_verdict_points_to_onnx(samples):
    """四种格式的结论都必须落到"转 ONNX"，否则等于没给下一步。"""

    tool, root = samples
    for name in ("state_dict.pt", "whole_model.pt", "torchscript.pt", "checkpoint.pt"):
        lines = tool.verdict(tool.inspect(root / name))
        assert any("ONNX" in line for line in lines), f"{name} 的结论里没提 ONNX"


def test_inspection_never_unpickles(tmp_path):
    """判定只看指令流：样本里埋的执行代码不能被触发。"""

    tool = load_tool()
    marker = tmp_path / "pwned.txt"

    class Payload:
        def __reduce__(self):
            return (_marker_probe, (str(marker),))

    blob = pickle.dumps(Payload(), protocol=2)

    # 先确认这份样本真的会执行代码，否则下面的断言是空的
    pickle.loads(blob)
    assert marker.exists(), "样本本身不具备执行能力，这个用例等于没测"
    marker.unlink()

    target = tmp_path / "evil.pt"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("evil/data.pkl", blob)

    info = tool.inspect(target)
    assert not marker.exists(), "inspect 反序列化了权重，等于执行了文件里的任意代码"
    assert info["container"] == "zip"


def test_export_onnx_round_trips_through_onnxruntime(samples, tmp_path):
    """state_dict 缺结构，但配 torchvision 的 ResNet 就能还原并导出。"""

    tool, root = samples
    info = tool.inspect(root / "state_dict.pt")
    out = tmp_path / "dish.onnx"
    result = tool.export_onnx(root / "state_dict.pt", out, info, arch="resnet18")

    assert out.is_file()
    assert result["output_shape"] == ["batch", 5]
    assert result["class_count"] == 5


def test_export_onnx_infers_class_count_from_head(samples, tmp_path):
    """类别数不传也要能从 fc 权重推出来，否则默认 1000 类会加载失败。"""

    tool, root = samples
    info = tool.inspect(root / "state_dict.pt")
    result = tool.export_onnx(
        root / "state_dict.pt", tmp_path / "auto.onnx", info, arch="resnet18"
    )
    assert result["class_count"] == 5

def test_unwrap_skips_optimizer(samples):
    """optimizer 也是字典，取错会拿到 state/param_groups，加载必然失败。"""

    tool, root = samples
    payload = torch.load(root / "checkpoint.pt", map_location="cpu", weights_only=False)
    picked = tool.unwrap_state_dict(payload)
    assert tool.is_tensor_map(picked)
    assert "fc.weight" in picked


def test_is_tensor_map_rejects_optimizer_shape(samples):
    tool = load_tool()
    assert tool.is_tensor_map({"a": torch.zeros(2)}) is True
    assert tool.is_tensor_map({"state": {}, "param_groups": []}) is False
    assert tool.is_tensor_map({}) is False


def test_export_from_checkpoint_keeps_epoch_meta(samples, tmp_path):
    """带 optimizer 的 checkpoint 要能一路导到 ONNX，并把轮次记进 manifest。"""

    tool, root = samples
    info = tool.inspect(root / "checkpoint.pt")
    result = tool.export_onnx(
        root / "checkpoint.pt", tmp_path / "ckpt.onnx", info, arch="resnet18"
    )
    assert result["output_shape"] == ["batch", 5]
    assert result["manifest"]["checkpoint_meta"]["epoch"] == 3
    assert result["manifest"]["checkpoint_meta"]["best_top1"] == 0.91


def test_arch_hint_is_reported(tmp_path):
    """架构名藏在 checkpoint 的 args 里，判定要能捞出来并提示用 --timm。"""

    tool = load_tool()
    path = tmp_path / "hint.pt"
    torch.save(
        {
            "epoch": 1,
            "model": {"fc.weight": torch.zeros(3, 4)},
            "args": {"model": "resnet50d.ra2_in1k"},
        },
        path,
    )
    info = tool.inspect(path)
    assert "resnet50d.ra2_in1k" in info["arch_hints"]
    assert any("resnet50d" in line for line in tool.verdict(info))


def test_export_with_timm_writes_preprocess_contract(timm_sample, tmp_path):
    """timm 架构必须走 --timm 重建，且预处理契约要落进 manifest。"""

    tool, root = timm_sample
    info = tool.inspect(root / "timm.pt")
    out = tmp_path / "t.onnx"
    result = tool.export_onnx(root / "timm.pt", out, info, timm_name="resnet18")
    manifest = result["manifest"]

    assert out.is_file()
    assert (tmp_path / "t.manifest.json").is_file()
    assert manifest["arch"] == "resnet18"
    assert manifest["arch_source"] == "timm"
    assert manifest["num_classes"] == 7
    assert manifest["preprocess"]["mean"] == [0.485, 0.456, 0.406]
    assert manifest["preprocess"]["std"] == [0.229, 0.224, 0.225]
    assert manifest["output"]["shape"] == ["batch", 7]
    assert "类别清单" in manifest["labels"]
