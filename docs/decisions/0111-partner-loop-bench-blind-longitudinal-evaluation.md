# ADR 0111：用盲化纵向 Event/Flow benchmark 评价 Partner 自主闭环

- 状态：accepted
- 日期：2026-09-24

## 决策

Core v1 工程验收冻结为基线。后续能力声明以 Partner-LoopBench 为准：被测 subject 与隐藏评价器分离；所有执行均由 Event/Flow 表达；比较完整 Partner 与预声明消融；零 uplift 不能算支持；每轮保留 checkpoint、成本和失败证据。

项目迭代、主动学习和自进化使用各自任务账，不互相冒充改善。纵向实验只允许消费前轮已验证记忆，并必须与不消费记忆的 matched arm 比较。Jev、LLM judge 与未来世界模型在完成校准前保持非权威。

## 原因

既有 benchmark 由开发者预声明正确特征或修复方向，主要验证执行与 Settlement。首个盲化 pilot 又显示简单任务上 single-turn 与 full Partner 同为 100%，完整链路没有可测增益。需要把研究重点从“能否跑完”转向“未知任务上是否产生可归因、可重复、随经验增长的效果”。

## 后果

后续不得用节点数、文档数、反思文本或单个成功案例声称自进化。论文级结论至少需要隐藏任务、独立答案维护、多 seed、纵向曲线、完整消融、成本/错误率和外部复现。
