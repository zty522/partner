# ADR 0055：Sprint 18 主动课程与真实项目闭环启动

**日期**：2026-09-01  
**状态**：Accepted for isolated experiment；production promotion 未授权、未发生

## 决策

Partner 新增确定性的 Sprint 18 学习控制面，但保持生产 `manual_stable` 不变。真实轨迹先投影为
`LearningObservation`，`learning_eligible` 与 `promotion_eligible` 分开；安全且可审计的失败进入负学习，
但 truth/业务/交付/Receipt 任一未过都不能支持生产晋升。Selector 使用可复算 acquisition 分解与 critic，
RepairRecipe 只允许 R0–R2 的隔离 Candidate；Beta 后验保留最低探索概率，不直接写 `control_policy.json`。

Event-first 入口为 `learning_observation_ingest`、`learning_topic_select`、
`learning_candidate_propose`、`learning_policy_update`、`sprint18_learning_cycle` 和
`targetdiff_active_learning`/`targetdiff_active_robustness`。只有带持久 Campaign 身份的 `[sprint18=true]` 消息能在手动稳定模式调用这组
隔离控制面；普通用户消息不能借标记打开生产自治。既有 production canary 不得重写 Sprint 18 计划。

## 首批真实证据

- 历史迁移：652 条最新轨迹，586 条 learning eligible、89 条 promotion eligible、421 正样本、231 负样本。
- 首轮最高价值题目为真实 output-reference 合同失败，匹配 `recipe_typed_output_reference_v1`，不是固定题库。
- TargetDiff 使用真实 `affinity_info.pkl`、公开 `split_by_name.pt` 和固定 official test；问题是“下一批标注
  哪些训练样本”，不是选择回归函数。三臂初始 RMSE 严格相同。
- v2 组合策略三轮 RMSE 2.476578，未优于 MaxVar 2.440746，诚实 rejected；v3 降低多样性权重后改善为
  2.460643，仍 rejected；v4 权重 `[0.98, 0.02, 0]` 得到 2.376959，优于 MaxVar 2.440746 和 random
  2.512266。该结果只是一项真实跨领域正样本，不满足长期统计门，也不支持药效因果。
- 新 Campaign `campaign_74833fad4af8` 为 48 小时、02/04/05 轮转、最多双槽、MiniMax-M3 only；任务终态
  立即触发重新入库、选题与下一 WorkItem。首轮真实暴露并修复 Sprint Event 被 manual preflight 禁用、
  旧 production canary 污染隔离路线、slot 每 tick 重复 systemd start 三个问题；失败历史保留。

## 2026-09-01 追加决策：单次胜出必须经跨池稳健门

- 02/04/05 的真实成功证据原本分别位于 `active_learning`、`research_learning`，终态收集器只识别后者，
  导致 02/05 被错误记为 `reward=-0.45`。收集器与 manual Reward 合同现覆盖两类 namespace；三条旧轨迹
  以 append-only revision=2 纠正为学习奖励 `+0.55`，仍不具备 production policy 资格。
- TargetDiff seed 20260901 的 v4 胜出不足以支持策略改进结论。新增两个冻结预算随机池：20260902 上
  active `2.421570`，劣于 random `2.337788`；20260903 上 active `2.342277`，略劣于 MaxVar `2.338250`。
  三 seed 只赢 1/3，故 `sprint18_targetdiff_seed_robustness_v1` 决策为 `rejected_not_robust`。平均值虽略胜
  MaxVar，也不得覆盖预注册的 2/3 稳健门。
- 持续控制器只在存在高信息目标时续跑；旧证据耗尽后按有限课程获取“未读 Harness 组合、独立真实池、
  后验复算”，全部耗尽则 `WAITING_EVIDENCE`，禁止用重复调用伪造在线时长。
- `active_learning/robustness_evaluated` 现在会投影为 truthful-but-negative LearningObservation：实验真值通过，
  被测动作失败，二者不再混为一谈。首个 rejection 自动成为 acquisition 最高题，生成 R0
  `recipe_uncertainty_calibration_probe_v1`。其 labelled-only OOB error/RMSD slice Candidate 在同三 seed
  0/3 胜出、平均 RMSE `2.403997` 劣于 MaxVar `2.380523`，再次 rejected；policy 更新后停止重复选题。

## 尚未满足

三真实日期、每臂 20 个真实去重样本、两项目统计改善、Wilson 下界、false-success=0、anchor 抗遗忘、
真实手动消息验收和完整 rollback/canary 均需运行证据。因此 Sprint 18 是“核心实现完成、真实长期验收可恢复但尚未完成”，
不是“长期 RL 成熟”或“已生产晋升”。

## 回滚

停止 `campaign_74833fad4af8` 即停止新任务；Candidate 和 learning policy 均
`production_effective=false`。生产 `manual_stable` 与已有 append-only 事实不回写、不删除。

> 后续 uncertainty 诊断、estimator Candidate、Campaign 最终计数和学习/项目账本隔离以 ADR 0056 为准；
> 本 ADR 的阶段性数字保留为历史快照，不覆盖重写。
