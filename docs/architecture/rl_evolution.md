# 可验证的离线 RL 自进化层

> **2026-08-26 v3 基线**：v2 任务轨迹继续作为兼容历史，新的学习证据以 Episode Trace v3 与六维
> Reward Vector 为准。初步自进化仅运行 shadow，不改生产；详见 ADR 0006 与 Sprint 15。

## 1. 目标和边界

让 Partner 根据真实运行结果改变后续策略，而不是根据一段“反思”自行宣称进化。
本层不训练 LLM 权重，不直接修改生产代码，也不替代项目迭代 Controller。

当前生产处于 `manual_stable`。因此 Campaign 数据流是历史/实验路径；当前可用入口是：通过手动任务
产生验收通过的项目 Receipt 和 v2 轨迹，再由用户显式要求 05 运行
`review_manual_evolution_evidence`。该事件只建立 candidate Experiment，不自动选择下一动作或晋升。

## 2. 数据流

```text
Campaign WorkItem + task log + artifact + delivery callback
                         |
                         v
       durable EvidenceManifest + trajectory JSONL v2
                         |
                         v
             verifiable reward components
                         |
                         v
       offline conservative candidate policy
                         |
                         v
       Receipt action -> baseline/candidate canary
                         |
                         v
     policy-selected next event + PromotionDecision
```

轨迹位于 `share/mind/governance/rl/trajectories.jsonl`，奖励规格位于
`reward_spec.json`，候选策略位于 `candidate_policy.json`。

## 3. 轨迹合同

每条 v2 轨迹绑定一个持久 WorkItem，额外记录 `strategy_id/policy_decision/policy_arm`、
`outcome_fingerprint`、`business_progress`、`monitor_only` 与 `policy_eligible`。只有 01–04
产生语义业务增量的 `project_iteration` 才能进入策略；报告、audit、05、no-change 和泛化动作只保留审计轨迹。

## 4. 奖励原则

- `business_progress` 是主奖励（0.45）；新的语义结果与真实承接上轮机器产物是次级奖励。
- completed、产物合同、QQ/PDF 交付和有意义事件各只有 0.05；它们是必要条件而非业务成功。
- blocked 只有在有真实证据和 `resume_event` 时才视为受控成果。
- failed、缺产物、缺交付、retry、timeout/watchdog 分别扣分。
- LLM 自评、Markdown 中的成功宣称和“下一步应该”不计奖励。

这是 RLVR 式的可验证奖励边界，不是通用的语义满意度打分。

## 5. 候选策略与晋升

当前算法是保守离线 contextual bandit。`rl_control.choose_action` 在未晋升时交替选择
baseline/candidate，并把选择写入 `canary_assignments.jsonl` 和 WorkItem marker，真实参与下一事件。
每臂至少 3 个 v2 有效样本；candidate 成功率至少 0.67 且均奖励比 baseline 高 0.15 才 promoted。
控制策略写在 `control_policy.json`；未过门一律 rejected/inconclusive。

### 5.1 Episode Trace v3 与候选生命周期

- raw：TaskInstance、task_log JSONL、step result、channel transcript、Receipt 和原 v2 trajectory。
- reduced：conversation/model/tool/artifact/delivery/failure graph；输出可删后重建，raw 不被覆盖。
- reward：truth、business_progress、handoff、observability、efficiency、safety。truth/safety 任一失败，
  `policy_eligible=false` 且 scalar=0，不允许用产物、速度或完成状态抵消假成功或越权。
- lifecycle：重复 Episode → 失败聚类 → Candidate Skill/Strategy → 历史回放 → shadow → 难度/来源匹配
  canary → 显式 PromotionDecision → 可回滚 production。

Shadow 至少 10 样本/臂，推荐 20。历史 3 样本/臂只证明基础设施能走通，不作为通用学习充分证据。

### 5.2 首个 Preflight 候选的真实状态（2026-08-26）

`candidate_preflight_aware_planning_v1` 已完成 candidate → shadow → bounded canary 的证据流：

- 历史基线反事实回放 10 个 Episode：preflight failure 6→投影 1，semantic repair call 9→投影 2。
- 追加跨来源承接后共有 17 个真实 candidate Episode：completed=4、policy_eligible=4、
  preflight failures=7、semantic repair calls=6、truth passes=10、observability passes=4。失败包括路径合同、
  结果引用、JSON 截断、真值格式、Receipt 承接、状态包装和历史语义误判，均作为负样本保留。
- 最终合格 canary 为 `episode_a844edfc1c673f2b`，reward=1.0；它证明当前实现可走通，不证明对未知任务
  的稳定提升。
- 状态为 `canary`，`production_effective=false`。当前策略 marker 只提供归因，baseline/candidate 尚未
  feature-isolated，因此 evaluator 固定输出 `intervention_isolated=false` 与 promotion blockers。投影、
  顺序调试和三轮承接不得伪装成独立 A/B；隔离执行路径后仍需匹配实跑与用户显式 PromotionDecision。

Shadow evaluator 通过 trajectory 的 `strategy_id + experiment_id + work_item_id` 把真实 candidate
执行与历史 baseline 分开，不能把 candidate 失败重新算进 baseline，也不能只统计最终成功。

