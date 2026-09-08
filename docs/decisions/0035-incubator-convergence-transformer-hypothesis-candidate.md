# ADR 0035：孵化区收敛迁移与 Transformer HypothesisProvider Candidate

**日期**：2026-08-30  
**状态**：Accepted；Candidate 保持 shadow/inconclusive，production 不变

## 决策

1. `partner_test` 只保留历史原型、失败与 benchmark，不再承担 Partner 运行时模块。
2. 已验证的函数假设、联想记忆、Event ledger、FunctionPool 与约束器进入 Partner 自有包。
3. Partner 的所有 Python 运行时代码禁止硬编码 `/mnt/e/work/partner_test`、`import bdk` 或 `from bdk`。
4. 外部学习中采用的结论进入 `docs/knowledge/incubated_external_learning`；原始大列表留作历史 provenance，
   不再由 runtime 扫描孵化区。
5. Transformer 是 `HypothesisProvider`，只缩小候选工作集；BIC/MDL、holdout 和 Event 仍独立评价。

## 正式代码落点

- `partner/cognition/world_model/`：假设、记忆、provider、engine、事件、三领域 benchmark。
- `partner/learn/bdk_function_pool.py`、`bdk_constraints.py`：旧数值器官的 Partner-owned 兼容实现。
- `partner/v2/world_model_events.py`：Library/Transformer shadow 与 matched evaluation Event。
- `scripts/run_world_model_candidate_experiment.py`：Issue→Experiment→Candidate→execute→Policy 编排。

## 实验

冻结领域：周期传感器、物理衰减、阈值响应。两臂 dataset SHA-256：
`db14f14372b54c0838870796abd3b4d956e4cc1e0b5d32790de28f4b1b7e63bc`。

| 指标 | Library baseline | Transformer Candidate |
|---|---:|---:|
| mean holdout RMSE | 0.0036258983 | 0.0036258983 |
| family accuracy | 1.0 | 1.0 |
| mean hypotheses considered | 23.0 | 8.6667 |

Candidate 在冻结目标上胜出：精度不变，工作集减少约 62.3%。但是只有一次合成 benchmark，不能证明真实
Agent 世界模型收益，因此治理决策为 `inconclusive`，不晋升。

真实治理对象：

- Experiment：`experiment_1d8b9b782175`
- Candidate：`candidate_world_model_transformer_1d8b9b782175`
- Evolution ledger：14 events，hash chain verified
- production_effective：`false`

## 后果

- 后续正式实现只改 Partner；若需探索新算法，可在临时/测试环境验证后一次性迁入，不维持双主线。
- 下一次 Candidate 实验必须加入真实 Partner Episode/上下文任务以及未见分布，不能重复本合成集合冒充进步。
