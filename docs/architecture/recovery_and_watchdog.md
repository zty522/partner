# Campaign 恢复与 Watchdog

## 租约

Controller dispatch 前先写 InstanceLease，再向 inbox 写带唯一 ID 的任务。dispatch 成功才把 WorkItem 标为 queued。
租约默认 30 分钟；超时后：

- 未耗尽 max_attempts：回到 proposed，下一 tick 使用新 task ID 重试。
- 已耗尽：标记 blocked，记录原因和 Issue。

## 重启恢复

每个 tick 会查找带 Campaign marker 的最新任务目录：

- 找到任务但未完成：queued → running。
- `completion_status=done` 只是单次计划执行边界，不能视为最终完成；只有后续
  `iteration_llm_check.satisfied=true`，或实时 stop 回调到达，才能从 task log 重建完成证据。
- 找不到任务且租约超时：按重试预算处理。

Controller 本身使用非阻塞 runner lock，避免两个后台 runner 同时运行同一 Campaign。

## 无进展检测

完成轮次的签名由事件序列和产物内容哈希组成。同一项目/实例连续三轮签名相同，第三轮不算成功，转为失败/阻塞并记录 Issue。
仅文件名不同但内容相同不能绕过该门。

## 人工恢复

- `paused` Campaign 只有显式 resume 后继续。
- 全部工作 blocked 时 Campaign 低频保留状态，等待新的 resume_event、数据或人工 WorkItem。
- deadline 或预算到达时先调度最终日报；日报真实交付或重试耗尽后才完成 Campaign。
