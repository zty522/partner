# Partner 自进化循环

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
