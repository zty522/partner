# 持续运行 Campaign

## 目标

Campaign 是 Task 和 Project 之上的持久控制层，使 Partner 能在外部 Agent 会话结束后继续运行数小时或一天。
它不是无限 `while True`，而是由一系列可恢复、有预算、有证据边界的 WorkItem 组成。

## 状态层次

```text
CampaignState
  ├─ WorkItem(project_iteration)
  │    └─ Task → IterationReceipt → NextAction
  ├─ WorkItem(evolution_experiment)
  │    └─ Issue → Experiment → PromotionDecision → 原项目
  ├─ WorkItem(audit)
  └─ WorkItem(report)
```

- Campaign 决定何时、由哪个实例运行。
- WorkItem 是一次有边界的工作，状态为 proposed/leased/queued/running/completed/failed/blocked/cancelled。
- Task 是实例内实际执行单元。
- Project Receipt 负责项目知识承接；Campaign 记录不替代 Receipt。
- 自进化账本独立，验证结束后返回原 Project。

## 持久化与恢复

状态位于 `partner_workspace/state/campaigns/{campaign_id}/`：

- `campaign_state.json`：目标、期限、预算、使用量和活动实例。
- `work_items/*.json`：每项工作的状态、task ID、证据和产物。
- `leases/*.json`：实例租约和超时。
- `events.jsonl`：只追加的状态变化证据。
- `reports/`：阶段/最终报告。

Controller 重启后读取上述文件。若 WorkItem 已产生任务目录，则从 `task_instance.json` 与
`task_log.jsonl` 恢复 running/completed 状态，不重新注入同一任务。
同一工作区只允许一个 unfinished active Campaign；新 Campaign 不会静默覆盖仍在运行或暂停的旧 Campaign。

## 单轮所有权

Campaign 消息带 `[PARTNER_CAMPAIGN campaign_id=... work_item_id=...]`。Research Loop 识别该标记后不启动
进程内连续轮次；本轮完成证据交给 Campaign Controller，由它决定下一轮。这样不会产生两个控制器互相重复入队。

## 完成语义

- dispatch 必须返回真实 message/task ID。
- 同一 WorkItem 的每次 attempt 使用不同 message/task ID，防止实例去重器吞掉重试。
- 要求产物时，产物必须实际存在。
- 用户明确指定文件名时必须精确存在；`*.md` 等宽泛 glob 不能拿其他文件替代。
- 要求交付时，必须在该任务 step result 中找到 `delivered=true` 或 `delivery_confirmed=true`。
- 文件送达只证明交付通道成功，不能覆盖内容验收；项目 WorkItem 必须有最终 `iteration_llm_check.satisfied=true`。
- 项目轮完成后写 IterationReceipt；下一轮 action 先保持 proposed。
- 三轮事件与产物内容签名完全相同会触发熔断和 Issue。

`completion_status=done` 只是单次执行边界，不是最终完成。Controller 只在其后出现最终 LLM 验收通过时恢复为 completed。
取消 Campaign 时，所有未终态 WorkItem 会转为 cancelled，活动 Lease 会 released，并恢复启动前的双槽组合。

## 边界

Campaign 当前通过 desktop inbox 驱动既有实例，而不是替代实例执行器。模型成本使用归一化 `cost_units`
（当前每个已记录 LLM call 计 1 单位），不是供应商账单金额。真实费用控制仍应在 API 适配层补充精确 token/价格统计。

## 2026-08-23 短程实机结论

短程 canary 真实验证了浏览器置前、登录态发布页、逐步截图、视觉模型描述、QQ 文件/文本回执以及验收失败后进入后续 Harness 迭代。试运行也暴露并修复了：中间完成边界误判、最终报告抢跑、Campaign 目标未进入 WorkItem、送达覆盖内容验收、宽泛 glob 替代明确文件、错误 PDF→Markdown fallback、重试消息 ID 复用和 Campaign 标题误去重。

本次整轮不记为通过：最后一轮遇到 QQ 文件 API 间歇性不可连接，按真实交付硬门取消。它是外部通道阻塞证据，不应降级成“本地文件已生成即成功”。

## 2026-08-23 30 分钟 canary 增量

- 业务 blocked 与执行失败分离：有真实产物、交付、验收、阻塞原因和 resume event 时，WorkItem
  可进入终态 blocked 并写 Receipt；重复终态回调必须幂等。
- blocked 只暂停业务推进，不暂停阶段报告；到报告间隔仍创建 report WorkItem。
- Campaign 控制消息走确定性路由；报告按报告文件路径识别并直接真实发送，不进入通用 LLM planner。
- Campaign 内不发送通用“收到指令/正在思考”和 STOP_PROJECT 阶段噪声，也不启动旧 Research Loop。
- 模型调用在 planner 返回后立即写 checkpoint；执行器按 planner 调用、逐步调用和最终累计值去重核算。
  视觉事务显式保留所有 visual_steps 并记录真实模型调用数。
- 外部数据扫描必须有深度、排除目录、候选数量和总文件数量上限；到达上限要在报告中声明 truncated。
- Lease 不得无限越过 Campaign deadline，只允许短暂收尾宽限。
