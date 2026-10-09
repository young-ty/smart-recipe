"""对照实验：搞清「提分辨率」和「换模型」哪个才是瓶颈。

这是实验报告中「模型选型」部分的对应代码。它本身不训练，而是依次用
train.py 跑 4 组配置各 5 个 epoch，再用真实 val 曲线选出最终骨干。

━━━ 实验设计 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
起点：resnet50d.ra2_in1k @224，20 epoch，测试集 top-1 = 79.71%，
并且已经彻底收敛（val 从第 12 epoch 起就在 79.0±0.2 横盘 8 个 epoch）。
所以再加 epoch 没用，必须换东西。两个候选杠杆：

  A. 提分辨率：数据集图片中位数 600x600，而训练只看 224x224，等于丢掉了
     86% 的像素。对「大米粥 vs 小米粥」这种细粒度任务，细节就是判别力。
     佐证：timm 官方 resnet50d 在 224 是 80.53%，换 288 评测就是 81.37%。

  B. 换模型：convnext_base.fb_in22k_ft_in1k（ImageNet-22k 预训练，88.6M）
     官方 top-1 85.82%，比 resnet50d 的 80.53% 高 5.3 个点。

与其拍脑袋烧 7~14 小时，不如每组跑 5 epoch（约 6.5 小时）用真实 val 曲线选型。
4 组构成一个 2x2 消融：分辨率(224/320 或 288) x 骨干(resnet50d / convnext_base)。

━━━ 实测结果（val top-1 %，每 epoch） ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
配置        模型                   img   bs    ep1     ep2     ep3     ep4     ep5
r50d-224    resnet50d.ra2_in1k     224   32   68.09   71.80   74.52   75.35   75.80
r50d-320    resnet50d.ra2_in1k     320   32   70.41   74.72   76.62   77.84   77.96
cnxb-224    convnext_base          224   32   76.95   80.63   82.25   83.05   83.20
cnxb-288    convnext_base          288   16   78.14   80.88   83.03   83.99   84.16

归因（相对基线 r50d-224 的 75.80%）：
    只提分辨率 (r50d-320) : +2.17
    只换模型   (cnxb-224) : +7.40   ← 主要杠杆
    两个都上   (cnxb-288) : +8.36   （< 2.17+7.40=9.57，说明两者收益重叠）

结论：**换骨干是主要矛盾，提分辨率是次要的**。据此选定 convnext_base。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

一个已知的变量不纯净：cnxb-288 因为显存限制用了 bs16，其余三组是 bs32
（convnext_base @288 bs32 实测峰值 8.6GB，8GB 卡放不下）。AdamW 对 batch size
不如 SGD 敏感，且四组 lr 都固定 1e-4，但解读 cnxb-288 那 +0.96 时这点要记在心里
——它的优化步数是其他组的两倍。

用法：
    python pilot.py                # 全部 4 组
    python pilot.py --only 0 3     # 只跑第 1、4 组
    python pilot.py --epochs 3     # 改轮数（默认 5）
    python pilot.py --summary      # 不训练，只打印已有结果的对比表

在中途被打断不要紧：再跑一次会自动跳过已完成的组、从断点续跑未完成的组。
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path


def _find_project_root(start: Path) -> Path:
    """向上查找含 config.py / dataset.py 的目录，使本文件可放在任意子目录。"""
    for p in (start, *start.parents):
        if (p / "config.py").exists() and (p / "dataset.py").exists():
            return p
    raise RuntimeError(f"从 {start} 向上找不到项目根（缺少 config.py / dataset.py）")


PROJECT_ROOT = _find_project_root(Path(__file__).resolve().parent)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import RUNS_DIR  # noqa: E402

# 与本文件同目录的 train.py；不用项目根那份，保证实验代码自洽
TRAIN_PY = Path(__file__).resolve().parent / "train.py"

# ══════════════════════════════════════════════════════════════════════
#  对照实验参数（2x2 消融，其余超参与 train.py 一致：AdamW lr=1e-4 / wd=1e-4
#  / label_smoothing=0.1 / Mixup 0.2 + CutMix 0.2）
#  (短名, 模型, 分辨率, batch size)。短名会成为 run 目录名 pilot-<短名>。
# ══════════════════════════════════════════════════════════════════════
CONFIGS = [
    ("r50d-224", "resnet50d.ra2_in1k", 224, 32),               # 基线：与已完成的 20ep 对齐
    ("r50d-320", "resnet50d.ra2_in1k", 320, 32),               # 只提分辨率
    ("cnxb-224", "convnext_base.fb_in22k_ft_in1k", 224, 32),   # 只换模型
    ("cnxb-288", "convnext_base.fb_in22k_ft_in1k", 288, 16),   # 两个都上（显存所限 bs16）
]

EPOCHS = 5            # 每组轮数
NUM_WORKERS = 8

# 各配置单 epoch 耗时（本机实测，纯 GPU 瓶颈），仅用于启动前给个心理预期
MIN_PER_EPOCH = {"r50d-224": 8.1, "r50d-320": 16.4, "cnxb-224": 20.2, "cnxb-288": 32.7}
# ══════════════════════════════════════════════════════════════════════


def parse_args():
    p = argparse.ArgumentParser(description="分辨率 vs 模型 对照实验")
    p.add_argument("--epochs", type=int, default=EPOCHS)
    p.add_argument("--only", type=int, nargs="+", default=None,
                   help="只跑指定序号的组（从 0 开始）")
    p.add_argument("--summary", action="store_true", help="只打印对比表，不训练")
    p.add_argument("--num-workers", type=int, default=NUM_WORKERS)
    return p.parse_args()


def run_dir_of(short):
    return RUNS_DIR / f"pilot-{short}"


def progress(run_dir):
    """返回 (已完成 epoch 数, 最佳 val_top1, 总耗时秒)。没有产物就返回 (0, 0, 0)。"""
    m = run_dir / "metrics.csv"
    if not m.exists():
        return 0, 0.0, 0.0
    rows = list(csv.DictReader(m.open(encoding="utf-8")))
    if not rows:
        return 0, 0.0, 0.0
    return len(rows), max(float(r["val_top1"]) for r in rows), \
        sum(float(r["secs"]) for r in rows)


def run_one(idx, short, model, img_size, batch_size, epochs, workers):
    """跑一组。已跑完的跳过，跑了一半的用 --resume 接着跑。"""
    run_dir = run_dir_of(short)
    done, best, _ = progress(run_dir)

    if done >= epochs:
        print(f"[{idx}] {short:<10} 已完成 {done}/{epochs} epoch"
              f"（best val_top1 {best*100:.2f}%），跳过")
        return True

    cmd = [sys.executable, str(TRAIN_PY),
           "--model", model, "--img-size", str(img_size),
           "--batch-size", str(batch_size),
           "--epochs", str(epochs), "--num-workers", str(workers),
           "--run-name", f"pilot-{short}"]

    if done > 0:
        # train.py --resume 会恢复 epoch/optimizer/scheduler 并从下一个 epoch
        # 继续，配合同样的 --epochs，续跑语义正确
        ckpt = run_dir / "last.pt"
        if ckpt.exists():
            cmd += ["--resume", str(ckpt)]
            print(f"[{idx}] {short:<10} 已有 {done}/{epochs} epoch，从第 {done+1} 个续跑")
        else:
            print(f"[{idx}] {short:<10} 有 metrics 但没有 last.pt，只能从头重跑")
    else:
        print(f"[{idx}] {short:<10} 全新开始")

    est = MIN_PER_EPOCH[short] * (epochs - done)
    print(f"      模型 {model}  img={img_size}  bs={batch_size}  "
          f"预计还需 {est:.0f} 分钟({est/60:.1f} 小时)")
    print(f"      日志 runs/pilot-{short}.log\n")

    log_path = RUNS_DIR / f"pilot-{short}.log"
    t0 = time.time()
    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(f"\n{'='*70}\n{' '.join(cmd)}\n{'='*70}\n")
        logf.flush()
        # stderr 走日志文件：tqdm 进度条写 stderr，直接放到控制台会刷屏。
        # stdout 同时进控制台和日志，所以每 epoch 汇总两种地方都能看到。
        proc = subprocess.Popen(
            cmd, cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=logf,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env={**os.environ, "PYTHONIOENCODING": "utf-8",
                 "PYTHONUNBUFFERED": "1"})
        for line in proc.stdout:
            sys.stdout.write(line)
            logf.write(line)
        proc.wait()

    dt = time.time() - t0
    done, best, _ = progress(run_dir)
    if proc.returncode != 0:
        print(f"\n[{idx}] {short} 失败（退出码 {proc.returncode}），"
              f"详见 {log_path}。后面的组继续跑。\n")
        return False
    print(f"[{idx}] {short} 完成：{done}/{epochs} epoch，"
          f"best val_top1 = {best*100:.2f}%，用时 {dt/60:.1f} 分钟\n")
    return True


def print_summary(epochs):
    """把 4 组的每 epoch val_top1 拉成一张表，直接看谁赢。"""
    print("=" * 78)
    print("对照实验汇总（每格是对应 epoch 的 val top-1 %）")
    print("=" * 78)
    header = f"{'配置':<12}{'模型':<16}{'img':>5}{'bs':>4}  " + \
             "".join(f"{f'ep{i+1}':>8}" for i in range(epochs))
    print(header)
    print("-" * len(header))

    results = {}
    for short, model, img_size, bs in CONFIGS:
        m = run_dir_of(short) / "metrics.csv"
        if not m.exists():
            print(f"{short:<12}{model[:14]:<16}{img_size:>5}{bs:>4}   （没有结果）")
            continue
        rows = list(csv.DictReader(m.open(encoding="utf-8")))
        vals = [float(r["val_top1"]) * 100 for r in rows]
        results[short] = vals
        mshort = model.split(".")[0][:14]
        cells = "".join(f"{v:>8.2f}" if i < len(vals) else f"{'—':>8}"
                        for i, v in enumerate(vals[:epochs]))
        print(f"{short:<12}{mshort:<16}{img_size:>5}{bs:>4}  {cells}")

    if not results:
        return

    print("-" * len(header))
    # 用最后一个 epoch 和「最佳 epoch」两个口径排，避免只挑对自己有利的那个
    last = {k: v[-1] for k, v in results.items() if len(v) >= epochs}
    if last:
        print(f"\n按最后一轮（ep{epochs}）排名：")
        for k, v in sorted(last.items(), key=lambda kv: -kv[1]):
            base = last.get("r50d-224")
            delta = f"  ({v-base:+.2f} vs 基线)" \
                if base is not None and k != "r50d-224" else ""
            print(f"  {v:6.2f}%  {k}{delta}")

        base = last.get("r50d-224")
        r320, c224, c288 = (last.get("r50d-320"), last.get("cnxb-224"),
                            last.get("cnxb-288"))
        if base is not None:
            print("\n归因（相对基线 r50d-224）：")
            if r320 is not None:
                print(f"  只提分辨率 (r50d-320) : {r320-base:+.2f}")
            if c224 is not None:
                print(f"  只换模型   (cnxb-224) : {c224-base:+.2f}")
            if c288 is not None and r320 is not None and c224 is not None:
                print(f"  两个都上   (cnxb-288) : {c288-base:+.2f}"
                      f"   （小于 {r320-base:+.2f} + {c224-base:+.2f} 说明两者收益有重叠）")

    print("\n各组的 train_loss 曲线在 runs/pilot-*/metrics.csv，"
          "想细看可以打开对应 log。")


def main():
    args = parse_args()
    if args.summary:
        print_summary(args.epochs)
        return

    picks = args.only if args.only is not None else range(len(CONFIGS))
    todo = [CONFIGS[i] for i in picks]

    total = sum(MIN_PER_EPOCH[s] for s, _, _, _ in todo)
    print(f"计划跑 {len(todo)} 组 x {args.epochs} epoch，"
          f"满打满算约 {total/60:.1f} 小时（实际会因续跑而减少）\n")

    t0 = time.time()
    for i, (short, model, img_size, bs) in enumerate(todo):
        try:
            run_one(i, short, model, img_size, bs, args.epochs, args.num_workers)
        except KeyboardInterrupt:
            # 打断时不要吞掉：告诉用户怎么接着跑，然后退出
            print("\n\n已被中断。重新运行 `python pilot.py` 即可从断点续跑"
                  "（已完成的组会跳过）。")
            print_summary(args.epochs)
            sys.exit(130)
        print()

    print(f"\n全部结束，总用时 {(time.time()-t0)/60:.1f} 分钟\n")
    print_summary(args.epochs)


if __name__ == "__main__":
    main()
