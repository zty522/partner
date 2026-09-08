# 可验证的离线 RL 自进化层

> **范围纠正（2026-08-29）**：RL 不是自进化的同义词，也不拥有执行入口。它只能根据真实 Event/Episode
> 证据为重复策略选择提出 Candidate；Candidate 仍须经白名单 Event 执行和匹配实验晋升。BO/MaxVar 适合
> 连续科学实验选点，不等于全面取代 context/tool/strategy 的 conservative bandit/offline RL。硬编码
> fixture、`synthetic_fixture=true` 和已知测试前缀不得进入学习或 canary 统计。

> **2026-08-26 v3 基线**：v2 任务轨迹继续作为兼容历史，新的学习证据以 Episode Trace v3 与六维
> Reward Vector 为准。初步自进化仅运行 shadow，不改生产；详见 ADR 0006 与 Sprint 15。

> **2026-09-08 语义边界**：主动学习面向外部知识，自进化面向 Partner 自身；RL 只在存在重复 state、
> 可比较 action 和可信 Reward 时学习选择偏好。当前自进化先由 LLM 做因果诊断，Candidate 通过机器硬门后再由
> 独立 LLM critic 找反例；模型不能给自己 Reward 或批准 production。API 账本记录实际 token usage，调用次数和
> token 本身均不计 Reward。详见 ADR 0072。

## 1. 目标和边界

让 Partner 根据真实运行结果改变后续策略，而不是根据一段“反思”自行宣称进化。
本层不训练 LLM 权重，不直接修改生产代码，也不替代项目迭代 Controller。

当前生产处于 `manual_stable`。因此 Campaign 数据流是历史/实验路径；当前可用入口是：通过手动任务
产生验收通过的项目 Receipt 和 v2 轨迹，再由用户显式要求 05 运行
`review_manual_evolution_evidence`。该事件只建立 candidate Experiment，不自动选择下一动作或晋升。

2026-09-01 用户另行显式授权了 `campaign_f8ace4ef3e44` 作为 04/05 七天取证实验。它不改变普通消息的
`manual_stable` 路径；每个真实终态立即接续下一 matched WorkItem，并在每次 reconcile 后重算 readiness。
只有所有生产门同时通过时，既有 Event-first promotion/activation 才可能执行。

Sprint 17 的首日期采样已经结束；“主动选题、自动修复、跨日期学习、生产晋升”仍是缺口，不得因已有
Candidate 正收益而写成完成。下一实现合同见 `docs/sprint18_主动课程自动修复与受限晋升.md`：核心是将
`learning_eligible` 与 `promotion_eligible` 分离，并用证据驱动 acquisition、RepairRecipe、真实日期 anchor
回放和受限 canary 形成长期状态机。

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

轨迹位于 `share/mind/governance/experience_guided_policy/trajectories.jsonl`，奖励规格位于
`reward_spec.json`，候选策略位于 `candidate_policy.json`。

2026-08-31 的 research-adoption Candidate 首次把这些轨迹作为**只读上下文记忆**使用：按 query、project、
真实 reward/进展/失败机制检索，帮助当前任务看到相关经验。这是 JitRL 式运行时经验复用，不是模型权重
训练，也不等于策略已因 RL 改善。五项目匹配实验只评价 evidence recall；在下游业务 Reward 成为匹配
outcome 前，该 Candidate 不能进入 RL promotion 统计。

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

当前算法是保守离线 contextual bandit。`policy_control.choose_action` 在未晋升时交替选择
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

- 05 的 `offline_policy_learning_self_evolution` 必须等待同一 Campaign 的 01–04 非报告任务全部进入终态，
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

05 的 Campaign 默认事件为 `offline_policy_learning_self_evolution`。手工重算指定 Campaign：

