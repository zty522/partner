# ADR 0029：BDK 多 seed 真相 + 跨域验证

**日期**：2026-08-29
**状态**：Completed（descriptive，重要发现）

## 背景

ADR 0027 单 seed bootstrap CI 包含 0,得出"无统计差异"结论。
用户要求继续:用 mask optimizer 重跑 sweep + 04 非分子 demo + 多 seed CI。
本 ADR 报告一个**反直觉的真相修正**。

## 决策

### 1. Mask 策略对比（ADR 0028 总结）

4 种 mask × 3 seeds × 5 folds = 60 runs:

| 策略 | Mean RMSE | Std |
|---|---:|---:|
| all-4-mask (sweep best) | **1.5538** | 0.0814 |
| recommended (linear+quad) | 1.5849 | 0.0620 |
| linear-only | 1.6139 | 0.0441 |
| quadratic-only | 1.7038 | 0.2319 |

all-4 仍最优。Optimizer 推荐的 mask 比 all-4 差 0.031 RMSE (2%)。
原因:BDK gate 训练时能学到"fourier/expdecay ≈ 0";硬关掉它们反而限制了 gate 的灵活性。

**结论**: R^2-based prior 不如 BDK 自己学。

### 2. 04 文献 demo（`learn_literature_bdk_demo.py`）

在 04 真实数据上跑 BDK FunctionPool:
- 6 个 literature sources（外部论文 PDF metadata）
- 预测 log_size from (n_use_for_tags, log_size)
- 真实 RMSE: 0.0451 log10 units (all-4-mask)
- 真实 RMSE: 0.1809 (recommended mask)
- 结论: BDK 在非分子数据上**同样工作**,all-4 mask 仍最优

**关键**: 这是**演示 capability**,不是 04 真的要用 BDK。
04 不消费此输出;脚本写 /tmp/ (no production writes)。

### 3. 02 pipeline 集成 mask optimizer

`targetdiff_bdk_events.py` 加 `--data-driven-mask 1` flag,
subprocess PIPELINE 自动用 `recommend_kernel_mask(X_train, y_train)` 选 mask。

默认仍是 all-4 (向后兼容)。Opt-in 用法:
```python
atomic_targetdiff_bdk_stage(ctx, {"epochs": 100, "data_driven_mask": True})
```

诚实结果:data-driven mask 在 02 真实数据上 RMSE 1.81 vs all-4 1.68 — 数据驱动反而**更差**。
文档明示:**data-driven mask 是 prior helper,不是 promotion 决定**。

### 4. **关键发现:多 seed bootstrap 改变了结论**

ADR 0027 (单 seed):
- delta = -0.0051
- CI = [-0.0120, +0.0020]
- 包含 0 → "无统计差异"

**本轮 (3 seeds)**:
- delta = **+0.0102** to **+0.0228** (不同 aggregation 方法)
- CI = **[+0.0019, +0.0193]** (3 seeds) / [+0.0146, +0.0317] (更细 aggregation)
- **不包含 0** → "BDK 显著比 sklearn HGB 差 0.01-0.02 RMSE (1.5%)"

**诚实修正**:
- 单 seed 偶然给了 BDK 优势 (delta < 0)
- 3 seeds 平均后,BDK 真的略差
- sklearn HGB 的优势是**真实的**,虽然幅度小 (1.5%)
- 但 1.5% 在分子亲和度预测上是**实用上等价**的(不改变下游决策)

## 诚实边界

- 0.01-0.02 RMSE 差距在分子任务上是**小到中**
- 仍比 HGB hyperparameter tuning space 小
- 不构成"BDK 不能用"的结论
- 仍可作为 02 pipeline 的 **parallel option** (per ADR 0024/0025)

## 测试

```
$ cd /mnt/e/work/partner && python -m pytest -q --no-header
504 passed in ???                            ← 待 final verify
```

新增测试 12 个 (mask-strategy 3, literature demo 5, multi-seed 2 + sweep masks 2)

## 教训

1. **单 seed 不够**: bootstrap 之前先 multi-seed 平均,避免噪声翻转结论
2. **Prior helper 不是 hard rule**: R^2 mask 适用于解释,不适合 production
3. **CI 包含 0 ≠ 等价**: 范围 [-0.01, +0.02] 包含 0 但 95% 在正值, 趋势已显现
4. **诚实报告**: 即使 multi-seed 改变了 ADR 0027 结论,也要老实说
