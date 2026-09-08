# ADR 0073：语义 Reward、生产动作 Bandit 与自进化诊断边界

日期：2026-09-08  
状态：已实施并完成生产短跑；术语与新存储路径由 ADR 0074 取代

## 决策

Partner 的三条链保持独立：项目迭代负责业务产物；主动学习只指向 GitHub、论文等外部知识；自进化只诊断和修改 Partner 自身机制。兼容期内 `agent_active_learning_*` Event 标识不删除，但所有新结果必须标记 `semantic_kind=partner_self_evolution`。

生产项目的下一动作不再由 `project_steps % len(options)` 决定。01–05 在依赖启动阶段之后统一使用 `ucb_contextual_bandit`，输入是同项目、同 action arm 的真实终态 trajectory Reward。未观察动作优先探索；已有样本后使用经验 Reward 与不确定性奖励选择。每四轮且前两项得分近似平局时允许 LLM 作为语义 critic 二选一；LLM 不能创造 Event、修改 Reward 或越过硬门。

Reward 新颖性不再比较完整字符串。先把 task/receipt/candidate ID、路径、时间和裸数值归一化，再按 action arm 与稳定语义 claim 比较。只换 seed、文件名或测量数值的重复结论记 `duplicate_outcome=true`、Reward=-0.1。定量结果只有显式给出 baseline/counterfactual 改善合同才可重新获得新颖性。

一次语义重复先作为 Bandit 负反馈，直接选择反事实 action；连续平台或真实执行失败才触发一次 Partner 自进化。内部 diagnose 增加 LLM 因果机制、反证和最小实验建议，但 Episode 覆盖率、allowlist、matched baseline/candidate、pytest 与 rollback 仍是生产硬门。

## 可审计数据

- 动作选择：`share/mind/governance/experience_guided_policy/native_action_selections/*.json`
  （历史 `share/mind/governance/experience_guided_policy/...` 为兼容镜像）
- 终态 Reward：`share/mind/governance/experience_guided_policy/trajectories.jsonl`
- 自进化诊断：`share/mind/governance/active_learning/diagnoses/*.json`（路径名为兼容遗留，语义字段为准）
- 每次选择记录 arm 样本数、平均 Reward、UCB score、选择原因与可选 LLM review。

## 边界

这是一种受治理的 contextual bandit / offline-RL 风格策略学习，不是 PPO、DQN，也没有更新基础 LLM 权重。完整回归通过只证明合同正确；能否跨日期持续改善仍必须由真实项目 trajectory 证明。

## 生产短跑结果

- 03 在旧动作 Reward 近似平局时真实调用 LLM critic，选择 `03_md_timestep_stability`，并记录选择理由与反例；后续相同实验结论均被正确记为 `-0.1`。
- 01、02、03 的既有固定实验大多收敛为 `duplicate=true / reward=-0.1`；Bandit 会换 arm，但没有新输入/新假设时不会伪报业务改善。
- 04 的 Harness 源码轮换在读取不同真实代码面时获得数轮 `0.85`，同一语义证据耗尽后回落到 `-0.1`；外部 scout 无新 repo/paper 时诚实失败为 `-0.45`。
- 自进化诊断真实产出 `llm_diagnosis`，含因果机制、反证和最小实验；诊断轮只记 learning progress，不冒充 business progress。
- 短跑发现并修复两处二阶 Reward hacking：控制字段 `学习干预=True` 不再制造 novelty；duplicate 计数不再污染另一 action 的失败预算。
- 最终完整回归：`962 passed, 2 warnings in 113.34s`。
