# Partner-LoopBench：盲化、纵向、可归因的自主闭环评测

## 研究问题

Core v1 的工程验收证明 Event/Flow、双臂执行、独立评价与 Settlement 可以运行，但没有证明 Partner 自己发现问题、选择干预、学习并持续改善。Partner-LoopBench 将被测系统与评价器分开，直接测量完整闭环相对较弱控制组的增益。

当前首个冻结假设为：在相同公开任务、模型和执行预算下，`提出→反驳→选择→Commitment→执行→验证` 的完整 Partner 决策闭环，任务成功率应比单轮 LLM 选择至少提高 `0.01`。零增益不能结算为支持。

## Event 与 Flow 边界

父 `benchmark_experiment` Flow 持有协议、隐藏评价器、两臂收集、guardrail、比较和权威 Settlement。新 `partner_loop_bench_subject@1.0.0` 只持有公开问题：

```text
loop_bench.task_inspect → CP1
  → loop_bench.candidate_propose
  → loop_bench.candidate_critic
  → loop_bench.candidate_select → CP2
  → loop_bench.commitment_freeze → CP3
  → loop_bench.action_execute → CP4
  → loop_bench.outcome_verify → CP5
  → loop_bench.settlement → CP6
```

Event 不调用 Event。父 Runtime 根据已验证协议启动相应 subject Flow，暂停父 Flow，子 Flow 终态后合并检查点并恢复父 Flow。

## 盲化合同

- `tasks/*.json` 仅包含情境、候选、约束，不得含 `answer/correct/oracle/score/label` 等答案字段。
- `sealed_oracles.json` 与评分 runner 不进入 `benchmark_subject_view`，只在 Commitment 之后计算结果。
- `public_subject_view()` 丢弃所有 `hidden_*` evaluator 输入，并只向当前 arm 暴露自己的 policy。
- baseline 与 candidate 在相同任务、模型和动作预算下运行；当前唯一允许差异是 policy。
- 评价反馈在两臂终态前不得返回被测 Flow。子 Flow 只能验证执行完整性，不能自行宣布效果。

## 任务与指标

v1 任务包含 15 项：项目迭代、主动学习、Partner 自进化各 5 项。首个 pilot 每类抽一项。核心指标为 Task Success、Autonomous Uplift、False Evolution Rate、Valid Run Rate；后续加入 Learning-to-Action Gain、恢复率、Event/模型调用成本和纵向斜率。

15 项选择题只用于基础设施校准，不足以支撑论文结论。正式任务必须加入真实产物执行、未知缺陷定位、来源选择与下游采用，并由独立维护者密封答案。

## 纵向与消融路线

正式评测以多 episode 序列运行。每轮 Settlement 后只把已验证经验写入下一轮允许读取的记忆；对照组获得相同任务但不消费该记忆。比较 no-memory、experience-only、growth-only、habits-only 与 full-memory 的学习曲线。

消融最少覆盖 single-turn LLM、no-critic、no-Commitment、no-active-learning、no-post-run-self-audit、static Flow 和 full Partner。Jev 与潜空间模型在完成校准前只作 shadow，不能成为权威评价器。

## 当前结果与含义

2026-09-24 有效 pilot 的三个任务均完成双臂与六检查点，所有硬 guardrail 和执行奇偶性通过。single-turn 与 full Partner 均为 `3/3`，`Autonomous Uplift=0.0`，`False Evolution Rate=0.0`。按修正后预声明的最小 uplift `0.01`，三项 Settlement 均为 `falsified`。

控制组共 3 次模型调用、1,596 tokens；完整闭环共 6 次模型调用、5,650 tokens。当前额外推理成本没有换来成功率增益。

这说明闭环基础设施可以运行，也说明当前简单任务有明显天花板效应，尚无证据表明增加 Critic 与 Commitment 能提升任务效果。下一轮要提高任务区分度并加入纵向记忆消费，不能把“Event 更多”当成能力提高。
