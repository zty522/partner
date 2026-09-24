# ADR 0109：以 Settlement 驱动项目迭代、主动学习和运行后自进化

- 状态：accepted
- 日期：2026-09-24

## 决策

在唯一 `main` 和 Event Runtime 上引入 `project_cycle@2.0.0`。每轮由 LLM 生成有限 Event 蓝图，确定性图和 Commitment 约束真实执行，由 Settlement 决定停止、继续项目或插入主动学习。主动学习只在后一项目轮给出匹配结果后结算效果。所有项目轮结束并完成消息、PDF、ACK 和记忆后，固定审计 Partner 自身；只有可复现、可独立评价、证据绑定的 `partner_mechanism` 问题才能进入自进化实验。

Jev 通过 OpenRouter 以 shadow 模式参与类型化判断。最终自进化消息必须经过正常消息 Event 链，Worker 不再直接调用 side-band 消息 handler。

## 原因

旧固定两轮把“继续”当日程安排，学习候选容易在阅读完成时被误写为改善，项目负结果也可能被误送入自进化。新的结算点把三个对象和三套证据分开，同时保留一个可恢复、可版本回放的父 Flow。

## 后果

最新相关 Flow 为 `project_cycle@2.0.0`、`project_cycle_round@2.0.0`、`active_learning@2.1.0`、`autonomous_evolution@2.0.0`。历史版本保留。普通任务仍受显式 `evolution_cycle` 授权和最多三轮预算约束；本 ADR 不开启定时 Campaign 或无界自治。
