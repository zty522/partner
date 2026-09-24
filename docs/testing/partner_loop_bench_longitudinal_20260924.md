# Partner-LoopBench 真实执行与纵向记忆验收（2026-09-24）

## 真实执行 pilot

在首轮选择题校准之后，新增三个隔离 runner：

- 项目迭代：真实生成分组数据，训练五折 HGB；随机 K-fold 虽得到略低 RMSE，但因部署目标是未见 target groups，被 leakage guardrail 拒绝。
- 主动学习：在当前 sklearn 环境真实执行 `root_mean_squared_error`、手工平方根和已移除 keyword 三种候选；只有来源绑定的现行公开 API 同时通过兼容性与 adoption 合同。
- 自进化：真实构造 477 个 Job JSON 和 native SQLite ready index；全量扫描能找到 Job，但因读取 477 个历史文件而失败，索引查询读取 0 个 JSON 并通过。

single-turn 与 full Partner 在三个任务上仍均为 `3/3`，Autonomous Uplift=`0.0`，结论为 `falsified`。结果位于 `partner_workspace/state/benchmarks/partner_loop_bench/pilot_20260924_174934.json`。控制组消耗 1,865 tokens，完整闭环消耗 4,559 tokens；真实执行没有消除天花板效应。

## Warmup 与 transfer 协议

纵向实验增加 `partner_loop_memory_vs_none_v1@1.0.0`。每个领域先运行一个公开 registry 映射 warmup；只有 baseline 与 candidate 的真实执行都为 `task_success=1`，wrapper 才把一条经验写入记忆。经验记录包含来源 report、SHA-256、来源 run 与验证状态。

transfer 时两个 arm 使用相同任务、runner、模型、Critic、Commitment 与预算。唯一差异是 baseline 不获得历史，candidate 获得当前已验证记忆。`loop_bench.task_inspect` 回读来源并核验 SHA-256；父级 `memory_source_verified` guardrail 从 child checkpoint 独立确认 baseline 没读记忆、candidate 只读到有效记录。

## 结果

- Warmup：三个领域均为 baseline `1`、candidate `1`，只是形成可验证学习来源，不计 transfer 增益。
- Transfer/no-memory：`0/3`。
- Transfer/verified relevant memory：`3/3`。
- Learning-to-Action Gain：`1.0`。
- 三个 transfer Settlement：`confirmed`。
- warmup＋相关 transfer 总成本：baseline 9 次模型调用、5,892 tokens；candidate 12 次、11,111 tokens。
- 结果：`partner_workspace/state/benchmarks/partner_loop_bench/longitudinal/longitudinal_20260924_175920.json`。
- 图：`partner_workspace/state/benchmarks/partner_loop_bench/longitudinal/longitudinal_20260924_175920.png`。

逐任务选择发生了可检查的变化：项目从 `recipe_b04` 变为已验证的 `recipe_a17`；学习从 `adapter_m08` 变为 `adapter_k22`；自进化从 `retrieval_t05` 变为 `retrieval_q31`。

## 记忆特异性负对照

为排除“多给一段文字就会提升”，对三个 transfer 分别提供来源和哈希均有效、但来自另一个领域的经验。no-memory 与 irrelevant-memory 都是 `0/3`，平均 irrelevant-memory effect=`0.0`，三个 Settlement 均为 `falsified`。结果：`partner_workspace/state/benchmarks/partner_loop_bench/longitudinal/memory_specificity_20260924_180409.json`。

## 结论边界

这证明当前 Event/Flow 能把前轮已验证结果转成有来源约束的记忆，并在下一轮实际改变动作和结果；无关记忆没有产生同样效果。它仍是内部构造的 registry-transfer 机制验收，映射复用是刻意设计的，任务数只有三个，没有多 seed，也没有让 Partner 自主发现应该学习什么。它不能证明开放世界主动学习、未知缺陷自进化或长期泛化已经成熟。

下一阶段应把映射换成外部维护的隐藏任务：让 Partner 自己识别缺口、选择和读取来源、生成 adoption candidate，再在不同但相关的真实项目中测量迁移；同时加入错误记忆、冲突记忆、记忆过期和跨模型复现。
