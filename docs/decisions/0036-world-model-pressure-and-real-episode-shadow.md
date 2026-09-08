# ADR 0036：世界模型压力门、鲁棒证据与真实 Episode 只读影子

**日期**：2026-08-30  
**状态**：Accepted；Candidate 继续 shadow/inconclusive，production 不变

## 问题

ADR 0035 的三个干净合成领域过于容易。补充 120 个多种子样例后，首版 Transformer 有 29/120 次把
真实函数族排除；固定结构参数也不能表达 Fourier 1.5、decay 3.2、hinge 0.37。离群点下 Library 与
Candidate 的平均 holdout RMSE 都约为 0.224。该证据否定了“完成三个演示即可继续晋升”的判断。

## 决策

1. Transformer 只做可撤销的候选检索：稀疏（少于 9 点）或对基础假设 surprise≥0.18 时，退让为全族搜索；
   其他不确定情况用 top-3，只有证据较清晰才用 top-2。
2. 扩展可审计结构网格，但不允许 Transformer 自己评价真值。
3. 少于 8 点必须返回 `insufficient_evidence`，不得用尖锐 posterior 冒充已确认机制。
4. 两臂共享 `robust_bic_mdl_v2`：Huber IRLS 拟合、截断异常残差用于证据排序，同时保留普通 RMSE 诊断。
   这是共同证据层升级，不记作 Transformer Candidate 的独占收益。
5. 真实 Episode 只做只读奖励轨迹 shadow。它不能推出任务真值或因果关系，也不能修改 Episode、prompt、
   control policy 或生产实例。
6. `hard_gate_passed` 的含义固定为 truth+safety；新增 `hard_gate_scope` 和 `outcome_gate_passed`，RL 不得把
   truth+safety gate 误当任务完成。

## 新正式实验

- Experiment：`experiment_d0ccd7752641`
- Candidate：`candidate_world_model_pressure_d0ccd7752641`
- stress dataset SHA-256：`adf6d7cf11414ebfa4758db8113f803c40823e444a60a6c897a968f823991095`
- evaluator：`robust_bic_mdl_v2`
- Evolution ledger：27 events，head
  `fe079ac4dc914bed4f887382992b972f9b3954d94ee8b5ea6fed67148852baf0`

| 压力指标 | Library | Transformer Candidate |
|---|---:|---:|
| mean holdout RMSE | 0.051701 | 0.047880 |
| P90 holdout RMSE | 0.115411 | 0.112031 |
| family accuracy | 0.6833 | 0.6917 |
| expected-family proposal recall | 1.0 | 1.0 |
| sparse insufficient-evidence rate | 1.0 | 1.0 |
| mean hypotheses considered | 28.0 | 20.95 |
| outlier mean RMSE | 0.012678 | 0.012678 |

真实 workspace 共读到 110 个 Episode；只有实例 04 的奖励至少有三种取值，可形成一个描述性 holdout
轨迹。03、05 的历史 reward 全为 0，禁止冒充有效训练数据。真实轨迹两臂 RMSE 相同，Candidate 工作集
28→21。历史中 68 条是“truth+safety gate 通过但任务未完成”，这是字段范围差异，不是 68 次任务成功。

全部压力与 Episode shadow 条件通过，实验层结果为 `candidate_wins`；治理层仍为 `inconclusive`，因为只有
一个真实可评估实例轨迹、奖励高度离散且没有前瞻 canary。`production_effective=false`。

## 后续门

- 收集 03/05 非零且可验证的 Episode reward；先修奖励可辨识性，再谈 RL。
- 增加组合机制、变点、缺失值与真实连续项目指标；不得只重复本压力集。
- 前瞻 Candidate 必须在独立任务上执行，并保持 Event-first、匹配 evaluator 和显式人工晋升。
