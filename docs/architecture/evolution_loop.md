# Partner 自进化循环

> **Event-first 约束（2026-08-29）**：本页闭环中的每个状态变化都必须落为 Event；Candidate 不是
> 可直接调用的 Skill。可执行、幂等和晋升合同以
> [`event_first_self_evolution.md`](event_first_self_evolution.md) 与 ADR 0032 为准。

## 定义

自进化是 Partner 的可复用做事能力经验证后发生改善，不是新增一份反思文档。

## 闭环

```text
Observe → Issue → Diagnose → Hypothesis → Candidate Intervention
        → Focused Test → Regression/Canary → Promote | Reject | Inconclusive
        → Return to original project
```

## 精准问题发现

信号包括用户反馈、事件失败、回执缺失、计划/执行差异、产物质量门、前后轮指标、重复动作、长时间无进展和资源异常。
问题必须归类到 context/planning/event/environment/verification/delivery/scheduling/data/model/unknown。

## 晋升门

- Issue 有至少一条可复核证据。
- Hypothesis 可被成功标准证伪。
- Candidate 记录干预范围，不扩大授权。
- Focused tests 通过且相关回归没有恶化。
- 需要外部效果时完成 canary/实机验收。
- 指标更好且没有越过边界才 promoted。

## 与项目循环的关系

项目运行可以产生 Issue；自进化验证完成后必须返回原项目重试失败步骤或继续下一步。
只有通过其他任务或回归验证的方法才是 Partner 通用成长；单个项目的一次优化结果仍属于项目记录。

ADR 0048 给出了完整的失败处理示例：第一次五项目 Candidate 对照虽有较大平均改善，但预算与 Receipt
硬门失败，因此记录 inconclusive；修复机制后另开 Experiment，而不是覆盖旧结果。第二次 7/7 通过仍只
证明上下文准备改善；没有下游任务 outcome 时继续 inconclusive。这是“Focused Test 的指标必须与实际
Claim 对齐”的标准案例。

ADR 0049 把 Focused Test 推进到下游 Agent：三类冻结任务由独立消费者作答，再由真值/来源/安全评价器
验收。两次中间失败都生成新 Experiment 并驱动最小修复，最终本地 3/3 仍只计 shadow learning evidence。
外部 LLM 未获项目数据外发授权时必须停止，不能把本地消费者成绩替代为 LLM 或业务 Reward。
