# ADR 0041：`planning.semantic_preflight` 真实业务配对、持续改善门与 Event-first 晋升

- 日期：2026-08-30
- 状态：已实施；04 限域生产激活；长期 Campaign 运行中
- 实验：`experiment_6ef2b9be620b`
- Candidate：`candidate_preflight_contract_v2`
- 决策键：`literature_github_learning:planning.semantic_preflight`

## 1. 问题与诊断

主动学习选择 `planning.semantic_preflight` 后，v6 诊断读取 13 个真实 Episode，得到系统性机制：

- `input_path_contract` 10；
- `evidence_output_contract` 4；
- `event_contract` 3；
- `artifact_contract` 1。

旧诊断曾把“未闭合步骤类型”误当该失败类的机制；失败 taxonomy 实验
`experiment_c1c3244dcaa4` 原样保留。修正后的 taxonomy 实验 `experiment_eaf4ad0a9017`
将下一目标收敛为 `planning.semantic_preflight/input_path_contract`，没有改写历史 Episode。

## 2. 修复内容

Candidate 路径在 BATCH_PLAN Event 前注入“用户明确点名、真实存在、位于只读白名单”的输入清单；
规划必须按输入逐一读取，并把单次 extract 的 `verified_sources` 交给生成步骤。生成后由 Harness
确定性保留每个 `source_path/evidence_quote`，不让模型改写已经核验的逐字引文。

同时修复了实验中暴露的四个基础合同问题：

1. Candidate verified-source 保留函数此前存在但没有接入 `generate_text` 输出路径；
2. `LICENSE` 等无扩展名真实输入没有被 preflight/引用质量门识别；
3. 业务 Planner 会误选 `agent_active_learning_*` 控制面 Event；现在控制面与业务面硬隔离；
4. 外部消息通道故障会把离线 matched observation 误标为业务失败；现在仅匹配实验允许用“真实非空产物 +
   证据归档”作为本地 observation，普通生产任务仍必须取得真实投递回执，且本地 observation 不获得
   `delivery_contract` 奖励。

## 3. 真实 matched 结果

三组任务分别读取 OpenClaw、Hermes Agent、OpenAI Codex 的 README、LICENSE 和包/工程清单；两臂使用
独立 task id、相同 `match_key`、相同输入与验收合同，并持久化路由证明。

| 指标 | Baseline | Candidate |
| --- | ---: | ---: |
| 完成 | 1/3 | 3/3 |
| 真值通过 | 1/3 | 3/3 |
| 可观测性通过 | 1/3 | 3/3 |
| mean Episode reward | 0.3 | 0.9 |
| semantic repair calls | 0 | 0 |

逐对 reward gain 为 `[0.9, 0.9, 0.0]`。持续改善门要求：至少 3 对、所有对不回退、至少 2 对严格改善；
本实验通过。这里证明的是 **04 的本地文件证据报告业务** 获得初始因果改善，不外推到五项目、模型权重、
浏览器动作或 QQ 网络。

权威评估：
`partner_workspace/share/mind/governance/experience_guided_policy/shadow_evaluations/experiment_6ef2b9be620b_isolated_preflight.json`。

## 4. Event-first 晋升与激活

Candidate 的执行合同仍是 `batch_plan` Event，允许实例仅为 04；评价合同是
`agent_active_learning_preflight_manifest_fresh_canary` Event。晋升前 `pytest -q` 得到
`555 passed in 110.92s`，`decide_evolution_experiment` 写入 `policy/promoted`；随后独立
`activate_promoted_candidate` Event 才修改 control policy，并同步 Candidate：

- `status=promoted`；
- `production_effective=true`；
- `promotion_decision_id=evoevt_1423e70520c8f82f6f7a`；
- 激活 Event：`evoevt_7e7d0d80a75c1fe75f42`。

这种两阶段语义防止“决策已通过但投影仍 false”或“只改 JSON 没有权威 Event”。无标记 04 生产任务现在
读取已晋升策略；显式 baseline/candidate marker 仍可隔离复现实验。

## 5. 长期验证与回滚（历史启动记录；当前状态见 ADR 0042）

最初启动 `campaign_b98311a476ce`：4 小时、01–05 五 lane、最多双槽、40 WorkItem、160 model calls、
60 cost units。该 Campaign 后续暴露确定性 Event 路由问题并由修复版替代；其失败证据保留。当前正式运行
`campaign_abf9e34bf6af`，终态契约、05 波次屏障和负样本策略聚合见 ADR 0042。

04 的 portfolio fingerprint 现在由“外部来源指纹 + 已晋升 policy/Candidate 指纹”组合；策略变化会触发一次
真实生产 canary，不必伪造外部仓库变化。

回滚只需通过治理事件移除决策键
`literature_github_learning:planning.semantic_preflight`；Baseline 代码路径、历史 Episode、失败实验和三组
matched evidence 全部保留，不删除历史。

补入独立生产激活 Event 与 04 policy fingerprint 测试后，最终全量回归为
`556 passed in 111.85s`，attestation 已更新。

## 6. 不能宣称的内容

- 这不是 LLM 权重训练，也不是通用 Agent RL 已完成；当前是 evidence-backed contextual policy learning。
- 不能用这三对 04 样本证明 01/02/03/05 都持续改善。
- Campaign `running`、服务 active、文件生成都不是业务成功；必须继续看 Receipt、truth、delivery、
  business_progress 和下一轮是否真实承接。
