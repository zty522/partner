# ADR 0033：第一次真实 Event-first BDK 匹配实验

**日期**：2026-08-29  
**状态**：Completed / Inconclusive  
**生产影响**：无

## 问题与预注册假设

问题：BDK FunctionPool 在 02 TargetDiff affinity 回归中是否比现有 sklearn baseline 更有用？

假设：使用相同 `affinity_info.pkl`、`[vina, rmsd]` 特征、`sha256(group)%5` group-disjoint 五折时，
BDK mean RMSE 至少比最佳 sklearn mean RMSE 低 0.02。晋升还要求复杂度检查通过，并至少有三个独立
匹配运行；单次结果无论方向如何都不晋升。

## 执行链

```text
issue/recorded
→ experiment/started
→ candidate/proposed
→ targetdiff_sklearn_affinity_baseline Event
→ execute_candidate Event
→ targetdiff_bdk_function_pool Event
→ candidate/execution_completed
→ experiment/completed
→ policy/inconclusive
```

- Experiment：`experiment_60f044790615`
- Candidate：`candidate_targetdiff_bdk_60f044790615`
- 输入：76,803 行记录，聚合为 63,123 个 ligand；输入 SHA256
  `2f99f361f77f41a6e0252aa2692470a4d433eeef169aac5ff129caf79e050cf9`
- BDK：200 epochs/fold，5 folds；所有折 `group_overlap=0`
- Event ledger：7 events，hash verification passed

## 结果

| 方法 | Mean RMSE |
|---|---:|
| sklearn LinearRegression | 1.600721 |
| sklearn HistGradientBoosting | **1.541555** |
| BDK FunctionPool | 1.544721 |

`BDK - sklearn best = +0.003166`：BDK 比 HGB 差约 0.21%，没有达到预注册的 `-0.02` 改善门；但比纯线性
低 0.056001。BDK 平均 kernel probabilities：Linear 0.06296、Quadratic 0.05627、Fourier 0.87862、
ExpDecay 0.00215；Ocamms 检查通过。

## 决策与解释

决策为 `inconclusive`，`production_effective=false`。这说明 BDK Event/Candidate 执行链真实可用，也说明
FunctionPool 能学到强非线性结构；但在本任务上没有预测优势，不能替换 HGB。Kernel 权重是模型内部选择，
不是贝叶斯后验，也不能证明 affinity 具有真实周期因果结构。

BDK 当前价值是可审计的结构假设器/可替换数值模型，而不是更强回归器或 Agent 底层。下一次实验应先冻结
训练前归一化、复杂度惩罚和多 seed 方案，再做独立重复；不得在 held-out folds 上反复调参。

## 证据

- `/mnt/e/work/partner_workspace/instances/02/state/tasks/bdk_event_first_20260829_220759/event_first_bdk_experiment_result.json`
- `/mnt/e/work/partner_workspace/instances/02/state/tasks/bdk_event_first_20260829_220759/event_first_bdk_experiment_report.md`
- `/mnt/e/work/partner_workspace/share/mind/governance/evolution_events.jsonl`
- `scripts/run_bdk_event_first_experiment.py`
