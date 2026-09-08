# ADR 0050：学习型 Candidate 的生产成熟度总门

**状态**：Accepted；当前 research-adoption Candidate 被阻断  
**日期**：2026-09-01  
**生产决定**：不激活；`manual_stable` 和现有 `control_policy.json` 保持不变

## 背景

ADR 0049 已证明多源证据 Candidate 能被本地确定性 Agent 消费，但用户要求进一步证明通用 LLM、真实项目
业务产物的持续改善和长期 RL 成熟，然后才接入生产。这三个命题不能由一次上下文 benchmark 互相替代。

## 决策

新增 `learned_candidate_readiness_v1`，学习型上下文/策略 Candidate 即使已有 `policy/promoted` Event，也必须
提交哈希校验通过的 workspace 内 readiness attestation，才能调用独立 activation Event。总门包含：

1. **通用 LLM**：至少两个外部模型家族；每个模型至少三组同设置 matched tasks；Candidate truth/safety
   全通过、均值提升至少 0.10、逐任务不回退，且外发 payload 脱敏审计通过。
2. **持续业务产物**：同一 Candidate 至少六轮真实完成、渠道确认送达且文件有效的业务轨迹；至少两个项目、
   三个时间窗、六个不同 outcome，平均可验证 Reward 至少 0.70。
3. **长期 RL**：同一 Experiment 每臂至少 20 个真实样本；包含两臂负样本；Candidate reward gain 至少 0.15、
   95% Wilson 成功率下界至少 0.67、零 false-success；覆盖至少两个项目、三个时间窗并通过回滚演练。
4. Event ledger 必须有效。任何一门失败时 `production_ready=false`，激活入口 fail closed。

## 当前实证

对 `candidate_research_downstream_a420574f08ad` 的正式审计结果位于：

`share/mind/governance/experience_guided_policy/production_readiness/candidate_research_downstream_a420574f08ad.json`

结果为 `blocked`：

- 唯一 MiniMax-M3 外部实验是 DNS 失败记录，reward delta=0；没有两个通过的外部模型家族。
- 该 Candidate 当前没有已确认送达的业务轨迹，因此持续业务门 0/6。
- 该 Candidate 没有 baseline/candidate 长期轨迹，RL 门没有可评价 Experiment。
- 全局 RL 虽有 546 条历史轨迹，但最大可比组只有 baseline 10、candidate 13，来自同一天、同一项目，
  Candidate 仍有 false-success；不能迁移成该 Candidate 的成熟证据。
- Event ledger 有效，348 Events；现有生产策略未变。

外部 MiniMax 调用即使使用不可逆脱敏，仍涉及向具体外部服务发送真实项目的派生信息。安全门要求用户明确
授权 MiniMax；“验证通用 LLM”不能自动推定为该项数据外发授权。当前没有本地模型。MiniMax-M3 与
DeepSeek v4-flash 均已配置，matched runner 已支持显式选择两者，但在获得对这两个具体外部服务的脱敏
项目派生数据外发授权前不会调用。

## 实现与验证

- `partner/governance/production_readiness.py`
- `scripts/audit_candidate_production_readiness.py`
- `partner/governance/candidate_skills.py`：学习型 Candidate activation 强制验证 attestation
- `partner/v2/governance_events.py`
- `tests/test_production_readiness.py`
- 完整回归：`653 passed in 333.73s`

在三门全部产生真实证据前，不得把“代码已经具备成熟度检查”写成“RL 已成熟”，也不得接入生产。
