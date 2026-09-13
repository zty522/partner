# Event 实现目录

本目录从 Sprint 35 起只接受两类内容：

1. Python 中的 canonical `EventDefinition` 与单一语义 handler；
2. `extensions/*.event.json` 中绑定既有能力的受限扩展清单。

根目录下历史 `*/EVENT.md`（cross-pollination、deep-analysis、exploration、idea-exploration、literature-deep-dive、method-learning、project-health-check、synthesis-review）是早期多阶段 playbook，运行时从未自动加载。它们作为历史设计材料暂时保留，不得注册成一个大 Event，也不得被新代码引用。需要继续使用其中思想时，应把步骤改写为 `partner/event_flows` 的 FlowNode，并把单步能力写入本目录 Python 定义。

约束：

- Event handler 不直接调用另一个 canonical Event；返回终态和下一候选。
- 组合、分支、并行与子 Flow 恢复只写在 `partner/event_flows`。
- LLM 负责理解、提出、反驳、综合；机器证据负责执行验真和生产门。
- 新清单在实例重启时进入新 Catalog；运行中的 Task 固定旧 Catalog version。
- 禁止在这里写 Campaign、周期轮询、前端传输或 Watchdog 业务逻辑。
