"""ChineseFoodNet 208 类菜品识别 —— 训练主程序。

本文件是实验报告中「训练」部分的对应代码。所有超参数集中在下方
【实验参数】区块内，改参数只需要改这一处，命令行仍可覆盖。

用法：
    conda activate dl
    python train.py                              # 直接跑 = 报告中最终实验的配置
    python train.py --epochs 5 --batch-size 16   # 临时覆盖
    python train.py --limit-batches 20           # 冒烟测试（约 1 分钟）

配套文件（同一个项目里）：
    config.py    路径常量（DATA_ROOT / MANIFEST_DIR / RUNS_DIR）与 num_classes
    dataset.py   图像变换与 Dataset
    pilot.py     先跑这个做选型对照实验，再跑本文件做最终训练

本文件放在项目任意子目录下都能跑：下面会自动向上找到含 config.py 的项目根。

━━━ 报告中对应的实验结果 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
配置        convnext_base.fb_in22k_ft_in1k @224 / bs32 / 20 epoch
产物目录    runs/final-cnxb-224/
最佳 val    82.66%（第 18 epoch）
测试集      top-1 82.84% / top-5 96.36% / macro-F1 0.8127
详见        report/实验报告.md 第 5 节「意外发现：20 epoch 反而更差」
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import argparse
import json
import sys
import time
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════
#  实验参数（报告附录 A 的出处，改这里即可）
# ══════════════════════════════════════════════════════════════════════
MODEL = "convnext_base.fb_in22k_ft_in1k"  # timm 模型名，ImageNet-22k 预训练，87.8M 参数
IMG_SIZE = 224        # 训练分辨率。对照实验证明它是次要杠杆，见 pilot.py
EPOCHS = 20           # 余弦退火的总周期长度（T_max = EPOCHS x 每 epoch 步数）
BATCH_SIZE = 32       # RTX 4060 Laptop 8GB 实测上限（@224）
LR = 1e-4             # AdamW，四组对照实验统一固定为 1e-4
WEIGHT_DECAY = 1e-4
NUM_WORKERS = 8       # 实测 8 最优：0 时 40 img/s，8 时 188 img/s（约 4.7x）
RUN_NAME = "final-cnxb-224"   # 固定目录名，便于续跑与归档

# 正则化与数据增强
LABEL_SMOOTHING = 0.1
MIXUP_ALPHA = 0.2     # timm Mixup，与 CutMix 二选一由 timm 内部按概率决定
CUTMIX_ALPHA = 0.2
# ══════════════════════════════════════════════════════════════════════


def _find_project_root(start: Path) -> Path:
    """向上查找含 config.py / dataset.py 的目录。

    这样本文件无论放在项目根、report/code/ 还是别处，都能直接运行，
    不必手动改路径或把文件拷来拷去。
    """
    for p in (start, *start.parents):
        if (p / "config.py").exists() and (p / "dataset.py").exists():
            return p
    raise RuntimeError(f"从 {start} 向上找不到项目根（缺少 config.py / dataset.py）")


PROJECT_ROOT = _find_project_root(Path(__file__).resolve().parent)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 必须在 timm 之前导入：config 顶部的 HF_HUB_OFFLINE=1 会阻止 timm 联网。
# 本项目用到的权重已在本机缓存，所以离线也能跑；若要换用未缓存的模型，
# 需要先把 config.py 里那行 HF_HUB_OFFLINE 去掉。
from config import (DATA_ROOT, MANIFEST_DIR, NUM_CLASSES, RUNS_DIR,  # noqa: E402
                    draft_size_for)

import timm  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402
from tqdm import tqdm  # noqa: E402

from dataset import FoodDataset, build_eval_transform, build_train_transform  # noqa: E402

try:
    from timm.data import Mixup
    from timm.loss import SoftTargetCrossEntropy
    TIMM_MIXUP = True
except ImportError:  # timm 版本差异导致退路
    TIMM_MIXUP = False


def parse_args():
    p = argparse.ArgumentParser(description="ChineseFoodNet 训练")
    p.add_argument("--model", default=MODEL, help="timm 模型名")
    p.add_argument("--epochs", type=int, default=EPOCHS)
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--lr", type=float, default=LR)
    p.add_argument("--weight-decay", type=float, default=WEIGHT_DECAY)
    p.add_argument("--img-size", type=int, default=IMG_SIZE)
    p.add_argument("--num-workers", type=int, default=NUM_WORKERS,
                   help="数据加载进程数，实测 8 最优；内存紧张时降到 4")
    p.add_argument("--no-mixup", action="store_true", help="关闭 Mixup/CutMix")
    p.add_argument("--run-name", default=RUN_NAME, help="产物目录名 runs/<run-name>")
    p.add_argument("--limit-batches", type=int, default=0,
                   help="只跑前 N 个 batch（冒烟测试用），0 表示不限制")
    p.add_argument("--resume", default=None, help="从 checkpoint 恢复")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return p.parse_args()


@torch.no_grad()
def evaluate(model, loader, criterion, device, limit_batches=0):
    """在 val 集上评估，返回 (loss, top1, top5)。"""
    model.eval()
    total_loss, total, top1, top5 = 0.0, 0, 0, 0
    for i, (imgs, labels) in enumerate(loader):
        if limit_batches and i >= limit_batches:
            break
        imgs = imgs.to(device, non_blocking=True)
        labels = labels.to(device, non_blocking=True)
        with torch.amp.autocast("cuda", enabled=(device == "cuda")):
            logits = model(imgs)
            # val 不做 mixup，用普通 CE
            loss = criterion(logits, labels)
        total_loss += loss.item() * labels.size(0)
        total += labels.size(0)
        maxk = min(5, logits.size(1))
        _, pred = logits.topk(maxk, dim=1, largest=True, sorted=True)
        pred = pred.t()
        correct = pred.eq(labels.view(1, -1).expand_as(pred))
        top1 += correct[0].sum().item()
        top5 += correct[:maxk].any(dim=0).sum().item()
    if total == 0:
        return 0.0, 0.0, 0.0
    return total_loss / total, top1 / total, top5 / total


def main():
    args = parse_args()
    device = args.device

    # 产物目录的选取。续跑时若没给 --run-name 却新建时间戳目录，会把同一个
    # 实验的 checkpoint 散落到两处，所以这里默认沿用 checkpoint 所在目录。
    if args.run_name:
        run_dir = RUNS_DIR / args.run_name
    elif args.resume:
        run_dir = Path(args.resume).resolve().parent
    else:
        run_dir = RUNS_DIR / time.strftime("%Y%m%d-%H%M%S")

    # 防呆：目录里已有 metrics.csv 却没给 --resume，说明是重复启动。
    # metrics.csv 只在表头不存在时写表头，第二次跑会把两次实验的曲线追加进
    # 同一张表，事后分不清哪行属于哪次。与其留下一份读不懂的结果，不如停下。
    if not args.resume and (run_dir / "metrics.csv").exists():
        raise SystemExit(
            f"\n{run_dir} 下已经有 metrics.csv，但这次没有指定 --resume。\n"
            f"  接着上次跑：加 --resume {run_dir / 'last.pt'}\n"
            f"  想重新开始：先删掉整个 {run_dir} 目录\n")

    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"设备: {device}" + (f" ({torch.cuda.get_device_name(0)})" if device == "cuda" else ""))
    print(f"输出目录: {run_dir}")

    # ---- 数据 ----
    draft = draft_size_for(args.img_size)
    train_ds = FoodDataset(MANIFEST_DIR / "train.csv", DATA_ROOT,
                           build_train_transform(args.img_size), draft_size=draft)
    val_ds = FoodDataset(MANIFEST_DIR / "val.csv", DATA_ROOT,
                         build_eval_transform(args.img_size), draft_size=draft)
    print(f"训练集 {len(train_ds)} 张 / 验证集 {len(val_ds)} 张"
          f"（img_size={args.img_size}, draft={draft}）")

    pin = device == "cuda"
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              num_workers=args.num_workers, pin_memory=pin, drop_last=True,
                              persistent_workers=args.num_workers > 0,
                              prefetch_factor=4 if args.num_workers > 0 else None)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                            num_workers=args.num_workers, pin_memory=pin,
                            persistent_workers=args.num_workers > 0,
                            prefetch_factor=4 if args.num_workers > 0 else None)

    # ---- 模型 ----
    # pretrained=True 取 ImageNet 预训练权重做迁移学习（权重已在本机缓存）
    model = timm.create_model(args.model, pretrained=True,
                              num_classes=NUM_CLASSES).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"模型: {args.model}  参数量 {n_params/1e6:.1f}M")

    # ---- 损失 ----
    use_mixup = (not args.no_mixup) and TIMM_MIXUP
    mixup_fn = None
    if use_mixup:
        mixup_fn = Mixup(mixup_alpha=MIXUP_ALPHA, cutmix_alpha=CUTMIX_ALPHA,
                         label_smoothing=LABEL_SMOOTHING, num_classes=NUM_CLASSES)
        train_criterion = SoftTargetCrossEntropy()
        print(f"启用 Mixup({MIXUP_ALPHA}) + CutMix({CUTMIX_ALPHA}) "
              f"+ label_smoothing({LABEL_SMOOTHING})")
    else:
        train_criterion = nn.CrossEntropyLoss(label_smoothing=LABEL_SMOOTHING)
        print(f"启用 label_smoothing({LABEL_SMOOTHING})（未启用 Mixup）")
    # 验证阶段始终用不带 mixup 的普通 CE
    val_criterion = nn.CrossEntropyLoss()

    # ---- 优化器与调度 ----
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr,
                                  weight_decay=args.weight_decay)
    batches_per_epoch = args.limit_batches or len(train_loader)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=args.epochs * batches_per_epoch)
    scaler = torch.amp.GradScaler("cuda", enabled=(device == "cuda"))

    start_epoch = 0
    best_top1 = 0.0
    if args.resume:
        ckpt = torch.load(args.resume, map_location=device)
        model.load_state_dict(ckpt["model"])
        optimizer.load_state_dict(ckpt["optimizer"])
        scheduler.load_state_dict(ckpt["scheduler"])
        start_epoch = ckpt["epoch"] + 1
        best_top1 = ckpt.get("best_top1", 0.0)
        # 余弦调度的 T_max 会被 scheduler.state_dict() 一起存下来、并在上面
        # load_state_dict 时覆盖掉这次构造的值。也就是说续跑时的总轮数取决于
        # 当初那次运行，而不是这次的 --epochs。两者不一致时 lr 会按错误的周期
        # 退火（极端情况：按 20 轮续跑一个原本 1 轮周期的实验，lr 全程贴着 0，
        # 训练等于白跑）。这个坑很安静，所以直接报出来。
        if scheduler.T_max != args.epochs * batches_per_epoch:
            print(f"  ⚠️  学习率周期与 checkpoint 不一致：checkpoint 里 T_max="
                  f"{scheduler.T_max}（约 {scheduler.T_max / batches_per_epoch:.1f} 个 epoch），"
                  f"本次 --epochs={args.epochs} 对应 {args.epochs * batches_per_epoch}。")
            print("      调度将沿用 checkpoint 里的周期。要改总轮数请从头训练。")
        print(f"已从 {args.resume} 恢复：完成到第 {start_epoch} 个 epoch，"
              f"best_top1={best_top1*100:.2f}%，从第 {start_epoch + 1} 个继续")

    # ---- 训练循环 ----
    metrics_path = run_dir / "metrics.csv"
    if not metrics_path.exists():
        metrics_path.write_text(
            "epoch,train_loss,train_top1,val_loss,val_top1,val_top5,lr,secs\n",
            encoding="utf-8")

    for epoch in range(start_epoch, args.epochs):
        t0 = time.time()
        model.train()
        run_loss, run_total, run_correct = 0.0, 0, 0

        pbar = tqdm(train_loader, desc=f"Epoch {epoch+1}/{args.epochs}", ncols=110)
        for i, (imgs, labels) in enumerate(pbar):
            if args.limit_batches and i >= args.limit_batches:
                break
            imgs = imgs.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            # 用未混合的标签统计训练准确率（近似值，混合样本不计入）
            plain_labels = labels

            if mixup_fn is not None:
                imgs, labels = mixup_fn(imgs, labels)

            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=(device == "cuda")):
                logits = model(imgs)
                loss = train_criterion(logits, labels)

            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()

            run_loss += loss.item() * plain_labels.size(0)
            run_total += plain_labels.size(0)
            run_correct += (logits.argmax(1) == plain_labels).sum().item()

            if i % 50 == 0:
                pbar.set_postfix(loss=f"{run_loss/max(run_total,1):.3f}",
                                 acc=f"{run_correct/max(run_total,1)*100:.1f}%",
                                 lr=f"{scheduler.get_last_lr()[0]:.2e}")

        train_loss = run_loss / max(run_total, 1)
        train_top1 = run_correct / max(run_total, 1)

        val_loss, val_top1, val_top5 = evaluate(
            model, val_loader, val_criterion, device, args.limit_batches)
        secs = time.time() - t0

        print(f"[Epoch {epoch+1}/{args.epochs}] "
              f"train_loss={train_loss:.4f} train_top1={train_top1*100:.2f}% | "
              f"val_loss={val_loss:.4f} val_top1={val_top1*100:.2f}% "
              f"val_top5={val_top5*100:.2f}% | {secs:.0f}s")
        with open(metrics_path, "a", encoding="utf-8") as f:
            f.write(f"{epoch+1},{train_loss:.6f},{train_top1:.6f},{val_loss:.6f},"
                    f"{val_top1:.6f},{val_top5:.6f},"
                    f"{scheduler.get_last_lr()[0]:.8f},{secs:.1f}\n")

        # 先更新 best_top1，再写入两个 checkpoint——否则 last.pt 里会残留
        # 上一轮的旧 best_top1，续跑时会拿它当基准，导致更差的模型覆盖掉
        # 真正更好的 best.pt。
        is_best = val_top1 > best_top1
        if is_best:
            best_top1 = val_top1

        state = {
            "epoch": epoch,
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict(),
            "best_top1": best_top1,
            "args": vars(args),
        }
        torch.save(state, run_dir / "last.pt")
        if is_best:
            torch.save(state, run_dir / "best.pt")
            print(f"  ↑ 新的最佳 val_top1 = {best_top1*100:.2f}%，已保存 best.pt")

        # 泄漏红线提醒
        if val_top1 > 0.97 and epoch >= 1:
            print("  ⚠️  警告：val_top1 异常高（>97%），请检查是否又引入了数据泄漏！")

    summary = {
        "best_val_top1": best_top1,
        "epochs": args.epochs,
        "model": args.model,
        "batch_size": args.batch_size,
        "lr": args.lr,
        "run_dir": str(run_dir),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n训练完成。最佳 val_top1 = {best_top1*100:.2f}%")
    print(f"产物目录: {run_dir}")


if __name__ == "__main__":
    main()
