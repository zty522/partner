# Core v1 基础框架验收记录（2026-09-23）

## 验收范围

本轮验收的是 Core v1 的代码与 Event 接线，不是 01/02 真实科研项目的纵向 uplift，也不是 Jev 或潜模型的生产晋升。

覆盖内容：

- Commitment feature 合入唯一 `main` 前的聚焦回归；
- Core v1 类型合同、不可变存储、Jev 降级、潜模型训练/拒绝、触发政策；
- 三条最新 Flow 的 Commitment 前置和 Settlement 后置；
- 历史 Flow 版本解析；
- 确定性触发 benchmark；
- 全仓现状回归，用于识别不能被本轮结果掩盖的已有故障。

## 结果

1. 合并前 Commitment：`154 passed`。
2. 合并后 Core v1 + Commitment + Event/Flow 聚焦集：`174 passed`。
3. Core v1 新增专项：`8 passed`。
4. 触发 benchmark：10/10，`route_accuracy=1.0`，`unsafe_self_evolution_rate=0.0`。机器结果在 `partner_workspace/state/acceptance/core_v1_20260923/trigger_benchmark.json`。
5. 全仓：`1299 passed, 53 failed, 3 skipped`。失败集中在旧私有 `_save_job` 无 lease 测试、GEPA/DGM 旧 adapter 合同、idempotent submit/benchmark fixture、共享 worker 历史 fixture、退役前端测试和部分索引/桥接测试。该结果意味着仓库总体发布门仍未通过；不能用 173 项聚焦通过宣称 Partner 全系统可上线。

全仓回归发现的一个真实运行缺陷已在本轮修复：instance-bound worker 曾返回未持有 DB lease 的 Job，首个 checkpoint 因 `checkpoint requires an owned lease` 失败；现在与 shared worker 一样先原子 claim，再返回 Job。同一 worker 对自己已持有的 Job 可继续 checkpoint。

## 尚未完成的生产证据

- 没有 `TYPESAFE_API_KEY`，因此未产生真实 Jev latency、校准或一致性结果；当前行为是明确 `unavailable` 后继续。
- `state/core_v1/transitions.jsonl` 尚无真实项目样本，潜模型仍会因少于 8 条可信 transition 拒绝预测。
- 尚未让 01/02 分别完成一次真实项目、主动学习或自进化的最新 Flow；这应作为下一轮独立的三链验收，而不是在基础实现中伪造成功。
- 全仓 53 项失败需按模块建立修复批次；与 Core v1 发布相关的 shared/instance worker、索引和三链 fixture 应优先处理。

## 当前判定

Core v1 基础框架和静态 Event 接线通过，触发安全基线通过；生产全链验收未通过。Jev 与潜模型保持 shadow，Core v1 不应被标记为 production-gated 或证明真实 uplift。
