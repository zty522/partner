# 实例原生长期运行时

updated_at: 2026-09-08  
authority: canonical

## 定位

长期项目推进属于实例本身，不属于 Campaign。每个实例维护自己的项目状态、Receipt、失败 Episode、学习中断和
恢复点；全局运行时只按本机实时资源仲裁 1–5 个计算槽。普通用户消息继续走 `manual_stable`，完成后等待下一条消息。

## 事件状态机

```text
PROJECT_DISPATCHED
  ├─ accepted terminal → next project action / YIELD_SLOT
  └─ failed | contradiction | uncertainty | knowledge gap
       → reduce real Task to Episode
       → EXTERNAL_LEARNING or SELF_EVOLUTION_DISPATCHED
       → 外部缺口: select → acquire → read → adoption proposal
       → 内部缺陷: observe → diagnose → bounded repair Candidate
       ├─ accepted terminal → reset failure streak → resume suspended project
       └─ failed terminal → BLOCKED (no silent project bypass)
```

没有“每 5 分钟学习一次”或“每小时发一次总结”。`TaskTerminalReceiver` 是主要推进信号；watchdog 只处理进程重启、
服务/槽账本漂移和漏掉的终态，不制造工作或用户消息。

## 不变量

1. Event-first：外部主动学习、自进化和项目假设提案均使用注册 Event，不以 Skill 或报告文本替代执行。
2. 学习前必须存在真实 Episode；缺少 Task/日志证据时 fail closed。
3. 同一实例严格串行；全局槽数由 CPU/load/可用内存实时预算决定，配置值只是上限。
4. 只消费 `manual_stop_project_finalization` 权威终态，避免 Harness 早期终态造成重叠任务。
5. 一次失败链最多插入一次受限学习；学习后业务重试仍失败就持久化证据并 `YIELD_SLOT`，不得无限学习占槽。
6. 重启时无法恢复的内存执行必须诚实终结为 `runtime_restart_orphaned`，不得永久 pending 或冒充成功。
7. 外部主动学习成功只表示获得了新外部证据；自进化实验成功只表示 Candidate 过门；二者均不等于业务进步，随后必须回原项目验证。
8. 外部发布和账户登录继续遵守审批边界。生产源码自动修改仅允许白名单代码面，并须同时满足真实 Episode、
   行为型 diff、隔离 baseline-fail/candidate-pass、聚焦回归和原子回滚；其他修改继续要求人工批准。
9. 项目停滞触发的是 EGPL 项目假设 Event：LLM 只能在 Event 测量契约内提议；它不是传统 RL，也不能自行晋升生产代码。

## 资源轮转

生产启用 01–05，`instance_native_max_active=5` 是安全上限而非固定并发数。调度器保留 2 个逻辑 CPU 和
2 GiB 内存给宿主，每 lane 预算 2 CPU + 1.5 GiB `MemAvailable`，每次 watchdog sweep 重算 1–5 槽。
资源收缩时不终止正在执行的实例，而是自然排空后再按新容量补位。实例完成配置数量的项目动作后产生 `YIELD_SLOT`，调度器按稳定
顺序换入下一实例；这是完成信号驱动的公平轮转，不是时间周期。每次收敛都会幂等执行 `systemctl start` 确保
“账本已选中但服务已死亡”的重启漂移被修复。
控制器自身重启必须保留所有尚未 `YIELD_SLOT` 的持久槽，不能重新选择配置列表头部并打断在途任务。

## 证据与验收

- 状态：`state/instance_native/instance_XX.json`
- 转移账本：`state/instance_native/events.jsonl`
- 权威终态：`state/campaigns/task_terminal_events.jsonl`（历史文件名保留，不代表任务属于 Campaign）
- Episode：`share/mind/governance/episodes/episode_*/`
- 项目：`share/projects/<project_id>/governance/`
- 生产服务：`partner-instance-native.service`

通过标准是业务/学习步骤真实执行、消息或本地镜像可观察、Receipt/Episode/Reward 归因一致、失败能触发一次学习并
回到项目、跨重启不丢任务。进程存活和循环日志本身不算通过。
