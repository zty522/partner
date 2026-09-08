# ADR 0048：多源证据编译为代码 Candidate 与五项目真实匹配实验

**状态**：Accepted for continued shadow evaluation；Policy 仍为 inconclusive  
**日期**：2026-08-31  
**生产边界**：`manual_stable` 与 `control_policy.json` 不变；不启动 Campaign，不自动 promotion

## 背景

ADR 0047 已证明 04 能真实读取源码/论文、选择高信息价值来源并更新证据信念，但“读到资料”仍不等于
Partner 已采用资料。下一硬门要求至少两条互补外部证据和 Partner 本地实现证据共同约束一个最小代码
Candidate，再在真实项目状态上走隔离的 baseline/candidate Event 对照。

## 决策与实现

只采用一个小切片，不复制任何外部 Harness：

1. Hermes/Codex 的上下文压缩经验约束“保护项目交接、头部规则与预算”；
2. JitRL 的直接论文证据约束“按当前状态检索 `<state, action, reward>` 轨迹，不更新模型权重”；
3. Partner 自己的 `context_selector.py`、`manual_runtime.py`、`storage.py` 作为本地可运行合同与 SHA256 证据；
4. 编译器要求 observed manifest、至少两个独立来源、code+paper、三类机制 Claim 和至少三个 Partner
   本地文件全部通过；任一缺失则拒绝生成 Candidate；
5. 产物 `candidate_evidence_trajectory_context_v1` 只能经
   `execute_candidate → research_adoption_context_shadow` 白名单 Event 执行，允许 04/05 shadow，
   `production_effective=false`。

匹配评价器刻意不伪装成 Candidate 自身的可执行 Event：`evaluation_ready=false`，由独立脚本创建新
Issue/Experiment、冻结 oracle 与预算后执行。这样 intervention 不能自行决定自己的成功。

运行时 Candidate 在固定总预算内组合 canonical docs、受保护的最新 Receipt、同项目相关轨迹和有来源身份的
研究摘录。轨迹按查询相关性、真实 Reward、业务/学习进展、失败机制与新颖证据排序；查询只读取同一
`project_id`，禁止跨项目经验泄漏。

## 第一次实验：失败并保留

- Experiment：`experiment_0d6cd09d84d8`
- 结果路径：`instances/05/state/tasks/research_adoption_20260831_185129/real_project_matched_experiment.json`
- 五项目均执行；平均证据召回 `0.067 → 0.867`，但硬门失败。
- 根因 1：生产 `select_context` 的 `used_chars` 只统计正文，没有统计 provenance/代码围栏包装，真实 bundle
  比声明预算多 284 字；这不是 benchmark 格式问题，而是已有预算合同 bug。
- 根因 2：01/05 的 Receipt 被截断时，位于 JSON 尾部的 `receipt_id` 丢失；“选择了 Receipt”不等于
  关键交接身份实际进入上下文。
- Policy：`inconclusive`；失败记录、Candidate execution Events 和结果文件均未覆盖。

## 修复与第二次实验

- `context_selector` 现在按最终序列化 bundle 精确计入 provenance wrapper；新增回归直接断言
  `len(context) <= budget_chars`。
- Candidate 在上下文首部写受保护的最小 handoff（Receipt ID、project、iteration、goal、findings、
  next actions、stop reason 和 delivery），然后再分配 canonical/trajectory/research evidence 预算。
- 新 Experiment：`experiment_7cb945f44c9e`
- Candidate：`candidate_research_adoption_7cb945f44c9e`
- 结果路径：`instances/05/state/tasks/research_adoption_20260831_185347/real_project_matched_experiment.json`
- 五组来自当前真实项目状态：01 小红书、02 分子生成、03 Partner 框架、04 文献/GitHub、05 自进化。
- 每组冻结 query、project、最新 Receipt、独立 oracle trajectory、action key 和 9000 字预算；baseline 与
  Candidate 使用同一 execution marker。
- 平均证据召回：baseline `0.0667`，Candidate `1.0000`，delta `+0.9333`。
- 7/7 硬门通过：五项目存在、配对身份相同、召回改善、Receipt+trajectory 全保留、无跨项目泄漏、
  两臂均在预算内、生产未修改。

## 为什么仍是 inconclusive

本实验已经证明“外部证据形成了可执行代码 Candidate”以及“它在五个真实项目快照上更完整地准备项目
承接证据”。但它评价的是下游推理前的 context/evidence preparation，不是五个项目的最终业务产出；每个
项目也只有一个冻结快照。因此不能据此宣称：

- 下游 LLM 的答案质量或任务完成率已改善；
- RL 已使业务长期持续变好；
- Candidate 可以进入 production；
- Partner 可以无界自动运行。

下一硬门应在其中一个项目上运行至少三组独立、同模型/工具/预算的下游任务对照，评价 truth、业务产物、
可观测性、成本和 Reward；只有随后完整回归及显式 PromotionDecision 才能讨论限域激活。

## 实现和验证

- `partner/governance/research_adoption.py`
- `partner/v2/research_learning_events.py`
- `partner/v2/candidate_events.py`
- `scripts/run_research_adoption_real_project_experiment.py`
- `tests/test_research_adoption_candidate.py`
- 完整回归：`641 passed in 99.67s`。
