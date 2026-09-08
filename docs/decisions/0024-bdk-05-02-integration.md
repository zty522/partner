# ADR 0024：05/02 BDK 集成接入

**日期**：2026-08-28
**状态**：Accepted；opt-in 接入
**上下文**：`partner_test/bdk_transformer/INTEGRATION_PLAN.md` 阶段 2

## 背景

Hermes 已实现 04 实例的 `learn_from_hermes.py`（`from bdk import ...` 形式）
和 BDK ocamms promotion guard（ADR 0023）。下一步是把 BDK 真正接入 05 实例
的 promotion 流程和 02 实例的 molecular pipeline。

## 决策

### 05 Promotion Hook（`decide_experiment`）

- 修改 `partner/governance/evolution_loop.py` 的 `decide_experiment`：
  - 在模块顶部注入 BDK 路径 `sys.path.insert(0, "/mnt/e/work/partner_test/bdk_transformer/src")`
  - 当 `params["enforce_bdk_ocamms"]=True` AND `decision="promoted"` 时调用
    `partner.learn.bdk_ocamms_promotion_guard`
  - `blocked=True` → 返回 `{"ok": False, "status": "bdk_ocamms_blocked", ...}`
  - `passed=True` → 把 guard verdict 追加到 `decision.evidence`，继续 promote
  - 非 BDK candidate → verdict `not_applicable=True`，不阻塞
  - `enforce_bdk_ocamms=False` 或缺失 → 完全保留原有行为（向后兼容）
- 必需参数：`enforce_bdk_ocamms=True` + `candidate_id="..."`。缺 candidate_id
  时 soft-pass（log 而不阻塞）。

### 02 BDK FunctionPool Stage（`targetdiff_bdk_function_pool`）

- 新增 `partner/v2/targetdiff_bdk_events.py`：
  - 不修改 `targetdiff_continuous_events.py` 的 Stage 9–13（保留 sklearn baseline）
  - 新 HANDLER `targetdiff_bdk_function_pool` 平行注册
  - 同样的 5-fold group-disjoint split（`sha256(group)%5`）
  - 同样的特征（vina, rmsd）和目标（pk）
  - 用 `partner.learn.bdk_function_pool_fitter.BDKFunctionPoolFitter` 训练
  - 产出 `mean_bdk_rmse`、`mean_kernel_probs`、`ocamms_all_passed` + 4 个文件
    (.py 源码 + .json 结果 + .md 报告 + 可选 .pdf)
  - BDK 不可用时返回 `bdk_unavailable`，不影响原 pipeline

## 测试

```
$ cd /mnt/e/work/partner && python -m pytest -q --no-header
446 passed in 42.14s                          ← 431 → 446, +15 新增, 0 回归
```

新增测试：
- `tests/test_bdk_ocamms_promotion_hook.py`：9 个测试覆盖 opt-in/必填/
  blocking/passing/记录 verdict/非 BDK 不阻塞/rejected/inconclusive/未知 candidate
- `tests/test_targetdiff_bdk_events.py`：6 个测试覆盖 handler 注册/
  BDK 路径注入/不可用降级/缺数据/端到端真实数据运行/不替换原 pipeline

## 边界

- 05 hook 默认关闭（`enforce_bdk_ocamms=False`），现有 14 个 canary
  流程无修改
- 02 BDK stage 不修改 Stage 9–13 的 sklearn baseline；新 handler 平行存在
- BDK 路径硬编码为 `/mnt/e/work/partner_test/bdk_transformer/src`；
  移到其他环境需修 `evolution_loop.py` 和 `targetdiff_bdk_events.py` 顶部常量

## 真实数据验证

02 BDK stage 跑真实 TargetDiff 数据（76803 行, 5-fold）：
- mean_bdk_rmse = 1.5692
- mean_kernel_probs = [0.031, 0.063, 0.904, 0.002]（fourier 主核）
- ocamms_all_passed = True（每折都 ≤ 2 个激活核）

这与 sklearn baseline 不可直接比较（baseline 跑在 stage 9，BDK 跑在新
handler；两组结果在独立 .json 文件里）。02 若要做对比，需要同时跑两边。

## 后果

- 05 实例 promotion 时只要 caller 显式传 `enforce_bdk_ocamms=True` +
  `candidate_id` 就会自动调用 BDK ocamms guard
- 02 实例可以通过 atomic event `targetdiff_bdk_function_pool` 调用 BDK
  FunctionPool 跑 affinity 拟合，无需修改现有 Stage 9–13 流程
- 两个 hook 都是 BDK 不可用时 fail-open（02 fail-closed 因为 stage
  本身需要 BDK；05 在 enforce=False 时 fail-open）
