# Adaptive Project Cycle v2：项目迭代、主动学习与 Partner 自进化

- 状态：implemented，单任务端到端验收通过；跨任务纵向效果待验收
- 更新：2026-09-24

## 责任边界

`project_cycle@2.0.0` 是一个有界父 Flow，最多执行三轮。项目轮优化用户项目；主动学习只解决可由外部来源补齐的知识缺口；自进化只修复 Partner 自身可复现的机制缺陷。科学指标未改善、数据质量问题和负实验不能进入自进化。

每个项目轮运行 `project_cycle_round@2.0.0`：`recall → inspect → plan → core_state → core_forecast/core_jev → core_commit → execute → verify → reflect → core_settlement`。父 Flow 在每轮之前用 `cycle.round_design` 冻结本轮 Event 蓝图，在子 Flow 完成后用 `cycle.round_settle` 选择 `complete / continue_project / active_learning / stop`。Flow 的 `when_output` 只消费结算后的布尔值，LLM 不能直接跳过预算和依赖图。

## 主动学习闭环

只有 Settlement 同时给出明确 epistemic gap 和“外部证据可以解决”时，`cycle.learning_request` 才启动 `active_learning@2.1.0`。该子 Flow 必须下载真实来源、进行来源绑定阅读和交叉核验，并冻结包含项目 Event、假设与证据引用的 adoption candidate。

阅读和生成 candidate 都不算改善。`cycle.learning_impact_settle` 只记录 `candidate_frozen`。下一项目轮的设计必须引用 handoff；轮后 `cycle.learning_downstream_settle` 只有同时看到明确引用、相同评价口径的 baseline/candidate 和机器布尔 `improved` 时才记为 `improved`。缺任一项分别记录 `not_consumed`、`candidate_consumed` 或 `inconclusive`。

## 运行后自进化

项目轮结束后，父 Flow 依次完成项目评估、普通消息及 ACK、PDF 及 ACK、lesson/growth/habit 候选更新和证据封存。随后固定运行 `cycle.partner_audit`，覆盖意图理解、规划、Event Flow、执行、迭代、主动学习消费、项目推进、消息、PDF、交付和记忆。

审计问题只有满足以下全部条件才由 `cycle.evolution_gate` 放行：分类为 `partner_mechanism`；有本轮证据引用；可复现；有 reproducer；有独立评价器；目标在 Partner 源码边界。获准后才启动 `autonomous_evolution@2.0.0` 做冻结测试、隔离 baseline/candidate、比较、晋升或回退。无合格缺陷时产生真实 `no_change` 终态，不为完成流程而制造修改。项目模型、项目数据和 RMSE 等领域指标是硬禁止目标。

自进化终态消息由 `final_summary → final_notify → final_compose → final_critic → final_deduplicate → final_send → final_ack` 表达。Event 不直接写消息队列。此前逐节点直接调用消息 handler 的 side-band 路径已从 Worker 调度删除，避免绕开 Flow、Ledger 和租约。

## Jev 与世界模型

Qwen 仍负责候选、复杂解释和有限 Flow 蓝图。潜空间动力学模型与 Jev 是 shadow 信号；Commitment、独立评价器和 Settlement 保持执行真值。当前 Jev 通过 OpenRouter `POST /api/alpha/decisions` 调用 `typesafe/jev-1.13`。Jev、世界模型和后续专用模型统一登记在工作区 `config/model_services.json`；主对话 LLM 仍由 `config/api.json` 管理。Jev 不授予执行、发布或代码晋升权限；不可用时记录 `unavailable` 并继续。配置合同见 `docs/architecture/model_services_config.md`。

## 验收边界

确定性回归可证明图结构、条件分支、学习下游门、自进化范围门、父子恢复和 Jev 类型协议。一次真实 LLM 闭环只能证明当前配置在该任务上可运行。2026-09-24 的运行属于单任务、两轮项目 matched canary：它有固定数据、冻结比较和机器评价，但没有通过 benchmark wrapper 运行多任务、多 seed、多 arm 聚合。因此它不是完整的 Partner benchmark。主动学习还缺采用前后的 downstream matched comparison，自进化还缺有真实缺陷时的 baseline/candidate replay。只有跨任务、跨 seed 的 matched benchmark 才能证明稳定效果。

2026-09-24 的隔离验收补充了三项运行约束：显式“第二轮/后一轮”协议会冻结最少轮数，首轮新鲜协议不会把其他 Job 的历史产物作为执行输入，模型终端文字超时时只有成功命令回执与可解析数据产物同时存在才恢复为待独立验证的执行终态。完整证据见 `docs/testing/adaptive_cycle_v2_acceptance_20260924.md`。

同日新增多运行 wrapper：`PartnerBenchmarkSuite` 冻结 task×seed 矩阵，每个单元仍走完整 benchmark parent/child Flow，最后才聚合跨运行 effect 和 bootstrap CI。主动学习 API adoption 与自进化隔离 repair 也各有独立协议，继续复用六检查点、Jev shadow 与确定性 Settlement。`benchmark/studies/event_runtime/run_core_v1_closure.py` 是三链重跑入口；实跑证据见 `benchmark/studies/reference_docs/results/core_v1_three_chain_benchmark_20260924.md`。
