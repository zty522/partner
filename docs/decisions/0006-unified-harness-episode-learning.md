# ADR 0006：四套 Harness 统一为 Episode 证据层，进化先 Shadow

**状态**：accepted  
**日期**：2026-08-26

## 决策

Partner 不迁移到 DeepSeek Harness、Codex、OpenClaw 或 Hermes 的运行时。四者分别提供可独立采用的
不变量：DeepSeek 的 append-only 生命周期事实；Codex 的 observe-first/offline reducer；OpenClaw 的
Gateway/session/transcript 权威与分级记忆；Hermes 的 memory→candidate skill 经验复用。Partner 在其上
保留自己的五实例、手动消息、Receipt、真值门、EvolutionExperiment 和 PromotionDecision。

统一学习单元是 Episode Trace v3：原始 task JSONL 与 step payload 保持权威，离线 reducer 生成
model/tool/artifact/delivery/receipt 关联图。奖励改为 truth、business progress、handoff、observability、
efficiency、safety 六维向量；truth 或 safety 失败不可被其他分量抵消。

自进化候选只能按 `episode→failure cluster→Candidate→historical replay→shadow→matched canary→explicit
promotion` 前进。初期 shadow 只写实验和评价，不改生产 prompt、代码、控制策略或自动续轮。

## 后果

- 生产继续是 `manual_stable`，现有 04 真值策略仍在，但事实矛盾属于缺陷并 fail closed。
- 05 可以离线发现候选，不能自评自升；至少 10 个匹配样本/臂后才讨论 canary。
- raw trace 记录失败不得阻断业务；reducer 输出可重建、可替换，不能覆盖原始日志。
