# 验收与真实性规则

## 项目迭代

- 每轮必须有结构化 `IterationReceipt`。
- 上一轮产物必须出现在下一轮 `inputs`。
- `next_actions` 必须处于 proposed/queued/running/completed/blocked/cancelled 之一。
- 只有 queued 后真实产生任务 ID 才能说“已入队”。
- 只有事件证据、产物和验收都完成才能说“已完成”。

## 自进化

- 需要 Issue 证据、可证伪假设、实际干预、前后指标和 PromotionDecision。
- 只有 `promoted` 才能进入长期成功经验。
- `rejected` 必须记录回退/不采用；`inconclusive` 不得宣称改进成功。
- 自进化不能终止原项目主线；改进验证后应回到原任务。

## 外部效果

- QQ 发送：必须有运行时回调确认。
- 可见浏览器：必须 `visible=true`、worker 健康且执行置前。
- 网页操作：视觉描述与 DOM/控件证据并行。
- PDF：必须通过内容长度、章节、证据和 Unicode 验收。

## 长期 Campaign

- service active、heartbeat 或 Bot ready 只能证明进程/连接，不能证明 WorkItem 在执行。
- 每项工作必须有 Campaign marker、WorkItem 状态、真实 task ID 和完成证据。
- 同一实例只能有一个 leased/queued/running Campaign WorkItem，全部实例最多两个活动槽。
- Controller 重启后必须从持久任务日志恢复，不能重复注入。
- 到期/预算耗尽后不得再启动项目工作，只允许最终日报；日报需要真实交付回执。