```bash
cd /mnt/e/work/partner
PYTHONPATH=/mnt/e/work/partner python scripts/partner_policy_update.py \
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

## 12. 2026-08-30 首个 sustained business gate

`experiment_6ef2b9be620b` 不再用“平均 reward 上升”单独宣称 RL 有效。04 的三个独立真实来源任务按
match_key 配对，要求 Candidate 对每一对均不回退且至少两对严格改善；结果为 `[+0.9,+0.9,0]`，
Candidate 完成/真值/可观测性 3/3，Baseline 1/3。

这里的 RL 是有证据的 contextual policy learning，不训练 LLM 权重。离线 matched observation 在外部渠道
不可用时可以用本地证据归档进入学习，但 `delivery_confirmed=false`，不获得 delivery reward；普通生产任务
仍必须真实投递。PromotionDecision 只声明证据通过，独立 `activate_promoted_candidate` Event 才同步
control policy、Candidate `production_effective` 和可回滚生产状态。

## 13. 2026-08-30 失败样本进入动作估计

`policy_eligible` 继续表示通过业务进步和硬门、可进入后续 canary 判断；它不能兼任“是否纳入统计”。
新增 `learning_observation_eligible`：只要 01–04 的有界项目动作可归因且不是 audit/no-change，completed、
blocked、failed 都进入同一 action key 的 reward 分布。这样交付失败、验证失败和重试成本不会被成功样本
过滤掉。

Candidate policy 额外记录 `negative_or_zero_samples`，样本数、mean reward、success rate 与 LCB 均包含这些
失败。当前 `01_content_readiness_gate` 在一次历史成功、一次真实失败和一次修复成功后为 3 样本、1 负样本、
success rate 0.6667，仍低于 0.67，保持 `eligible_for_canary=false`。负样本用于阻止幸存者偏差，不会绕过
truth/safety、matched experiment、回归、PromotionDecision 或 activation Event。

## 14. 2026-08-30 manual 负样本的当前盲区

04 Task `d2cb657c-f79d-4146-93d2-cdec40dc084e` 已产生负轨迹
`traj_manual_4d3f78f10ae387af`，但它不能视为“RL 已学会修复”：

- action 被压成 `04:manual_project_iteration:generic`，没有保留 PDF output-reference 机制；
- outcome artifacts 为空，但真实 Markdown 已存在，partial success 丢失；
- failed 轨迹仍含少量 `accepted_completed`/`artifact_contract` 正分，表示与证据不一致；
- Episode truth 未发现报告的跨来源混淆；
- 当前 Campaign action aggregation 不会自然把 generic manual action 合并到已晋升策略的同一估计中。

后续必须先校正 action identity、partial artifact、claim truth 和 manual/Campaign 聚合合同，再允许该类负样本
更新 action value。记录 trajectory 是数据管道成功，不是策略改善证据。详见 ADR 0043。
# Sprint 12 增量：奖励项目进步而非只奖励完成

全局语义指纹用于 novelty，不能靠换路径或时间戳重置。机器 JSON 的 `lineage.consumed=true`
证明承接；二者仍不绕过 Experiment/PromotionDecision，也不训练基础模型权重。

## 2026-08-31 增量：学习进展与业务进展分账

真实源码/论文研究没有用户成品文件，不应获得 business reward；但来源已指纹化、证据已落盘且下一选择受
反馈影响时，也不应被旧的“无 artifact = -0.45”规则当作失败。

- `business_progress`：仍要求业务 artifact、findings、meaningful event 和内容指纹；
- `learning_progress`：要求 research-active-learning Event、受限治理 evidence refs、findings 和新指纹；
- 学习成功可正向更新研究选择观察，但 `policy_eligible=false`，不能进入业务 production promotion；
- `learning_observation_eligible=true` 允许保留正/负研究证据和后验变化；
- 阅读、报告或正 Reward 本身均不等于自进化。只有形成代码/策略 Candidate 并通过真实 matched 项目门，
  才能评价 Partner 行为是否改善。

## 2026-08-31 增量：下游 benchmark Reward 仍不是业务 Reward

ADR 0049 的 `0 → 1` 是冻结认知任务的 evaluator reward，只证明 Candidate 证据能被本地消费者正确使用。
实验必须写 `business_progress=false`、`policy_eligible=false`，不得进入生产 action value。只有真实项目产物、
用户/环境结果和跨轮无回退证据同时成立，才可形成业务 RL 样本；LLM 配对还需合法的数据外发或本地模型。

## 2026-09-01 增量：学习型 Candidate 生产成熟度总门

学习型上下文/策略 Candidate 的 `policy/promoted` 与 production activation 继续分离。激活前必须提交
`learned_candidate_readiness_v1`：两个外部模型家族、六轮跨项目/跨时间真实交付、同一实验每臂 20 个
真实样本、Candidate 成功率 95% Wilson 下界、两臂负样本、零 false-success 和回滚演练。缺任一项时
activation fail closed。当前 research-adoption Candidate 为 blocked，不得写入 control policy。

## 2026-09-01 增量：完成信号与高密度真实取证

- 初始 56 WorkItem 覆盖 04 `literature_github_learning` 与 05 `agent_self_evolution`，每项目 14 组
  baseline/candidate；同实例严格先 baseline 后 Candidate，两个实例最多双槽。
- Task 最终治理通过 named FIFO 发完成信号，终态后立即调度；固定 timeout 只做丢信号恢复。
- 每个新真实日期最多补 3 pair/项目，累计三个日期窗；不伪造日期、不用 sleep 制造样本。
- MiniMax readiness 读取全部 accepted/rejected 结果，不能只挑成功实验。当前单授权通用模型门已通过，
  但 sustained business 与 longitudinal RL 仍 blocked。
- local observation 可训练错误归因、Claim 和策略选择，却没有 delivery reward，也不满足真实业务轮。
- 当前是在做 conservative offline/contextual policy learning 与运行时记忆，不训练 MiniMax 权重；
  “自进化”仍是 Issue→Candidate→matched experiment→PromotionDecision→rollback 的更大闭环。
