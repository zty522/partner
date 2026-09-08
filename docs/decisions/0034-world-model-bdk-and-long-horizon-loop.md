# ADR 0034：BDK 回归世界模型原意，长期循环采用证据相位控制

**日期**：2026-08-30  
**状态**：Accepted（研究与实验控制）；production `manual_stable` 不变

## 决策

1. BDK FunctionPool 保留为历史数值 provider，但不再代表 BDK 北极星。
2. BDK 研究主线定义为“观察—联想—多假设—拟合/模拟—主动求证—记忆巩固”的世界模型认知底层。
3. Transformer 将来只作为检索/假设 provider；Event、真值评价、安全门和晋升在模型外。
4. Partner 长期 Campaign 增加持久相位 reducer：`ADVANCE_PROJECT`、`CONSOLIDATE_EVIDENCE`、
   `VALIDATE_CANDIDATE`、`WAIT_EVIDENCE`、`WAIT_WORK`。
5. Candidate 自动实验必须同时满足：同一 Campaign 来源、execution/evaluation 两个 Event 合同均 ready、
   非 production、业务续步已清空、治理密度未超限。每个 Candidate 每 Campaign 最多自动安排一次，不自动晋升。

## 原因

旧路径把 BDK 缩窄成固定函数选择，又把知识笔记称为 Skill，偏离了用户关于世界模型、经验联想和记忆巩固
的原意。另一方面，无限循环若没有证据相位，会退化成报告、反思或 Scout 自我复制，无法产生可学习轨迹。

## 证据

- BDK world-model 核心 6 个测试通过（含事件篡改阻断）；点集实验从 5 点 `cubic` 修正到 17 点
  `fourier_f1`，重复经验增强记忆。
- Partner long-horizon/campaign 定向测试 55 个通过，含端到端“业务→05 学习→一个 Event-first Candidate
  Experiment→禁止重复”测试。
- 当前没有启动真实长跑、没有 production policy 变化、没有自动 promotion。

## 后果

- 后续不得用 TargetDiff RMSE 单独回答“BDK 世界模型是否有用”。它只评价旧 FunctionPool 器官。
- 没有新业务证据时 WAIT 是正确状态；长期运行指可恢复地等待和推进，不是持续消耗模型调用。
- 下一阶段先做 shadow Episode 映射和跨领域小任务，不直接训练大型 Transformer 或修改五实例生产上下文。
