# ADR 0037：双层主动学习与首个真实 Agent 诊断闭环

**日期**：2026-08-30  
**状态**：Accepted；shadow/inconclusive，production 不变

## 决策

1. 将 `bdk_transformer` 的“选择下一观察”与 `hermes_external_learning` 的“选择下一 Agent 实验”统一为
   value-of-information acquisition，而不是继续把 BDK 限定为函数 selector。
2. 对象层采用信息增益×覆盖度选择下一观察；函数/RMSE 只作透明 evaluator。
3. Agent 层从真实 Episode 形成 failure hypotheses，比较 diagnostic、bounded repair、matched resample；
   当前只允许诊断和 taxonomy refinement 通过 Candidate allowlist 执行。
4. 失败 ontology 可被证据修订，但历史 Episode 不可改写。全局排程和当前诊断链分别使用默认选择与
   `focus_failure_class`。
5. 诊断反馈进入 Beta action memory；没有匹配业务 outcome 前，不形成生产晋升或 RL 增益声明。

## 实验事实

- 真实 `lifecycle.unclosed_tool`：17/17 Episode 有可读原始 task log 或归档 trace；发现 6 类未闭合步骤，
  top concentration `0.2571`，因此旧粗标签被判定为异质而不是单一系统故障。
- `experiment_c4a6c70690fa`：taxonomy、Episode 不变性、账本和 production 条件通过，但下一全局查询没有
  回到当前父类，`next_query_targets_child=false`。该失败被保留。
- 新增显式局部 focus 后，`experiment_3f0fd4cc62bd` 五项条件全过，下一目标为
  `lifecycle.unclosed_tool/generate_text`；Candidate `candidate_failure_taxonomy_3f0fd4cc62bd`。
- `experiment_e8f5c512f4b0` 对该子类读取 9/9 Episode，得到 `systematic_failure`、confidence `0.95`，并以
  diagnosis artifact 为证据记录 action feedback；Candidate `candidate_agent_diagnosis_e8f5c512f4b0`。
- 最终 ledger 72 events、head `8fc7ee087d7cf96de43eddce91ead682c2a45ecb78fd10449485bb4a1fb838ea`，验链通过。
- Partner 完整回归：`532 passed in 152.18s`。

## 尚未证明

- 没有自动修复 generate_text 未闭合机制；
- 没有 matched repair/resample 业务对照；
- 没有证明长期 RL 或自主循环已经有效；
- 没有 production/canary 变更。

下一门是实现 subtype-scoped、可回滚的 repair provider，并用独立 Episode 的 task outcome 而不是诊断文案
评价；只有多次可比较反馈足够后，才训练 contextual bandit/RL action policy。
