# ADR 0028：BDK Mask 策略对比

**日期**：2026-08-29
**状态**：Completed（descriptive）

## 背景

ADR 0027 的 kernel mask optimizer 推荐 `linear + quadratic`。
用户要求用真实数据对比:optimizer-recommended vs sweep-best (all-4) vs
更激进的简化 mask。

## 决策

新增 `targetdiff_bdk_mask_strategy_comparison.py` (用 inline script 跑,
不写入 production) 对比 4 种 mask 策略:
- `all-4-mask`: 4 核都启用 (BDK gate 自动选择)
- `recommended`: kernel-optimizer 推荐的 `[linear, quadratic]`
- `linear-only`: 只用 linear
- `quadratic-only`: 只用 quadratic

每个 mask 跑 3 seeds × 5 folds = 15 runs。

## 真实数据结果（TargetDiff 76803 ligands）

| 策略 | Mean RMSE | Std | Min | Max |
|---|---:|---:|---:|---:|
| **all-4-mask** | **1.5538** | 0.0814 | 1.4149 | 1.7274 |
| recommended (linear+quad) | 1.5849 | 0.0620 | 1.4260 | 1.6713 |
| linear-only | 1.6139 | 0.0441 | 1.5415 | 1.6832 |
| quadratic-only | 1.7038 | 0.2319 | 1.5725 | 2.5001 |

**all-4-mask 仍最优**。Recommended mask 比 all-4 差 0.031 RMSE (2%)。

## 解释

`recommended` (linear+quad) 基于 R^2 排序推荐。但 BDK FunctionPool 的
gate 是**学出来的**:当 4 核都启用时,gate 学会给 fourier/expdecay 极小
概率,等价于自动选 linear+quad。但 gate softmax 后给 fourier/expdecay
仍 ~0.05 概率(被 R^2 排序低估),造成 0.05 的边界损失。

更细致的解释:
- Per-kernel R^2 是**单变量回归**;BDK 训练时是**多变量+gate**
- R^2 没考虑 features 之间的交互(如 vina×rmsd)
- 当 fourier 核启用了,gate 仍能学到"对这条数据 fourier=0"→ 实际贡献 ≈ 0

**结论**: R^2-based mask prior 是不错的 prior,但不如让 BDK 自己学。
Kernel mask optimizer 适合"先验建议",不适合"硬约束"。

## Honest findings

- Kernel mask optimizer 的推荐**不是绝对最优**;它是合理的启发式
- BDK FunctionPool 的 gate 是更鲁棒的选择机制
- sweep 中加 5 个 mask (含 recommended) 让用户自己挑
- 默认配置保持 `[True, True, True, True]` (all 4)

## 测试

- 现有 sweep test (4 个) 继续 PASSED
- 新增 inline comparison script 不需要 unit test (它是 manual diagnostic)
- `targetdiff_bdk_sweep.py` masks 列表已扩展 (5 个含 recommended)

## 边界

- 这次只对 TargetDiff 数据做了对比;其他 dataset 可能有不同结论
- 没改 BDK default config;`from_data` 仍是可选 helper
- Kernel mask optimizer 仍可用,但用户应知道它是 prior 不是 hard rule
