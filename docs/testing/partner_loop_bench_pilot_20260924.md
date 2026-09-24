# Partner-LoopBench 真实 pilot（2026-09-24）

## 有效运行

- Protocol：`partner_loop_full_vs_single_v1@1.1.0`
- Project：`partner_core_v1_1`
- LLM：工作区 `config/api.json` 中的 Qwen `qwen3.8-flash`
- 三项任务：`project_01`、`learning_01`、`evolution_01`
- 结果：single-turn `3/3`，full Partner `3/3`
- 平均 Autonomous Uplift：`0.0`
- False Evolution Rate：`0.0`
- 预声明最小 uplift：`0.01`
- 聚合结论：`falsified`
- single-turn：42 个 subject Event 终态、3 次模型调用、1,596 tokens
- full Partner：42 个 subject Event 终态、6 次模型调用、5,650 tokens
- 结果：`partner_workspace/state/benchmarks/partner_loop_bench/pilot_20260924_173627.json`
- 图：`partner_workspace/state/benchmarks/partner_loop_bench/pilot_20260924_173627.png`

三个权威 run 分别是 `bench_e02ef3d997934d3d`、`bench_a6584c95fa584867`、`bench_a3a666ad67c34815`。每项都有 baseline/candidate 子 Flow、六个 checkpoint、隐藏 runner、执行奇偶性、硬 guardrail、父级比较和确定性 Settlement。三个 Settlement 均为 `falsified`，因为效果为零。

## 首轮无效运行与 benchmark 自修正

第一次提交的三个 Job 在新 subject 第一个 Event 前失败，原因为 EventDefinition 使用了未注册 series `benchmark_subject`。失败 Job 与 Flow原样保留。Event series 改为既有 `benchmark` 后，聚焦回归 `18 passed`，真实 Flow 才进入模型和执行阶段。

随后首个完整 pilot 暴露协议错误：`expected_effect.minimum=0.0` 会把零提升结算为 `confirmed`。该批运行只作为 calibration 证据保留，不作为效果结论。协议升级到 `1.1.0`，最低 uplift 改为 `0.01` 后重新完整运行，零提升被正确裁决为 `falsified`。

## 历史队列 reconcile

运行 pilot 前检查到 140 个 queued 与 20 个 running 的旧投影，全部来自 2026-09-18/19，Flow 仍写作 running，但没有 Partner worker 进程。本轮新增有年龄门的 reconcile 操作；先 dry-run，再把 160 个投影以 append-only Job history 和 Flow recovery history 收敛为 cancelled，不删除任何 Job、Flow、事件或产物。应用报告：`partner_workspace/state/maintenance/job_reconciliation/20260924_172912_291649_apply.json`。应用后旧非终态为 0。

## 证据边界

本轮证明盲化双臂 Event/Flow 框架和“零提升不误报”能够运行。它没有证明完整 Partner 优于单轮 LLM。三个任务对当前 Qwen 太容易，出现 100% 天花板；15 项任务包也仍是内部作者构造的离散选择题。下一次有效研究运行必须先做难度校准、独立密封答案和真实执行任务，再进行多 seed、纵向与消融比较。

在效果相同的情况下，完整闭环的模型调用数为控制组两倍，token 用量约为 `3.54×`。因此当前 pilot 下完整闭环还表现为额外成本，不能仅凭结构更完整主张价值。
