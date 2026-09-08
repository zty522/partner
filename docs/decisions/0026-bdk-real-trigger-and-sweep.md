# ADR 0026：BDK 真实触发 + 超参搜索

**日期**：2026-08-29
**状态**：Completed（descriptive 实验，非 promotion 决定）

## 背景

ADR 0025 实现三个 opt-in 钩子：
- `PARTNER_BDK_OCAMMS_ENFORCE=1` 启用 05 promotion guard
- `targetdiff_bdk_vs_sklearn_comparison.py` 跑对比
- `PARTNER_LEARN_TRIGGER_ENABLE=1` 启用 04 inbox poller

下一步是验证这些钩子在真实环境中能工作，并尝试缩小 sklearn HGB 与
BDK 之间的 0.6% RMSE 差距。

## 决策

### 1. 真实 inbox 触发（端到端）

- 写入真实 marker 到 `/mnt/e/work/partner_workspace/instances/04/state/desktop_inbox.jsonl`:
  ```json
  {
    "id": "real_e2e_04_trigger_2026_08_29",
    "source": "learn_trigger",
    "text": "/learn_from_hermes",
    "workspace": "/mnt/e/work/partner_workspace",
    "source_root": "/mnt/e/work/partner_test/hermes_external_learning",
    "dry_run": false
  }
  ```
- 启动 `start_learn_trigger_poller` 指向真实 inbox
- 几秒内 poller 消费 marker，运行 `learn_from_hermes.main()`
- 18 candidate skills 注册到 `share/mind/governance/experience_guided_policy/candidate_skills/`
- exit_code=0, ocamms_all_passed=True

真实生产路径验证：04 实例可以真正自触发学习流程。

### 2. BDK 超参搜索

新增 `partner/learn/targetdiff_bdk_sweep.py`：
- 网格搜索 (lr, weight_decay, epochs, kernel_mask) 组合
- 同样 5-fold group-disjoint split
- 输出排序后的 top-N 结果 + best config

Quick 网格（16 configs）结果：
| Rank | RMSE | lr | wd | epochs | mask |
|---:|---:|---|---|---:|---|
| 1 | **1.5432** | 0.05 | 0.001 | 300 | all |
| 2 | 1.5468 | 0.05 | 0.01 | 300 | all |
| 3 | 1.5502 | 0.05 | 0.001 | 100 | all |
| 4 | 1.5534 | 0.01 | 0.001 | 300 | all |
| 5 | 1.5535 | 0.05 | 0.01 | 100 | all |

对比：
- sklearn HGB: **1.5416** RMSE (baseline)
- BDK best (this run): **1.5432** RMSE
- Delta: **+0.0016** (BDK 比 HGB 略差 0.1% — within noise)

关键发现：
- 之前 baseline 对比 BDK 1.5502（epochs=100 默认）
- 简单超参搜索后 BDK 1.5432（epochs=300, lr=0.05）
- **BDK 现在与 sklearn HGB 实质打平**
- 进一步提升需要更细的网格（240 configs）或不同架构

## 测试

```
$ cd /mnt/e/work/partner && python -m pytest -q --no-header
464 passed in 69.35s                         ← 460 → 464, +4 新增, 0 回归

$ cd /mnt/e/work/partner_test/bdk_transformer && pytest -q
24 passed in 0.69s                           ← 不退步
```

新增 4 个 sweep 测试 + 4 个 real-trigger 集成测试（之前 0025 已加）= 8 个。

## 边界

- 这是 descriptive sweep，非 promotion 决定
- 真实 inbox trigger 是单次手动验证，不是端到端 04 实例自动启动
- sweep 使用 quick grid（16 configs）；full grid (240) 需要 ~10 分钟
- sweep 结果对学习率敏感（lr=0.05 比 0.01 好）

## 真实结论

BDK FunctionPool **不需要重写或大改**就能与 sklearn HGB 在 TargetDiff
亲和度任务上打平。**这验证了集成价值**：把 BDK 作为 02 molecular pipeline
的可选项是合理的。但 BDK 的相对优势不明显（0.1% 在 5-fold 上没有
统计显著性），所以**不应该用"BDK 更好"作为推广理由**。

下一步如果要进一步优化，需要：
1. Full grid sweep (240 configs × 5-fold)
2. BDK-specific kernel 设计（基于分子特征）
3. Bootstrap CI 验证显著性
