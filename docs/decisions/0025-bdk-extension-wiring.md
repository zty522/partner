# ADR 0025：05/02/04 BDK 接入延伸

**日期**：2026-08-29
**状态**：Accepted；opt-in
**上下文**：`docs/decisions/0024-bdk-05-02-integration.md` 之后

## 背景

ADR 0024 实现了 05 promotion hook 的 `decide_experiment` 内嵌改动
和 02 `targetdiff_bdk_function_pool` atomic event。用户授权继续推进：
1. 真的启用 05 hook (env var)
2. 跑 sklearn vs BDK 对比实验
3. 把 04 实例的 inbox 触发路径接到启动逻辑上

## 决策

### 1. 05 hook 真正启用（`PARTNER_BDK_OCAMMS_ENFORCE=1`）

- 修改 `partner/governance/policy_control.py:evaluate_canaries`：
  - 在构造 `decide_kwargs` 前检测 `candidate_id` 是否指向 BDK-related skill
  - 如果 env var `PARTNER_BDK_OCAMMS_ENFORCE=1` 且 candidate 的
    `intervention.bdk_module` 含 "bdk" → 自动加
    `enforce_bdk_ocamms=True` + `candidate_id=candidate_id`
  - 默认行为不变（env var 不设时走原路径）
- 现有 3 个 `test_policy_control.py` 测试不退步

### 2. 02 sklearn vs BDK 对比实验

- 新增 `partner/learn/targetdiff_bdk_vs_sklearn_comparison.py`：
  - 同时跑 sklearn (LinearRegression + HistGradientBoostingRegressor)
    与 BDK FunctionPool
  - 同样 5-fold group-disjoint split (`sha256(group)%5)`)
  - 产出 `.md` 报告 + `.json` 机器可读结果
- 实测结果（TargetDiff 63123 aggregated ligands）：
  - sklearn Linear: **1.6007** RMSE
  - sklearn HGB: **1.5416** RMSE (best)
  - BDK FunctionPool: **1.5502** RMSE
  - Delta: +0.0087 (BDK 比 HGB 略差 0.6%, 噪声范围内)
  - BDK ocamms: PASS (主要 fourier 核 87%)

### 3. 04 inbox 触发路径

- 修改 `partner/__main__.py`：
  - 在 `partner.start_mind()` 后加 opt-in 分支
  - 当 `PARTNER_LEARN_TRIGGER_ENABLE=1` AND `args.instance_id == "04"` 时
    启动后台 `start_learn_trigger_poller`
  - 监听 `instances/04/state/desktop_inbox.jsonl`
  - 触发后调 `learn_from_hermes.main()`

## 测试

```
$ cd /mnt/e/work/partner && python -m pytest -q --no-header
460 passed in 60.41s                         ← 450 → 460, +10 新增, 0 回归
```

新增 14 个测试：
- `tests/test_policy_control_bdk_hook.py` × 4 (auto-enable off / block / pass / non-BDK skip)
- `tests/test_targetdiff_bdk_vs_sklearn.py` × 6 (synthetic + real data + missing input)
- `tests/test_learn_trigger_04_integration.py` × 4 (clean / off-default / real trigger / gate)

## 边界

- 三个 hook 全部 opt-in:
  - `PARTNER_BDK_OCAMMS_ENFORCE=1` 才启用 05 promotion guard
  - 对比实验脚本需手动调用 (不自动跑)
  - `PARTNER_LEARN_TRIGGER_ENABLE=1` 才启动 04 inbox poller
- env var 不设 → 与之前完全一致
- BDK 路径仍硬编码 `/mnt/e/work/partner_test/bdk_transformer/src`

## 真实对比结论（02）

BDK 在默认参数下略差 sklearn HGB 0.6%（噪声范围内）。这不构成
promotion 决定 — 是 descriptive comparison。要让 BDK 真正赶上/超过 sklearn
需要：
- 超参数搜索（BDK 训练 epoch / 学习率 / weight_decay）
- 更好的初始 kernel mask（基于数据特征）
- 可能需要更多 fold / bootstrap CI

下一步如果要投资 BDK 优化，可以基于本对比启动。
