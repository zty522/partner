# 成本、资源与安全预算

## 强制预算

CampaignState 保存：

- 最大 WorkItem 数。
- 最大失败数。
- 每项最大重试次数。
- 最长运行时间。
- 最大模型调用数。
- 最大归一化成本单位。

任一硬预算到达后不再启动项目/进化 WorkItem，只允许最终日报进入发送流程。

## 安全门

WorkItem autonomy：

- `safe`：允许自动执行。
- `human_required`：登录、真实发布、付款、购买、密码/凭证和不可恢复生产操作；保持 proposed 等待用户。
- `forbidden`：Campaign 永不执行。

默认文本检查会把明显的真实发布、支付、购买、输入密码等动作转为 `human_required`。这只是兜底，不能替代事件自身权限检查。

## 成本策略

- 上下文优先使用确定性 catalog 选择。
- Campaign tick、调度、恢复和报告汇总本身不调用 LLM。
- 一个 WorkItem 是一个有边界模型任务；不在实例内再开无界 Research Loop。
- 阶段消息按 report_interval 汇总，避免每个 tick 发消息。
- 当前 cost_units 是可审计的抽象额度；接入精确 API 账单前不得称为真实货币成本。
