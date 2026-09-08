# ADR 0040：用显式承接意图区分独立输入与项目交接

**日期**：2026-08-30  
**状态**：Accepted；`manual_stable` 路径修复，自治运行未开放

## 问题

Agent 元层主动学习把 `outcome.no_business_progress` 拆分后，10 个真实 Episode 聚集在
`governance.unlinked_previous_receipt`。逐条读取任务与计划证据发现：这些 03/05 任务的
`continue_from_project` 均为空，但读取了源码、holdout 或评估文件。旧治理合同仅凭 `inputs` 非空，就断言
任务在承接同角色项目的最新 Receipt，因此把合法的独立新任务硬拒绝。

这里混淆了两种关系：

- **材料关系**：独立任务本轮读取哪些源码、数据和证据；
- **承接关系**：本轮明确延续哪个历史项目，因而必须消费其最新归档产物。

输入集合不能证明承接意图。ADR 0009 用 empty/non-empty inputs 区分两者，只修到了空输入 inbox 情形，
对真实源码审查任务仍会误判。本 ADR 取代 ADR 0009 的该项分类规则。

## 决策

1. `TaskInstance.continue_from_project`（或显式 `previous_receipt_id`）是承接意图的权威来源。
2. `stop_project` 原样传递 continuation、previous receipt、inbox provenance 和 completion inputs。
3. 明确 continuation 且未消费 previous Receipt artifact：继续硬拒绝
   `unlinked_previous_receipt`，写 high Issue。
4. 明确 standalone：即使有独立输入也允许生成下一条 Receipt；若同角色项目已有 Receipt，则写 info
   Issue 说明“new receipt branch”，并显式通知底层 Receipt validator 放行本次非承接关系。
5. 未提供新字段的旧/direct caller 保持保守兼容：仍按非空 inputs 推断 continuation。
6. Receipt iteration 仍单调递增，但序号递增不等于语义上消费了上一轮。

## Event-first Candidate 实验

- 来源诊断：`diagnosis_d852fdcc85ea15cc`，10/10 readable，systematic failure，confidence 0.95。
- 最终 Experiment：`experiment_6b9a3aa5fbc0`（首轮 `experiment_a509e7f75bb1` 保留为修正奖励前证据）。
- Candidate：`candidate_handoff_intent_6b9a3aa5fbc0`。
- 执行 Event：`agent_active_learning_handoff_intent_fresh_canary`。
- 隔离 canary：seed Receipt → standalone + source input → explicit continuation 缺交接 → explicit
  continuation 正确交接。
- 结果：standalone accepted；explicit missing rejected；explicit linked accepted；拒绝尝试不增加 iteration，
  最终 iteration=3；standalone 不获 handoff reward、正确承接获得该 reward；九项 criteria 全过。
- Evolution ledger：153 events，hash chain verified；Policy 为 `inconclusive`，因为本实验验证的是局部
  runtime contract，不是长期自治、业务效果或生产策略晋升。

历史文件名 `candidate_skills.py` / API `register_candidate_skill` 仅为兼容投影名。Candidate 的唯一可执行
合同仍是 `kind=event` + allowlisted handler；本实验没有恢复 Skill-first 侧路。

## 回归与边界

- 定向：31 passed。
- 全仓：544 passed in 125.83s。
- 未启动 01–05、Campaign、cron 或长时运行；production config/control policy 未修改。
- 不能据此声称 RL 已提升业务表现。本轮是“Episode 聚类 → 机制诊断 → bounded repair → fresh canary →
  feedback memory”的一次真实自进化闭环。

## 不回退约束

- 不再用 `bool(inputs)` 作为新 runtime 的承接意图。
- 不允许为了降低拒绝率而取消明确 continuation 的 previous artifact 硬门。
- 不用 successful iteration 数冒充 handoff consumed；承接必须有显式 intent 和 artifact evidence。
- 旧 trajectory 缺少 intent、无法可靠区分时保持 append-only，但 policy read-time 保守扣除其 handoff bonus。
- 新增终态字段必须贯穿 enqueue/stop/governance，不得只存在于 planner 内存。
