# ADR 0074：经验驱动策略学习与可证伪 Event Candidate

日期：2026-09-08  
状态：已实施；生产纵向效果仍待持续观测

## 决策

对外不再把当前动作选择机制简称为“RL”。正式名称为**经验驱动策略学习**（Experience-Guided Policy Learning，EGPL）。其内部选择器是 UCB contextual bandit，读取可验证终态 Reward；它没有训练基础模型权重，也不是 PPO、DQN 或完整传统强化学习。旧状态已一次性迁移，当前只使用 `experience_guided_policy/`、`selection_algorithm` 与 `policy_decision_key`，不再双写、回退或保留旧目录和旧字段。

项目历史连续三次非正 Reward 后，注册 Event `project_hypothesis_propose` 可调用 LLM 提出一个新方向。LLM 只能选择预先登记的 Event、策略和有界整数参数，不能生成任意代码或自行扩展测量能力。生成的 Candidate 先通过 Event allowlist、参数边界和测量契约隔离检查，再作为一个 canary arm 加入该项目动作池；真实执行后的终态 trajectory 决定继续、拒绝或进入后续验证。

## 防止“会说但不能测”

每个 Candidate 必须声明 `measurement_contract`。权威 hypothesis、opposing hypothesis 和 falsifier 由代码根据 Event 的真实业务指标生成；LLM 原话保存在 `llm_advisory`，仅供审计和启发。若缺少契约、执行证据未出现任何声明指标、结果重复或 Reward 不高于冻结基线，Candidate 自动拒绝。

本次生产探针发现并拒绝两条语义越界候选：03 的温度扫描被 LLM 误说成验证热容/滞回，02 的 RDKit QED/SA 基准被误说成验证 AiZynthFinder/IBM RXN。两条 Event 均真实运行，但实验命题超出测量能力且 Reward 均为 `-0.1`，因此不能作为成功学习证据。历史记录不改写，Candidate 状态追加拒绝原因 `missing_event_measurement_contract`。

## 哲学推理契约

LLM 的选题与近似平局 critic 共用六段操作契约：现实接触（Perceive）、联想但不冒充因果（Associate）、先验与能力边界（Constrain）、主张/反对者/反例（Dialectic）、现有结构不够时区分外部学习与系统顺应（Accommodate）、选择最小可证伪 Event 并根据结果修改下一行动（Act–Reflect）。哲学概念只组织思考，不能替代工具证据。

三条账本继续严格分开：项目真实产物与领域指标是 `business_progress`；GitHub/论文获取、阅读和采用建议是 `external_active_learning`；Partner 代码或策略的隔离对照、回归、可逆晋升是 `partner_self_evolution`。报告、消息送达、运行时长和新颖措辞本身均不算进步。

## 当前边界

这次完成的是“LLM 提案 → 有界 Event Candidate → 测量契约 → canary Reward”的第一段闭环。即使三次 canary 为正也只进入 `ready_for_matched_experiment / production_effective=false`，不能自我晋升；后续必须由独立 baseline/candidate Event 和 promotion ledger 决策。跨日期持续改善、外部知识转代码采用、不同失败机制的代码 Candidate 和长期统计仍须继续验证。

## 验证

- 定向：22 passed。
- 完整仓库：966 passed，2 warnings，585.20 秒。
- 生产越界探针：两个真实执行 Candidate 均被拒绝，未宣称提升。
