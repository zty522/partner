# Adaptive Project Cycle v2 单任务真实验收（2026-09-24）

## 结论

最终隔离运行 `job_b14bfc0c820d43bf` 完成 `project_cycle@2.0.0` 的 36 个父 Flow 节点，`failed_node_ids=[]`。本次证明 Qwen 主模型、Jev shadow、潜状态预测、Commitment、真实执行、独立验证、Settlement、条件迭代、文字交付、图文 PDF、记忆更新、Partner 自审计与最终 ACK 能在一个父 Cycle 中顺序完成。它只是一项合成回归任务，不证明跨项目自治、自进化 uplift 或主动学习 uplift。

## 冻结协议与项目结果

- 原始协议明确要求两轮：第一轮生成固定 `seed=20260924` 数据并建立仅含 `x1` 的 baseline；第二轮只能消费第一轮数据与切分，并增加预声明的 `x2`。
- Cycle 冻结 `max_rounds=2`、`minimum_rounds=2`。第一轮历史执行输入索引为空，未复用其他 Job 结果。
- 第一轮独立验证通过，test RMSE 为 `5.481857656395991`；Settlement 因显式协议下限选择 `continue_project`。
- 第二轮通过 SHA-256 绑定第一轮数据，test RMSE 为 `0.9370380247002416`，改善 `4.544819631695749`；Settlement 选择 `complete`。
- 两轮 Jev 均通过 OpenRouter `typesafe/jev-1.13-20260917` 返回类型化 shadow 判断；Jev 没有覆盖确定性 Settlement。

## 交付、记忆与自进化

- 阶段文字消息通过独立事实审查并取得本地 ACK。
- PDF 子 Flow 完成真实标量图、来源核验、正文主张审查、渲染、质量检查、发送与 ACK。
- lesson、growth、habit 分别更新；growth 与 habit 保持候选状态，不把一次成功写成持久能力。
- `cycle.partner_audit` 固定运行，覆盖消息理解、方案、Event Flow、执行、轮次衔接、学习消费、项目推进、消息、PDF、ACK 和记忆。
- 本轮审计问题数为零；`cycle.evolution_gate` 没有放行候选，`cycle.evolution_request` 诚实返回 `no_change`。项目 RMSE 没有进入 Partner 自进化。
- 最终消息通过事实审查并取得 ACK；`completion.json` 记录 `delivery_verified=true`、`post_run_audit_completed=true`、`evolution_gate_completed=true`。

## 本轮修复所保留的失败证据

验收没有改写早期失败：`job_e85defadc49d4f58` 暴露模型收尾超时误伤真实命令产物；`job_5f7bda316fd84bbd` 暴露第一轮成功后提前结束；`job_c6661400aca242d2` 暴露跨 Job 历史产物污染首轮；`job_bc55a289765d4011` 暴露候选 JSON 指标命名导致图表规划失败。对应修复均有独立回归，最终使用新目录与新 Job 验收。

## 测试

- 自主 Cycle、Core v1 与图文报告相关测试：`66 passed`（在最初 63 项通过后，将三项最终验收缺陷固化进受版本控制的 Core Event 测试并复验）。
- 终端超时恢复、最终改稿复审、最终消息路由与首轮来源隔离专项：`6 passed`。
- `python -m compileall -q partner` 与 `git diff --check` 通过。

仓库中部分历史测试仍依赖旧的无租约 `_save_job`、已退休前端或旧消息规则，不计入本次 72 项相关回归；此前扩展集合运行结果为 144 passed / 14 failed，这些失败不由本次功能制造，也未被改写为通过。

## Benchmark 口径

本次真实任务运行了一个有固定数据、固定 seed、同数据 baseline/candidate、独立机器评价和两轮 Settlement 的项目级 matched canary。它验证了单条纵向闭环，并得到 baseline RMSE `5.481857656395991`、candidate RMSE `0.9370380247002416`。

本次没有通过 benchmark wrapper 执行任务集、多个 seed、多个系统 arm、失败注入和聚合置信区间，故不能称为完整系统 benchmark。72 项是代码回归测试，也不是科学效果 benchmark。主动学习 Flow 只冻结了来源绑定 handoff，尚未做采用前后 downstream matched comparison；自进化本轮为 `no_change`，尚未做真实 Partner 缺陷的 baseline/candidate replay。

后续状态更新：上述缺口已由独立的三链 benchmark 补齐，不能回写成本次单任务 Cycle 已经执行过。项目多任务/多 seed wrapper、该 handoff 的 downstream matched comparison 和隔离自进化 replay 结果见 `docs/testing/core_v1_three_chain_benchmark_20260924.md`。
