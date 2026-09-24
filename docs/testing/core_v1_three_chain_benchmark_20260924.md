# Core v1 三链真实 benchmark（2026-09-24）

## 结论

Core v1 缺失的三类效果验收已通过生产 Event/Flow benchmark wrapper 实际运行。统一闭环产物为 `partner_workspace/state/benchmarks/closures/core_v1_closure_20260924.json`。本轮创建的所有根 Job 均已终态，当前没有 Partner OS 进程。扩大检查发现工作区仍保留 2026-09-18/19 等更早日期的 140 个 queued 与 20 个 running 状态投影；它们不属于本轮，也没有对应活进程。服务保持 inactive，后续启动生产 worker 前应另做历史队列 reconcile，不能把“本轮停止”写成“全工作区队列为空”。

## 项目迭代矩阵

数据使用 DeepDTA 公开 Davis kinase binding affinity 数据的 68 个化合物、442 个靶点和 30,056 个有效配对，原始来源及逐文件 SHA-256 保存在 `partner_workspace/benchmark_data/davis/source_manifest.json`。pKd 按 `-log10(Kd/1e9)` 转换。来源：[DeepDTA repository](https://github.com/hkmztrk/DeepDTA)、[数据说明](https://github.com/hkmztrk/DeepDTA/blob/master/data/README.md)。

冻结 HGB、target-group 五折、同一数据和预算，对 `target_basic`、`target_kmer32`、`target_combined` 三个预声明特征任务分别运行 seed 17 和 29。每个矩阵单元都是完整 `benchmark_experiment → benchmark_subject baseline → benchmark_subject candidate → deterministic evaluators → Settlement`，含六个检查点和 Jev shadow。

- Suite：`suite_a2cb244d4d3549d8`
- 6/6 有效，6/6 `confirmed`，所有硬 guardrail 通过。
- 平均 RMSE 改善：`0.10970990554709352`。
- 跨运行 bootstrap 95% CI：`[0.10358955129017555, 0.11502295593227006]`。
- `target_basic` 平均改善 `0.11524729141389306`；`target_kmer32` 为 `0.10006080634987008`；`target_combined` 为 `0.1138216188775174`。
- 所有单次运行的 paired bootstrap CI 均高于零，也超过预声明最小改善 `0.03`。
- Suite 同时输出 `suite_result.json`、`suite_report.md` 和 `suite_effects.png`；图中显示三个任务均值、各 seed 点、预声明 `0.03` 门槛及跨运行 95% CI。

## 主动学习 downstream matched comparison

输入为真实主动学习 handoff：`job_learning_real_af541928f231/.../learning_handoff.json`。该 handoff 基于 sklearn 官方源码提出从已移除的 `mean_squared_error(..., squared=False)` 迁移到 `root_mean_squared_error`。

- Canonical Job：`job_83408b4ec2fe4698`
- Canonical Run：`bench_ca51b297ff164f7b`
- baseline 在当前 sklearn 1.8 环境的两项冻结输入均失败；candidate 两项均与独立数学 oracle 一致。
- failure RMSE：`1.0 → 0.0`，effect `1.0`，95% CI `[1.0, 1.0]`。
- handoff source hash、同输入、独立 oracle、预算和产物五项硬 guardrail 全部通过。
- 确定性 Settlement：`confirmed`；Jev `completed`、`authoritative=false`。
- `benchmark.effect_record` 已把 `improvement_verified=true`、effect、CI 和 handoff SHA-256 写入 `state/active_learning/benchmark_settlements/bench_ca51b297ff164f7b.json`。

这项结果把该 handoff 的 `improvement_verified` 从“尚无下游证据”推进为有一项匹配环境证据支持。结论范围只覆盖 sklearn API 兼容性，不外推为其他主动学习主题都有效。

## 自进化 baseline/candidate replay

同一 source-bound handoff 被转成一个隔离的 Partner 指标适配器缺陷：baseline 使用当前 sklearn 已删除的参数；candidate 做最小 API 替换。两个 arm 在临时目录、相同 Python、相同 pytest、相同输入和相同超时下执行，生产源码没有被修改。

- Canonical Job：`job_597a6f57b3664cfe`
- Canonical Run：`bench_6ddcfa3d33744762`
- baseline：2/2 测试失败；candidate：2/2 测试通过。
- failure RMSE：`1.0 → 0.0`，effect `1.0`，95% CI `[1.0, 1.0]`。
- 隔离执行、同测试、基线复现、无新增回归、预算和产物六项硬 guardrail 全部通过。
- 确定性 Settlement：`confirmed`；Jev `completed`、`authoritative=false`；`production_effective=false`。
- `benchmark.effect_record` 已把匹配效果和输入 SHA-256 写入 `state/self_evolution/benchmark_settlements/bench_6ddcfa3d33744762.json`。

这证明自进化的隔离执行与匹配裁决能够识别“基线失败、候选修复、无回归”。该缺陷是冻结 benchmark fixture，尚不证明 Partner 能对未知生产缺陷稳定完成自主发现和补丁生成。

## Wrapper 缺陷及修复

第一次矩阵运行发现 `PartnerBenchmarkWrapper.wait()` 在父 Flow 暂停等待 child arm 时提前返回，导致套件把 `running` 当终态并继续提交。失败 suite `suite_afde3419024e49a6` 和三个父 Job 原样保留；三个 Job 随后恢复并真实完成。wrapper 现持续轮询、重新取得受限租约并驱动父/子 Flow，直到根 Job 进入 `completed/failed/cancelled/blocked` 或超时。最终 suite 使用修复后的行为，未出现提前聚合。

## 回归与边界

benchmark、Core 和模型配置聚焦回归 `27 passed`；编译、YAML catalog 与 diff 检查通过，其中包含 wrapper 暂时无可运行节点时继续等待根终态的回归。新增 `run_core_v1_closure.py` 可从一个入口重跑项目矩阵、主动学习和自进化三项验收。

本轮是 Core v1 的完整工程闭环 benchmark，不是论文最终 benchmark。它仍是一个分子数据集、三个 target-feature 任务和两个 seed；论文级结论仍需更多数据集、不同项目域、隐藏任务、消融、成本/时延比较及外部复现。
