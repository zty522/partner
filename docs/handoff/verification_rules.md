# 验收与真实性规则

## 当前模式门

- 当前生产模式必须是 `manual_stable`；缺配置时也按该模式关闭自治能力。
- 普通任务只能由用户消息触发，并且只运行一次规划；结束后等待新消息。
- 必须实证“收到指令 → 每步开始/完成 → 最终结果”，只发 PDF、只写队列或只写日志均失败。
- `create_campaign`、`enqueue_campaign_work`、`strict_reflect`、`next_iteration`、Research Loop、
  自动 self-heal、CRON/WAKE_UP 在该模式必须拒绝或空操作。
- 同时最多两个实例；切换槽位后验证 systemd、heartbeat 和消息回执，不以 service active 单独判定可用。

## 项目迭代

- 每轮必须有结构化 `IterationReceipt`。
- 上一轮产物必须出现在下一轮 `inputs`。
- `next_actions` 必须处于 proposed/queued/running/completed/blocked/cancelled 之一。
- 只有 queued 后真实产生任务 ID 才能说“已入队”。
- 只有事件证据、产物和验收都完成才能说“已完成”。
- Campaign 直接事件必须有 `started/executed/finished` 三阶段真实消息回执；只在最后发送 PDF 不合格。
- Scout 可使用简短模式，但必须说明检查对象、no-change 是否发生以及为什么不算业务进步。

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
- 报告：机器 JSON 与用户正文分离；不同项目使用领域化章节，不得用公共模板和大段 JSON 充当分析。

## 长期 Campaign（实验、当前暂停）

- service active、heartbeat 或 Bot ready 只能证明进程/连接，不能证明 WorkItem 在执行。
- 每项工作必须有 Campaign marker、WorkItem 状态、真实 task ID 和完成证据。
- 同一实例只能有一个 leased/queued/running Campaign WorkItem，全部实例最多两个活动槽。
- Controller 重启后必须从持久任务日志恢复，不能重复注入。
- 到期/预算耗尽后不得再启动项目工作，只允许最终日报；日报需要真实交付回执。
