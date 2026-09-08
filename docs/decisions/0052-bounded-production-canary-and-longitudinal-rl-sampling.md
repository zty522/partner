# ADR 0052：受限生产 Canary 与真实日期长期 RL 采样

**状态**：Accepted；受限 canary 已激活，完整 production promotion 仍 blocked  
**日期**：2026-09-01  
**模型边界**：MiniMax-M3 only；没有 DeepSeek fallback  

## 背景

ADR 0051 已证明 Research-Adoption Candidate 在 MiniMax-M3 的 6 个脱敏 matched pair 与一个严格 04
业务对照中优于 baseline，但只有单项目、单日期和少量样本。直接写入 `control_policy.promoted` 会把短期证据
冒充长期成熟；完全不接真实流量又无法形成持续业务证据。因此需要在 shadow 与 full promotion 之间增加一层
可逆、限域、自动回退的 production canary。

## 决策

1. `control_policy.promoted` 的完整生产门保持不变。新增独立 `control_policy.canaries` 投影；canary 路由有效
   不等于 Candidate `production_effective=true`，也不生成 `policy/promoted` / `policy/activated` Event。
2. Canary 只有在已签名 readiness attestation 的 `general_llm` 与 Event ledger 通过后才能激活；持续业务和
   长期 RL 可以尚未通过，因为 canary 的目的正是收集这两类真实证据。
3. 当前 `canary_65f4a91260f915a6` 仅覆盖 04/05、研究/文献/GitHub/源码/证据类请求，且普通请求必须提供
   至少两个当前存在的来源文件。其他实例、其他意图、缺来源请求全部走原生产路径。
4. Canary 最多接受 12 个任务、有效期 7 天。一次 claim truth failure、一次 false-success、连续两次失败或
   样本预算耗尽会自动执行 `policy/canary_rolled_back`；历史轨迹不删除、不改写。
5. 普通生产 canary 请求被确定性编译为
   `execute_candidate(mode=canary) → generate_text → create_file → unified delivery`。Candidate 仍只能调用
   allow-listed Event，Planner LLM 调用为 0。
6. 长期 matched 采样独立于生产 canary：04 `literature_github_learning` 与 05 `agent_self_evolution` 每个
   真实日期最多各调度两个 baseline/candidate pair，最多双槽。采样器只写普通 USER_MESSAGE；不写 reward、
   trajectory、Receipt、PromotionDecision 或伪造日期，最终资格完全由现有 Harness/Claim/交付/Reward 门决定。
7. 每次采样 tick 都重新计算签名 readiness。只有四类完整硬门全部通过，且已有本次用户的显式生产授权时，
   supervisor 才能依次生成 `experiment/completed → policy/promoted → policy/activated`，随后关闭 canary；
   任一门未过时只写 `blocked` attestation。当前首次自动复审为 `activated=false`。

## 真实演练与首批运行

- 对真实控制投影完成 `activate → resolve → rollback` 演练，路由在回退前命中、回退后不命中；证据：
  `share/mind/governance/experience_guided_policy/rollback_drills/candidate_research_downstream_a420574f08ad.json`。
- 重新计算 readiness 后，`rollback_drill_passed=true`；完整 readiness 仍因 20/arm、两项目、三真实日期窗和
  Wilson 下界不足而 `blocked`。
- 04/05 已在真实网络环境连接 QQ 与 MiniMax。2026-09-01 的首个长期窗口登记 5 个 baseline、5 个
  candidate Task，覆盖两个真实项目；登记不等于通过。
- 04 的首个 v4 Candidate 通过双源与 10-Claim 真值门，成为 `completed` observation。
- 05 的两个早期 Candidate 由旧进程把输入论文尾部 `Updates.pdf` 误判为输出，均诚实记录 `reward=-0.4`、
  `false_success=true`。没有改写负样本；检查器现同时读取 reduced root goal 与 durable Task USER_MESSAGE，
  修复已通过回归并通过重启加载。
- Windows/DrvFS 上出现“记录 PID 已不存在但旧 inode 仍被锁”的启动故障。`InstanceLock` 现在只在确认精确
  owner PID 不存在时归档 stale inode 并重试；活 PID 或不可判定状态继续 fail closed。

## “接入生产”与“长期成熟”的准确边界

现在已经接入的是**受限生产 canary**，不是 full promotion：符合范围的真实 04/05 请求会使用 Candidate，
可自动回退；`candidate_projection_production_effective=false`。完整生产晋升仍只能在以下证据真实形成后发生：

- 至少 6 个真实送达、唯一产物业务轮，覆盖至少 2 个项目和 3 个真实日期窗；
- 同一可比实验至少 baseline/candidate 各 20 个真实样本；
- Candidate reward 增益、Wilson 95% 下界、零 false-success、双臂负样本等门全部通过；
- Event ledger 与 rollback drill 继续有效，并产生显式 PromotionDecision/Policy Event。

这条边界禁止把单日 10 个已调度 Task、测试通过数或后台进程存活误写成“长期 RL 已成熟”。

## 验证与运行

- 完整回归：`676 passed in 156.41s`。
- 受限 canary：`canary_65f4a91260f915a6`，截止 2026-09-08，最多 12 个 accepted tasks。
- 长期采样状态：
  `share/mind/governance/experience_guided_policy/longitudinal_sampling/candidate_research_downstream_a420574f08ad.json`。
- 运行器：`scripts/run_longitudinal_policy_sampling.py`；默认每 6 小时检查一次、每个真实日期只调度一次，
  连续运行 7 天。

## 回滚

调用 `rollback_production_canary(..., expected_canary_id=...)`，或由自动阈值触发。回滚只关闭 canary route，
不删除 Candidate、实验、Task、Episode、trajectory、产物或负样本；`promoted` 路径始终未被本 ADR 修改。