## 6. 防止自进化风暴

- 一次 Campaign 最多拥有一个 05 主审计和一个 evolution WorkItem；跨 tick 不得重复物化。
- evolution WorkItem 失败不再生成新的高优先级验收 Issue。
- “验证 Issue 失败”类派生记录不可再成为 evolution 源。
- 自进化实验终态后必须回到源项目，不得取代项目迭代。

## 7. 轮次承接与时序

- 05 的 `offline_rl_self_evolution` 必须等待同一 Campaign 的 01–04 非报告任务全部进入终态，
  防止只学习到先完成的半轮结果。
- 05 执行时建立可读审计、candidate policy 和正式 candidate Experiment；它不会自动晋升。
- Campaign 到达截止或硬预算边界、创建最终报告前，Controller 再执行一次幂等 final sync，
  补入晚完成的业务任务和 05 自身轨迹。报告 WorkItem 始终排除在策略奖励之外。
- `trajectory_id` 与持久 WorkItem 绑定，重复同步不会重复计数；策略文件只代表候选，
production promotion 仍要求独立 canary、测试和 PromotionDecision。

## 9. 2026-08-26 手动证据门实跑

04 已形成 3 个有效、独立 Receipt 和 3 个唯一 outcome，来源族覆盖 `deepseek-harness`、
`openai-codex`、`github` 与 `md`。一个含虚假能力声明的 Receipt 已追加作废，验证错误成功不会进入
有效样本。05 据此创建 `experiment_c5f8bc67f9ac`，状态为 `candidate`、`promotion=false`。

这只证明“真实结果 → 筛选 → 候选实验”的前半闭环。晋升还需 baseline/candidate 每臂至少 3 样本、
candidate 0 false-success、逐源证据全匹配、全回归通过和显式 PromotionDecision。不得把样本数达标、
reward=1.0 或生成审查 Markdown 单独解释为策略已经学习或生产效果已经提升。

## 10. 2026-08-26 受控 Canary 与 Production 真值门

旧实验 `experiment_c5f8bc67f9ac` 因 baseline/candidate 已共享同一严格实现而被明确判为
`inconclusive`。新实验 `experiment_5af99917bea9` 使用可分离干预：candidate 在最终文件阶段重新打开
每个命名输入，验证 `source_path` 存在、`evidence_quote` 至少 20 字且逐字属于对应源；baseline 保持
现有 Harness/Citation 门。

三组成对实跑结果：baseline 3 样本、2 成功、mean reward 0.5167、false-success 1；candidate
3 样本、3 成功、mean reward 1.0、false-success 0，且 3 轮全部逐源核验。reward gain=0.4833。
该阶段独立全量回归为 `307 passed in 9.23s`（当前基线为 327 passed）；实验决策使用已落盘 attestation 后，05 的用户显式事件生成
PromotionDecision=`promoted`。控制策略只把
`literature_github_learning:manual_final_artifact_truth` 映射到 `manual_stable_truth_audit_v2`。

晋升后的普通 04 来源型 Markdown/TXT 任务会以 `policy_arm=production` 记录轨迹并执行相同最终真值门。
生产实跑进一步把确定性命名源抽取前置到报告生成之前，防止 LLM 改写 HTML/Markdown 引文；最终门仍
独立重开来源复核。任务 `45cbe78a-bc36-46a3-9961-02b645baf7d3` 当时以 Receipt
`receipt_680db01279ab` 记录 3/3 来源、reward=1.0；Episode v3 后续发现成品能力声明和真实 `create_file`
矛盾，已追加作废。其 v3 truth=0、policy_eligible=false，说明来源匹配不能替代当前运行事实一致性。
实验失败任务即使没有 Receipt 也必须进入负奖励轨迹；作废 Receipt 必须从评估中排除；回归证明缺失时
不得提前消耗一次性决策。05 决策事件幂等自产 Markdown/JSON，并在完成后停止。

## 11. 运行

05 的 Campaign 默认事件为 `offline_rl_self_evolution`。手工重算指定 Campaign：

```bash
cd /mnt/e/work/partner
PYTHONPATH=/mnt/e/work/partner python scripts/partner_rl_update.py \
  --workspace /mnt/e/work/partner_workspace --campaign-id <campaign_id>
```

命令更新轨迹、candidate policy 并评估已收集的 canary；它不启动实例。真正动作选择由
Receipt continuation 在后续 Campaign tick 中使用。

手动稳定模式下的 Episode/Shadow 重算：

```bash
cd /mnt/e/work/partner
PYTHONPATH=. python scripts/partner_shadow_bootstrap.py \
  --workspace /mnt/e/work/partner_workspace/instances/04 \
  --instance 04 --project-id literature_github_learning \
  --experiment-id experiment_bf3cf4963540 --limit 100
```

该命令只归约和评估，不启动任务、不修改 production control policy。
# Sprint 12 增量：奖励项目进步而非只奖励完成

全局语义指纹用于 novelty，不能靠换路径或时间戳重置。机器 JSON 的 `lineage.consumed=true`
证明承接；二者仍不绕过 Experiment/PromotionDecision，也不训练基础模型权重。
