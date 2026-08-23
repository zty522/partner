# 项目迭代循环

## 目标

让每个实例能读取上一轮的真实结果，创建并执行下一轮，同时在证据、权限或资源边界到达时正确停止。

## 核心状态

`ProjectState` 记录项目目标、owner、当前轮次、最新 Receipt、暂停/阻塞原因和恢复事件。

`IterationReceipt` 记录本轮：

- 真实 inputs。
- 已执行 actions。
- 产物与发现。
- 未解问题。
- 交付是否确认。
- 显式 `NextAction`。
- 无下一步时的 stop reason。

## 续跑条件

仅当以下全部成立时自动续跑：

1. 项目未 completed/cancelled/blocked。
2. 存在 proposed 的 NextAction。
3. 动作具有可执行 event_type。
4. 动作会产生新证据，而不是简单重复。
5. 未超过成本/轮数/重复安全门。
6. 不需要未获得的用户授权。

enqueue 成功后把动作标记为 queued 并保存 task_id。事件开始/结束后分别转为 running/completed。

## 停止与恢复

- 缺数据/授权/外部状态：blocked，写清 resume_event。
- 阶段目标完成：completed。
- 实例被调度换出：paused，保存当前状态，不改写项目结论。
- 恢复时先读 ProjectState + 最新 Receipt，再执行 resume_event/首个 queued action。
