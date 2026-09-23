# ADR 0107：Core v1 单一主分支决策脊柱

- 状态：accepted
- 日期：2026-09-23

## 决策

Partner 以现有 Event Runtime 为唯一生产运行时，在 `main` 上加入 Core v1，不新建永久 core 分支或平行系统。三条真实链统一采用：领域 LLM 候选 → 潜空间影子预测 → Jev 类型化影子判断 → Commitment 冻结 → Event 真实执行 → 独立评价 → Settlement → 确定性触发路由。

Jev 和潜模型在 v1 中不拥有科学真值、执行权限或晋升权。它们不可用时记录原因并继续真实执行。项目迭代、主动学习和自进化的触发由 `TriggerEvidence` 硬条件决定；模型只提供可审计建议。

历史 Flow 按版本保留。已完成的 `feature/commitment-kernel` 快进合并进 `main` 后删除；`cleanup-v1` 没有独有提交并删除。未跟踪 canary 产物在移除 worktree 前归档并生成 SHA-256 清单。

## 原因

原系统已有成熟 Event、Commitment 内核和三条领域 Flow，再建立一套 runtime 会重复队列、状态和恢复语义。统一脊柱让“预期—行动—结果”的差异成为同一种研究对象，也让主动学习和自进化成为有证据条件的分支，而不是 LLM 的叙事选择。

## 后果

最新 Flow 版本变为 `project_iteration@3.1.0`、`active_learning@2.0.0`、`self_evolution@2.0.0`。旧任务仍解析旧版本。Core 状态新增到 `state/core_v1`。Jev 真实调用需要环境变量 `TYPESAFE_API_KEY`；没有密钥时状态是 `unavailable`，不构成运行失败。
