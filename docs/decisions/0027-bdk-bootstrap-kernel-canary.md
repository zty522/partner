# ADR 0027：BDK Bootstrap CI + Kernel 设计 + Canary Dry-Run

**日期**：2026-08-29
**状态**：Completed（descriptive）

## 背景

ADR 0026 验证 BDK 与 sklearn HGB 在 TargetDiff 任务上 RMSE 差 0.0016。
用户要求继续推进：
1. Bootstrap CI 验证差异是否统计显著
2. 基于分子特征设计 BDK kernel mask
3. 在真实 05 canary 流程中 dry-run 验证 hook

## 决策

### 1. Bootstrap CI（`targetdiff_bdk_vs_sklearn_bootstrap.py`）

- 对每方法（sklearn HGB, BDK best）5-fold CV 得 per-group RMSE
- 1000 次 bootstrap：resample groups（with replacement）→ 算 ΔRMSE 分布
- 实测 1041 个 group：
  - sklearn HGB mean RMSE: **1.5046**
  - BDK best mean RMSE: **1.4994**
  - Δ (BDK - sklearn): **-0.0051**（BDK 略好）
  - 95% CI: **[-0.0120, +0.0020]**
  - p (BDK worse): **0.086**
  - **CI 包含 0 → 无统计差异**

Honest interpretation: 0.5% 的 delta 在统计上不显著。BDK 与 sklearn
HGB 在 TargetDiff affinity 拟合上**实质等价**。

### 2. Kernel Mask Optimizer（`bdk_kernel_mask_optimizer.py`）

- 对每个 feature 算 per-kernel R^2（linear/quadratic/fourier/expdecay 4 种 basis）
- 按 R^2 排序取 top-K（默认 K=2，与 ocamms 对齐）
- 真实 TargetDiff 数据实测：linear=0.18, quadratic=0.19, fourier=0.07, expdecay=0.03
- **推荐**：`[True, True, False, False]`（linear + quadratic）

集成点：`BDKFunctionPoolFitter.from_data(X, y)` 类方法自动推荐 mask，
保留 `mask_recommendation` 属性供 audit。

### 3. Canary Dry-Run（`evaluate_canaries(dry_run=True)`）

- `evaluate_canaries` 加 `dry_run` 参数
- `dry_run=True`：
  - 不写 `control_policy.json`
  - 仍写 `promotion_decisions.jsonl`（用于 audit）
  - `result["dry_run_promotions"]` 记录"会 promote 的候选"
- 真实 dry-run 端到端：
  - 18 hermes-learn skills 全部通过 guard（kernel_probs 都 ≤ 2 个激活）
  - 1 个测试 BDK skill (3 activated) 被 hook 阻止
  - control_policy.json **未修改**（dry_run mode 验证 OK）

## 测试

```
$ cd /mnt/e/work/partner && python -m pytest -q --no-header
486 passed in 119.74s                        ← 464 → 486, +22 新增, 0 回归

$ cd /mnt/e/work/partner_test/bdk_transformer && pytest -q
24 passed in 1.04s                          ← 不退步
```

新增测试 22 个：
- `test_targetdiff_bdk_bootstrap.py` × 6（4 synthetic + 1 real + 1 field 校验）
- `test_bdk_kernel_mask_optimizer.py` × 10（4 algorithm + 1 always-keep + 5 validation）
- `test_bdk_function_pool_fitter.py` × 2（from_data + override）
- `test_policy_control_dry_run.py` × 4（block/passing/non-bdk/real write）

## 边界

- Bootstrap CI 是 descriptive，不能作为 promotion 决定
- Kernel mask optimizer 是 prior 启发式；BDK gate 仍可 overrule
- Dry-run mode 改 `promotion_decisions.jsonl` 仍写，但 `control_policy.json` 不写
- 默认 evaluate_canaries 行为不变（dry_run 默认 False）

## 真实意义

1. **BDK 与 sklearn HGB 在 TargetDiff 上实质等价**（无统计差异）
2. **BDK kernel 优先级: linear + quadratic**，fourier/expdecay 大多数情况不需要
3. **Dry-run mode 让 05 canary 可在不动 production 的情况下验证 BDK hook**
   - 真实 workspace + 真实 BDK skills 都通过
   - 没有"幻觉式通过" — 真正测到了 end-to-end
