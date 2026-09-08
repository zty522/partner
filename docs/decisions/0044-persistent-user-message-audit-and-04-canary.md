# ADR 0044：用户消息持久审计、限域去重与 04 手动 canary

- 日期：2026-08-30
- 状态：promoted（仅消息投递与纯文本手动任务路径）
- 实例：04 实机验证；实现适用于全部实例
- 前置决策：ADR 0043

## 问题

04 在持久任务已经失败、`active_plan=waiting` 且无 queued/running Task 时，仍周期性向 QQ 发送旧版
“后台执行超过单步时间限制”长消息。用户可见消息没有进入 `qq_chat_history.jsonl`、
`delivery_queue.jsonl` 或 Event pipeline，无法从标准记录证明发送者。运行证据显示 04 仍是修改前启动的
长驻进程，并约每 30 分钟恢复 QQ 会话；最可信根因是旧进程内遗留的非标准超时通知路径，而非 Campaign、
Hermes cron 或持久任务队列重跑。

## 决策与实现

1. 停止旧 04 进程，清空的只是进程内遗留事件；历史任务、Issue、Episode、聊天和失败记录全部保留。
2. `push_text_now` 成为运行时文本发送的审计入口：发送前统一净化，发送后只有真实 channel ACK 才计成功。
   executor 的报告、直接回复、错误提示和 self-review 文本不再直接调用裸 `_push_callback`。
3. 新增 `partner/core/user_message_audit.py`：
   - `state/user_message_delivery.jsonl` append-only 记录 attempting/sent/failed/unavailable/deduplicated；
   - `state/user_message_dedup.json` 只保存获得 ACK 的指纹；失败尝试可以重试；
   - 普通任务按 `parent Event + source + semantic text` 限域，不能把两个独立任务的相同步骤消息互相吞掉；
   - 超时/阻塞洪水使用 6 小时内容级指纹，跨进程和 QQ reconnect 仍能抑制。
4. 删除重复的超时净化分支，旧长文统一压缩为一行；`project:backend_timeout_notice` 仍是默认不可见噪声。
5. 修复 canary 暴露的两个相邻缺口：
   - 显式“不生成文件/只回复文本”覆盖输入路径中的 `.md` 关键词，不再自动注入 `report.md`；
   - 纯文本读取任务从真实 Event result 提取所请求的第一行，不把输入 README 误称为新生成文件。

## 实机 matched canary

固定任务均为：真实读取 `docs/README.md` 第一行，只回复结果，不生成或修改文件，完成后停止。

| 轮次 | 结果 | 暴露/验证 |
|---|---|---|
| 1 | rejected | `.md` 输入被误判为输出，注入占位 `report.md` 并原样重试 |
| 2 | rejected | 执行成功但总结只报步骤/README 候选，没有发送真实第一行 |
| 3 | partial | 已发送 `# Partner 文档体系`，但普通步骤消息被跨任务内容去重误抑制 |
| 4 | passed | 收到、计划、步骤开始、步骤完成、实际第一行、最终停止全部获 QQ ACK 并写入本地审计 |

第四轮 Task `73400cbb-f030-4f16-9db1-1934e298a832`，Receipt
`receipt_e21b086e629e`。运行终态为 `manual_stable` 等待，没有自动下一轮。最终全仓回归：
`609 passed in 115.23s`。

## 边界

- 本 ADR 解决的是可见文本发送、重连去重、审计和纯文本 canary，不代表 ADR 0043 的 claim-level
  语义证据、Episode/Reward、主动学习或 RL 已完成。
- 无日志的历史重复消息无法反推到唯一函数；结论保留为证据支持度最高的归因，不伪造成确定事实。
- 文件发送仍使用独立文件 ACK/内容签名路径；后续若统一文件审计，需要单独 Candidate 与 canary。
