# ADR 0038：dependency-skipped 终态修复与主动学习 selector v2

**日期**：2026-08-30  
**状态**：Accepted as repository observability fix；Candidate shadow 仍 inconclusive，未启动 production/Campaign

## 问题纠正

ADR 0037 将 `lifecycle.unclosed_tool/generate_text` 的 9 个 Episode 诊断为 systematic，但这仍只是现象分类。
进一步读取 trace 后发现：多数 `generate_text` 没有真的开始模型调用后挂死，而是上游失败后被执行器正常
skip。旧路径只向 progress callback 发送 `step_complete`，没有向权威 `task_log.jsonl` 写 terminal event；
Episode reducer 因而把已结束的 skipped step 误判为 interrupted。

这说明主动学习找到了真实缺陷，同时也反证了“generate_text 模型本身系统性失败”的过强解释。

## 修复

1. `PlanExecutor._run_step` 在 required dependency 失败的早退分支写入唯一
   `plan_executor_step_completed`，包含：
   `ok=false`、`terminal_status=skipped`、`terminal_reason=required_dependencies_failed`、失败依赖和完整 ordinal。
2. progress callback 与 task log 共用同一 terminal payload，避免两个观测面语义漂移。
3. Episode reducer 将该步骤记作 `status=skipped`；它是终态，不产生 `lifecycle.unclosed_tool`，也不产生
   `tool.<event>.failed`。真正失败仍归因于上游步骤。
4. 没有 dependency-skip 证据的 unmatched step 不修复、不吞掉。

## 正式 shadow

- Experiment：`experiment_2e4ff0e1db7c`
- Candidate：`candidate_skipped_terminal_2e4ff0e1db7c`
- 9 个匹配 Episode，证据覆盖 `1.0`。
- baseline/no-op unclosed：10。
- 有明确 skip 证据、可终态化：9。
- 无相同证据、继续保留为真实/未知中断：1。
- 历史 state/trace byte-identical：true。
- resample：`unknown_without_new_execution`，没有用历史反事实伪造结果。
- 五项 shadow 条件全过，feedback 已写入 `bounded_repair` action memory；Policy 保持 `inconclusive`，因为
  replay 证明的是可观测性归因，不是业务 outcome 改善。

## selector v2

最新 systematic/transient diagnosis 现在会更新 hypothesis prior；已有诊断的新颖性下降；repair/resample
任务价值结合该后验的预期成功率和历史 Beta memory。decision identity 纳入 `selector_version`，算法升级不会
覆盖旧决策文件。

真实 selector v2 决策 `active_query_e02512e8878d6aea`：

- target：`lifecycle.unclosed_tool/generate_text`
- selected：`bounded_repair`
- Event：`agent_active_learning_skipped_terminal_repair_shadow`
- acquisition score：`0.006491777935226712`（正值，不是“三个负分里选最高”）

## 验证与边界

- 新增 fresh bounded harness 测试：上游确定性失败后，下游 handler 不运行、模型调用为 0、task log 恰有一个
  skipped terminal。
- 新增 Episode reducer 回归和历史 repair evaluator 回归。
- 完整回归：`535 passed in 155.72s`。
- 这不是 generate_text 模型质量提升，也没有修复那 1 个未知中断；没有启动实例、Campaign 或自动循环。
