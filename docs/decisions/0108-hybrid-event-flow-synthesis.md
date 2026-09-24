# ADR 0108：LLM 候选生成与学习策略混合的 Event Flow 合成

- 状态：accepted
- 日期：2026-09-24
- 适用分支：`main`

## 背景

Partner 需要生成项目计划、迭代计划、主动学习和自进化 Flow。Event Catalog 和运行约束仍在演化，现有真实轨迹尚不足以支持端到端神经网络可靠生成完整图，但开放项目又需要语义分解和新策略创造。

## 决策

当前由 LLM 生成 2–4 个结构化候选 Flow。候选依次经过确定性 Flow Compiler、Jev 类型化筛选、潜空间后果预测和 Commitment 冻结，再由 Event Runtime 执行，最终由独立评价器和 Settlement 裁决。

失败后的迭代默认是最小子图替换。只有可复现 Partner 机制缺陷进入自进化；只有可由外部证据解决的知识缺口进入主动学习。两者都是有界 child Flow，完成后返回父项目。

完整运行轨迹将形成未来学习数据。神经网络先作为 FlowRanker、NextEventPolicy 和 OutcomeModel 进入 shadow matched evaluation；只有跨任务验收通过后才逐步获得局部决策权。端到端完整 Flow 生成属于后续研究阶段，且始终受 Compiler、Commitment 和 Settlement 约束。

## 后果

- LLM 保留开放任务上的组合和创造能力；
- Jev 与潜模型提供低成本筛选和预测，不声明实验真值；
- 非法 Flow 在执行前被确定性拒绝；
- 迭代证据可直接沉淀为未来模型训练样本；
- 当前不会用合成小样本训练出的策略替代尚未稳定的运行主干。

详细合同见 `docs/architecture/flow_synthesis_policy.md`。
