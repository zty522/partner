# ADR 0057：Sprint 18 持久控制器与 Monitor 投影隔离

**日期**：2026-09-01  
**状态**：Accepted；服务已运行，production policy 未改变

## 背景

用户要求让 Sprint 18 持续运行。通用 `partner-campaign@` 服务只能消费普通队列，不执行 Sprint 18 的主动课程
续项逻辑；直接前台运行又不能跨 Agent 会话保持。同时，持久启动后的首个定时报告暴露：渠道/最终治理双终态
会把 control-plane report 误计为业务失败，并把显式 learning-ineligible 的 monitor 投影成 RL 样本。

## 决策

1. 新增 `partner-sprint18@.service`，固定运行 `run_sprint18_active_campaign.py`。终态信号即时续项，30 秒仅为
   watchdog；服务崩溃可恢复，Campaign 自身 deadline、模型、成本、失败和 WorkItem 预算仍是硬边界。
2. Campaign 为 `WAITING_EVIDENCE` 时服务保持在线，但不占实例槽、不调用模型、不重复旧题。持久控制器在线与
   Campaign 有无当前可运行 WorkItem 是两个状态，不再混写。
3. `campaign_report_delivery` 在 manual governance 永远形成 completed、reward=0、policy/learning-ineligible
   monitor。渠道 ACK 由 Campaign report ledger 单独验收。
4. report delivery problem 可保持 blocked 并计入 `report_issues`，但不消耗业务 failure/retry budget，也不创建
   业务 Issue。历史误投影使用 WorkItem 与 trajectory correction，原 Task/渠道事实不删除。
5. Observation 投影必须尊重 trajectory 显式的 `learning_observation_eligible=false`。当最新 trajectory revision
   改变资格时，追加同 observation identity 的新 revision；中性 monitor 不计 positive/negative。

## 真实结果

- 服务：`partner-sprint18@campaign_74833fad4af8.service` active，启动追踪 `NRestarts=0`。
- Campaign：`30` WorkItem，`23 completed / 7 blocked / 0 active`；`9` 次模型调用，状态
  `WAITING_EVIDENCE`。
- Observation：`678 total / 539 learning / 94 promotion / 435 positive / 234 negative / 9 neutral`。
- 全仓回归：`720 passed, 2 warnings in 151.96s`。
- `control_policy.json` 未包含本 Sprint 新 Candidate，生产仍为 `manual_stable`。

## 边界

服务会持续到 Campaign deadline 或预算硬门；这不是无预算永久消耗。当前在线等待不等于长期 RL 门已通过。
三真实日期、20/arm、Wilson、anchor、false-success=0 和 canary/rollback 仍须后续真实证据。
