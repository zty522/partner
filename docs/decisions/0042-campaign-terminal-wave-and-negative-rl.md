# ADR 0042：长期 Campaign 终态契约、学习波次屏障与负样本 RL

- 日期：2026-08-30
- 状态：已实施；正式 Campaign 已由用户显式停止（运行结果保留）
- Campaign：`campaign_abf9e34bf6af`
- 前置决策：ADR 0041

## 1. 为什么前两次长跑看似执行、实际却失败

第一次 `campaign_b98311a476ce` 暴露 `manual_stable` 会把 Campaign 明确授权的确定性 Event 重新送入
通用 Planner。该问题修复后，第二次 `campaign_cacd94a1a320` 的 01–04 均实际执行成功，生成了产物，
`push_files`、总结消息和五阶段进度回执也全部显示 delivered；但 `STOP_PROJECT` 终态遗漏
`completion_ok` 与 `delivery_confirmed`，治理层又把已经完成的 TaskInstance 改写为 failed。重试随后耗尽
failure budget。

两次 Campaign 均以 `superseded_after_fix:` 取消，失败 Task、产物、Episode 和事件日志全部保留。它们是
框架负面证据，不能删除或改写成成功。

## 2. 终态契约修复

Campaign 直达 Event 仅在同时满足以下条件时生效：

1. 用户请求含持久 `[PARTNER_CAMPAIGN campaign_id=...]` 标记；
2. 请求明确写出“直接执行确定性事件 `<registered_event>`”；
3. Event 位于注册白名单，且没有“禁止/不要/不得”语义。

确定性处理器完成后，`_direct_campaign_terminal_payload` 将 `completed_files`、`completed_event_types`、
`completion_ok`、`delivery_confirmed` 和可读 findings 显式传入 `STOP_PROJECT`。终态治理不再从“有文件”
猜成功，也不会丢失已经验证的成功事实。

修复后正式 Campaign 的 01、02、03、04 首轮均在第一次尝试完成，有 EvidenceManifest、五阶段回执、
真实投递和 `business_progress=true`。

## 3. 防止 05 学习槽饥饿

业务完成会更新 ProjectState，进而改变自身输入指纹。旧调度器在同一 tick 立即准入下一业务项，导致
05 永远等不到“所有业务非终态项同时为空”。现在增加有限波次屏障：

- 已有至少两个已结算业务 WorkItem；
- 当前没有正在执行的业务 WorkItem；
- 当前业务 outcome fingerprint 尚未被 05 消费；

满足三项时，新的业务指纹暂记为 `waiting_learning_barrier`，先准入一个
`offline_policy_learning_self_evolution`，随后立即释放屏障并继续业务轮转。屏障不终止正在运行的业务，也不让 05
获得生产晋升权。

实跑中 05 已完成三次离线学习，分别消费新终态轨迹并更新 Candidate policy；当前 Campaign 已进入低频
Scout 等待，而不是因队列暂空退出。

## 4. RL 必须学习失败，而不是只学习幸存者

此前失败轨迹会被保存并获得负 reward，但由于 `policy_eligible=false`，Candidate policy 聚合只统计成功
业务进度，存在幸存者偏差。现在分离两种语义：

- `learning_observation_eligible`：动作属于 01–04 有界业务且结果可归因；成功、blocked、failed 都进入
  该动作的 reward 分布；
- `policy_eligible` / `eligible_for_canary`：仍要求真实业务进步、样本数、均值和成功率门，失败样本不能
  获得晋升资格。

01 的 `01_content_readiness_gate` 因 PDF 纯正文 696 字、低于详细报告 700 字门而失败。报告不是降门，而是
补入实际的验收与恢复条件；恢复 canary 随后生成 JSON、Markdown、PDF 并真实送达。最新策略统计为
3 样本、1 个非正样本、mean reward `0.2967`、success rate `0.6667`，仍低于 `0.67`，因此
`eligible_for_canary=false`。这证明系统既能记住失败，也不会因一次修复成功立刻晋升。

## 5. 正式运行证据（历史终止前快照）

`campaign_abf9e34bf6af`：

- 4 小时，deadline `2026-08-31T00:15:41+08:00`；
- 01–05 五 lane 轮转，`max_active=2`；
- 当前快照：30 个 WorkItem，29 completed，1 blocked；2 次失败计数来自该 blocked 项的两次尝试；
- 3 个 05 WorkItem 均 completed；
- 当时无活动槽，等待 `2026-08-30T20:40:09+08:00` 的低频 evidence Scout；该 Campaign 后续已由用户
  显式停止，当前不是 running；
- 全量回归：`560 passed in 112.32s`。

这可以证明“长期控制器已完成多轮五项目轮转，05 能消费真实失败和成功终态并更新候选策略”。仍不能证明
五项目业务效果都随时间单调提升，也不是 LLM 权重训练。04 的持续业务改善因果证据仍以 ADR 0041 的
三组 matched experiment 为准。

后续状态校正：停止后用户改为手动运行 04；该手动生产负样本及其学习缺口见 ADR 0043。

## 6. 回滚与后续观察

- 终态传递、波次屏障和负样本聚合均有独立回归测试，可分别回滚；不得删除历史失败 Campaign。
- 后续至少观察到 deadline/final report，核对 Scout 不冒充业务进步、max-active 不超过 2、预算不超限。
- Candidate 仍需 matched baseline/candidate、回归 attestation、PromotionDecision 与独立 activation Event；
  离线 policy 排名不能直接修改 production。
