# Sprint 18：完整主动学习与可验证自进化闭环

**规格版本**：v2  
**状态**：Native Event + Mechanism-bound Learning Operational / Novelty Gate Implemented / Longitudinal Gates Open  
**日期**：2026-09-02  
**生产基线**：`manual_stable` 不变  
**资源边界（历史）**：Sprint 18 实施期同时最多两个实例；当前生产已由 Sprint 19 / ADR 0070 改为资源自适应 1–5 槽。
MiniMax-M3 为当前授权的外部通用 LLM；不调用未授权付费模型  
**前置里程碑**：Sprint 17 已完成终态信号、双槽队列、真实 matched 实验、Episode/Reward 与 readiness 基础设施

## 0. 本 Sprint 到底要完成什么

> 2026-09-07 推进（ADR 0068）：真实终态→Episode→主动学习→matched Candidate→返回项目的控制链已接通，且原生项目下一步已从通用 LLM 报告规划切换为五实例专属真实 Event。全仓 920/920 通过；03 已产生真实数值实验 Receipt。当前 Sprint 进入生产轮转、跨日期业务增量与策略统计阶段；在纵向门达标前不得写“长期 RL 成熟”。

> 2026-09-07 收口（ADR 0069）：五实例均已产生真实业务 Receipt，严格双槽和具体 Event finding 已在生产长跑验证；空 failure class 的 matched 假阳性已关闭，并完成一组“空机制 rejected → archived trace 恢复具体机制 → v4 candidate validated”的真实实验。跨轮相同语义 outcome 不再获得正 Reward。Sprint 的单轮工程闭环已 operational；剩余是跨真实日期、外部变化与独立 canary 的纵向成熟度，不得通过重复运行相同确定性任务凑样本。

> 同轮追加：`duplicate_outcome` 会形成 `outcome.duplicate_semantic_result` Episode 信号并触发一次受限学习，而不是继续按“成功”刷项目轮次。若 Candidate 仍无新信息，必须保留负/中性证据；不得把学习 Event 自身当作业务改善。

本 Sprint 不以“新增几个主动学习 Event”“生成一份自进化报告”或“一次 Candidate 成功”为完成。目标是让
Partner 在真实项目中反复走通下面的闭环，并让上一轮结果可验证地改变下一轮行为：

```text
真实项目执行
  → Observation / Episode / Issue
  → 主动选择最值得解决的问题
  → 因果诊断与最小修复假设
  → 隔离 Candidate（配置、Event、代码或策略）
  → 匹配 baseline/candidate 实跑
  → 真值、业务、成本、安全 Reward
  → 更新后验与下一动作概率
  → 跨轮、跨项目、跨真实日期继续
  → shadow / bounded canary / promotion 或 rollback
```

“完整”限定为上述工程闭环在本 Sprint 规定的真实项目、样本量、时间窗和安全门下成立；不等于训练出新的
基础模型，不等于 AGI，也不授权 Partner 无限制修改自身。

## 1. 当前真实基线与主要缺口

Sprint 18 从以下事实出发，不改写历史：

- Campaign `campaign_f8ace4ef3e44`：`51 completed / 37 blocked / 0 active`，当前 `blocked`。
- 连续实验 baseline 22、Candidate 25；均值分别为 `-0.4 / 0.56`。
- Candidate 有 20 个正样本、5 个负样本，Wilson 95% 下界约 `0.609 < 0.67`，false-success=5。
- 已覆盖 `literature_github_learning` 和 `agent_self_evolution`，但只有 `2026-09-01` 一个真实日期窗口。
- sustained-business 只有 3 个合格轮次、单项目；渠道 ACK 与业务承接证据不足。
- `learning_observation_eligible=0`：现有资格合同没有正确区分“可用于学习”和“可用于生产晋升”。
- 现有任务主要由预置题库生成；它们证明执行器可用，不证明系统会根据后验主动选择下一题。
- Candidate 修复仍大量由外部开发 Agent 完成；Partner 尚不能稳定完成“诊断→最小改动→匹配重测”。

## 2. 不可回退原则

1. **Event-first**：选择、诊断、修复、实验、更新、canary、晋升和回滚均须有正式 Event 与持久账本。
2. **项目主线优先**：学习闭环必须返回真实项目，反思、报告和 05 审计不能替代业务推进。
3. **真值优先**：文件存在、消息发送、LLM 自评、测试数量和报告漂亮均不能抵消 Claim/safety 失败。
4. **失败保留**：rejected、timeout、false-success、无增益和回滚样本不得删除或重写为成功。
5. **最小干预**：优先配置/参数/确定性后处理，再到单 Event handler；架构大改默认需要人工批准。
6. **生产隔离**：Candidate 只能在隔离执行路径生效；未晋升不得进入普通 `manual_stable` 消息路径。
7. **不能自批自改**：提出 Candidate 的角色不能单独批准 promotion；评估器使用独立、冻结的硬门。
8. **有意义地持续**：有新证据就立即继续；信息增益耗尽时等待事件，不用重复任务制造“在线时长”。

## 3. 长期运行状态机

```text
OBSERVE
  │  terminal / new evidence / user feedback / new real date
  ▼
SELECT ── no useful target ──→ WAITING_EVIDENCE
  ▼                              │ wake signal
DIAGNOSE                         └──────────────┐
  ▼                                             │
PROPOSE_CANDIDATE                                │
  ▼                                             │
SANDBOX_EXECUTE                                 │
  ▼                                             │
MATCHED_EVALUATE                                │
  ▼                                             │
UPDATE_POSTERIOR ──→ NEXT_PROJECT / NEXT_TARGET ┘
  │
  └─ gates satisfied → SHADOW → CANARY → PROMOTE / ROLLBACK
```

调度仍由 Task 权威终态信号驱动。30 秒仅作丢信号/进程恢复 watchdog，不是任务周期。新日期、外部资料指纹、
Project Receipt、用户反馈和代码变更指纹均需有 durable wake event；进程重启后可从状态恢复。

## 4. 数据合同：先让系统知道什么可以学

### 4.1 `LearningObservation`

每条观察至少包含：

```text
observation_id, episode_id, project_id, instance_id, temporal_window
state_features, action_key, strategy_id, outcome, reward_vector
failure_class, mechanism, evidence_refs, artifact_refs
truth_passed, safety_passed, delivery_confirmed, business_progress
learning_eligible, promotion_eligible, novelty_digest, created_at
```

资格必须拆开：

- `learning_eligible=true`：truth 与 safety 通过，原始 Episode/来源/产物可复核；允许
  `delivery_confirmed=false`，因为渠道故障不应阻止系统学习内容机制。
- `promotion_eligible=true`：除上述条件外，还要求真实业务增量、跨轮承接、完整交付和非 monitor-only；
  只有该通道能支持生产晋升。
- false-success、truth/safety 失败仍进入负学习样本，但永远 `promotion_eligible=false`。
- 测试 fixture、synthetic、报告任务和重复 outcome 不进入真实策略效果统计。

### 4.2 五类持久决策

| 账本 | 必要字段 | 用途 |
|---|---|---|
| `TopicSelectionDecision` | 候选全集、分数分解、选择目标、证据 | 证明为何选这题 |
| `DiagnosisRecord` | 因果切片、owner、mechanism、置信度、反证 | 防止只复述错误文本 |
| `CandidateBundle` | 假设、diff/config、测试、预算、rollback | 可执行且可回退的最小干预 |
| `MatchedExperiment` | 冻结任务/模型/来源/预算、双臂结果 | 证明改善归因于 Candidate |
| `PolicyUpdateDecision` | prior、observations、posterior、选择概率 | 证明 Reward 真改变下一轮 |

所有账本 append-only；修正使用新版本或 correction event，不覆盖旧事实。

## 5. 主动选题：从固定题库升级为主动课程

### 5.1 候选问题来源

- 未解决 Issue 与重复失败机制；
- Project Receipt 中尚未执行的高价值 NextAction；
- Claim Ledger 的 `not_found/inference` 知识缺口；
- 高不确定或高成本的 Event/策略；
- 新论文、GitHub revision、数据集或代码指纹；
- anchor 任务中的回归与遗忘；
- 用户明确的负反馈和成功反馈。

### 5.2 Acquisition score

```text
score = expected_information_gain
        × uncertainty
        × business_value
        × transfer_value
        × novelty
        - execution_cost
        - safety_risk
        - repetition_penalty
```

每一项须由持久事实计算，不允许 LLM 只给总分。Selector 输出 Top-K 及未选原因；critic 攻击最高分目标，
检查是否过难、过易、不可证伪、重复或没有真实执行入口。最终以 `learning/topic_selected` Event 落账。

### 5.3 课程难度与持续运行

- 目标成功率过高：提高组合复杂度、跨源约束或迁移项目难度。
- 连续失败且无新证据：缩小动作、补诊断或换高信息 probe，不机械重试。
- 同日仍有高分目标：完成即续跑。
- 全部目标低于信息增益阈值：进入 `WAITING_EVIDENCE`，等待真实新证据/日期，不空转。

## 6. 自动修复：从 Issue 到最小可执行 Candidate

### 6.1 修复等级

| 等级 | 修改面 | 自动权限 |
|---|---|---|
| R0 | 参数、timeout、选择权重、上下文预算 | 可在 shadow 自动生成与验证 |
| R1 | prompt、Event 参数合同、确定性后处理 | 可在隔离 Candidate 自动修改 |
| R2 | 单个 allowlisted handler/模块和对应测试 | 可生成补丁；通过代码门后进入 shadow |
| R3 | 跨模块协议、存储 schema、调度架构 | 只生成设计与 patch proposal，默认需人工批准 |
| R4 | 权限、安全、凭证、发布、支付、不可逆数据 | 禁止自动修改 |

### 6.2 `RepairRecipe`

注册表以 `failure_class + mechanism + domain` 为键，包含允许修改范围、前置 probe、候选模板、针对性测试、
全量回归、业务验收、最大尝试数、成本上限和 rollback。未知机制先诊断，不允许套通用“重试/加 timeout”。

### 6.3 自动修复硬门

1. 从 plan DAG、参数引用、工具日志和最终治理结果提取最小因果切片；
2. 写可证伪假设和“不改变什么”；
3. 在隔离目录/分支生成 Candidate；生产状态和 `control_policy.json` 不变；
4. 运行静态检查、针对性测试、全量回归、恶意/负向测试；
5. 相同输入、模型、来源、预算和真值门运行 baseline/candidate；
6. 失败标 `rejected/inconclusive`，禁止同 fingerprint 无变化重试；
7. 成功后返回原项目继续下一业务动作，不停在自进化报告。

## 7. Reward、后验与策略更新

本 Sprint 使用保守 contextual bandit/offline RL，不训练基础模型权重。状态至少包含项目、领域、失败机制、
来源类型、预算、上下文压力、历史成功率和渠道状态；动作是 `topic/repair_recipe/strategy/event_variant`。

每次 UPDATE：

- 更新 `failure_class × repair_recipe × domain` 的 Beta 后验、收益分布、成本和风险；
- truth/safety 失败强制为负，不能由速度、文件或消息抵消；
- 学习、业务、交付 Reward 与生产晋升资格分别保存；
- baseline 保留最低探索概率，避免 Candidate 偶然成功造成错误收敛；
- policy update 后运行“相同状态下选择概率已变化”的反事实断言；
- 决策可由 Observation 集合和固定算法复算，LLM 解释不是策略权威。

## 8. 三个真实项目验收场

本 Sprint 不使用纯函数拟合或合成 fixture 作为最终证明。单元测试可以合成，完成证明必须来自以下项目。

### 项目 A：Partner Harness 真实缺陷自动修复（主项目）

从 held-out 真实 Episode 主动选出一个尚未编码修复的高价值机制，自动完成诊断、最小 R0–R2 Candidate、
测试和 matched canary。

- 至少 12 个 held-out Episode、3 种 failure mechanism；不可把答案写进 selector；
- 至少两个 Candidate 被诚实拒绝或证明不优，避免只展示成功样本；
- 最终 Candidate 显著降低目标失败且全仓零回归；
- 真实手动消息验收“收到→步骤→结果→停止”，不能只跑 pytest；
- Candidate 返回源项目完成下一动作，证明自进化没有吞掉业务主线。

### 项目 B：成熟 Harness 外部证据到 Partner 实际采用（迁移项目）

04 从 Codex、DeepSeek Harness、Hermes、OpenClaw 的固定 revision 与论文中主动选择最高价值缺口，05/03
将证据转成一个真正进入 Partner 隔离执行路径的最小采用 Candidate。

- 必须有 source SHA/revision、逐字证据、采用/不采用边界、代码或配置 Candidate，不是总结报告；
- 至少比较三个机制，selector 选择须胜过固定顺序/随机基线；
- 在项目承接、失败恢复、长上下文/证据三类真实下游任务 matched 验证；
- copied-source=false 或完整 third-party attribution；不替换 Partner Event-first 根基；
- 改善体现在真实任务 Reward/成本/真值，不是“检索到更多上下文”。

### 项目 C：TargetDiff/分子项目主动实验选择（跨领域压力测试）

02 在来源明确、split 可复现的真实分子任务上选择下一数据切片、误差区或实验，不再以纯 RMSE 函数选择
代替主动学习。

前置硬门：官方或可审计替代 split、真实特征/标签、固定 holdout、可复现实验入口全部存在；任何一项缺失，
项目 C 标 blocked，Sprint 不得用合成点或报告替代。

- 相同实验预算比较 active selector、random 和 MaxVar/现有启发式；
- 指标含 holdout 改善、信息增益、校准/不确定性、成本和失败切片覆盖；
- 至少三轮“选择→真实运行→观察→再选择”，后轮必须受前轮结果影响；
- 不把统计预测写成药效因果，不以单次 RMSE 波动宣称科学改进。

项目 A、B 是强制主项目；项目 C 是强制跨领域压力测试。若 C 真实数据前置条件阻塞，Sprint 保持未完成，
不降低验收标准。

## 9. 匹配实验与统计合同

每个 pair 冻结任务、初始 ProjectState、来源 revision、模型、公共 prompt、token/时间预算、工具权限、真值门、
交付要求和随机种子，只有目标干预不同。

- 每臂至少 20 个真实、去重、非 synthetic 样本；
- 至少两个项目、三个真实日期窗口；
- Candidate 平均 Reward 比 baseline 高 ≥0.15；
- Candidate 成功率 Wilson 95% 下界 ≥0.67；
- Candidate false-success=0；
- 双臂均保留负样本；
- 成本/延迟无未解释的严重退化；
- anchor 回放无关键能力回退；
- 至少一次真实 rollback drill。

## 10. 跨日期学习与抗遗忘

Controller 监听真实 `new_date`，不伪造时间。每个窗口包含固定 anchor、matched pairs、上一窗口后验选择的
adaptive tasks、跨项目 transfer task 和 drift report。Observation 按新颖性、复用价值、置信度、最近验证时间
和反例进行巩固/衰减；只删除可重建缓存，原始 Episode、失败和 PromotionDecision 不可删除。

## 11. 生产晋升与回滚

```text
policy/candidate_proposed
→ policy/shadow_evaluated
→ policy/canary_activated
→ policy/canary_observed
→ policy/promoted | policy/canary_rolled_back
```

Canary 限定 Candidate identity、实例、项目、意图、来源、任务数、预算、有效期、自动停止条件和原策略 digest。
truth/safety failure、连续失败、成本越界或身份不匹配立即回滚。Promotion 只修改明确策略映射，不开启无界
Campaign；晋升后保留 baseline 探针和自动退化检测。

## 12. Sprint 看板与实施顺序

| 阶段 | 交付 | 当前状态 | 硬验收 |
|---|---|---|---|
| S18-A | 双资格 Observation Ledger | Implemented | 最新 689 observations；547 learning / 94 promotion eligible / 12 neutral；修正 append-only |
| S18-B | 主动课程 selector + critic | Implemented | 分数可复算；失败优先；重复受罚 |
| S18-C | Diagnosis + RepairRecipe | Implemented | claim/reference/delivery/timeout/terminal/uncertainty-miscalibration + unknown probe |
| S18-D | 隔离 Candidate builder | Implemented | CandidateBundle 可回滚；production_effective=false |
| S18-E | matched evaluator + policy update | Partial / Evidence-backed | 第二日期 4 pair 为 0/4→4/4；revision 去重后 26/29 samples；长期统计仍不足 |
| S18-F | 项目 A：Harness 自动修复 | Partial | typed output reference 4/4 gates matched 通过；尚缺 12 held-out/3 mechanisms/真实手动消息 |
| S18-G | 项目 B：外部机制实际采用 | Partial | 04 context v3 绑定有效 Receipt、DeepSeek/Hermes 固定 digest 与 3 条轨迹；尚缺下游业务 matched 效果 |
| S18-H | 项目 C：分子主动实验 | Diagnosis + shadow Candidate | 两轮 acquisition rejected；raw uncertainty 被诊断失准；cross-fitted estimator 2/3，仅接纳 acquisition shadow |
| S18-I | 三真实日期 + 抗遗忘 | In Progress (2/3 dates) | systemd 跨重启恢复；第二日期自动续跑；尚缺第三窗口与 anchor |
| S18-J | bounded production canary | Pending | 统计门全过、真实回滚、PromotionDecision |
| S18-K | 文档与交接 | Current | ADR 0055–0058、status/README/sprint/test baseline 已同步；全仓 726 passed |

顺序为 A→B→C→D→E→F→G→H→I→J→K；准备工作可并行，但运行实例不超过两个。失败必须先定位机制，
不得绕过硬门推进。

## 13. 测试矩阵

1. 单元合同：schema、资格、acquisition、后验、去重、幂等、rollback。
2. 性质测试：真值失败永不晋升、重复证据不增益、成本增加降低选择分、历史不可改写。
3. 故障注入：模型 timeout、QQ ACK 缺失、进程重启、FIFO 丢信号、文件变化、Candidate 崩溃。
4. 隔离测试：baseline/Candidate 路径、上下文、配置、产物不串线。
5. 真实 E2E：项目 A/B/C 的业务入口、文件、消息、Receipt、Episode、Reward 和下一选择。
6. 长期测试：三个真实日期、重启恢复、anchor 回放、策略退化、canary rollback。
7. 全仓回归：每个代码 Candidate 均跑针对性测试和完整 pytest；测试不替代真实项目验收。

## 14. 资源、停止边界与文档纪律

- 最多双槽，同实例严格串行；每个 Candidate 有模型、token、墙钟、工具和重试预算。
- timeout 后不并发启动相同请求；登录、付费、发布、凭证、不可逆操作继续等待用户授权。
- 用户暂停、截止、预算、安全或 readiness 失败均形成终态和恢复条件。
- 每阶段同步 `current_status`、Sprint 看板、change log、evolution journal、架构/Event/代码索引、ADR、测试
  基线和真实项目证据；rejected/inconclusive/rollback 同样记录。

## 15. 2026-09-01 实跑收口

- Campaign `campaign_74833fad4af8` 已完成当前有限信息课程：`30` 个 WorkItem 全部终态，
  `23 completed / 7 blocked / 0 active`；专用持久控制器在线，Campaign 状态为 `WAITING_EVIDENCE`。等待不是失败，也不能靠
  重复旧题伪造持续运行；新真实日期、用户反馈、代码/资料指纹或 Project Receipt 会形成下一轮 wake evidence。
- TargetDiff uncertainty reliability 诊断冻结 official split、三 seed、预算 240 和预测模型；test label 只用于
  后验评分。raw tree variance 的平均 Spearman 为 `-0.0625`、top-error recall 为 `0.2381`，预注册三门全败。
- 该负结果投影为 `uncertainty_miscalibration` LearningObservation，并匹配
  `recipe_uncertainty_estimator_comparison_v1`。cross-fitted residual estimator 在同预算对照中赢 `2/3`，
  Spearman `-0.1306 → -0.0181`、top-error recall `0.2381 → 0.3333`；只允许进入下一轮 acquisition shadow，
  不得写成最终采样策略改善或 production promotion。
- 04 context Candidate v3 在 9,000 字预算内绑定有效 Receipt `receipt_4fd68f29fc41`、DeepSeek/Hermes 两份
  当前 digest 源证据和三条同项目轨迹。它证明上下文组合可执行，不证明下游业务已经持续改善。
- 实跑修复四项归因缺陷：共享学习产物未计入 WorkItem、Campaign report 被当业务 Reward、隔离学习错误要求
  QQ delivery、Campaign 外层把学习结果写成 Project Receipt 并触发 continuation。旧事实不删除，均以
  trajectory revision、Receipt invalidation 或 WorkItem correction 追加修正。
- 修复前被错误 continuation 触发的 02/04 任务确有真实产物和送达，因此保留为业务 Receipt；但它们不能作为
  “学习策略正确触发业务改善”的因果证据。生产 `manual_stable` 与 `control_policy.json` 均未改变。
- 长期启动后又修复 report monitor 的双终态归因与 failure-budget 污染，并按轨迹显式 eligibility 纠正 75 条
  历史 observation；最新为 678 total、539 learning、94 promotion、9 neutral。
- 全仓回归为 `720 passed, 2 warnings in 151.96s`。本轮证明同日、隔离、Event-first 的主动选择→诊断→
  Candidate→matched→后验更新可以运行；未证明三日期长期 RL、生产自进化或持续业务改善。

### 15.1 2026-09-02 第二真实日期

- systemd 服务在挂载 I/O 故障和 WSL 重启后自动恢复，并由真实本地日期幂等生成第二窗口；04/05 共执行
  4 个严格 matched pair，baseline 0/4、Candidate 4/4，累计模型调用 17。
- matched 结果只进入实验和学习账本，不写 Project Receipt、不触发业务 continuation，也不凭本地产物获得
  delivery credit。一次修复前误建但未执行的 continuation 已明确 cancelled。
- readiness 读取 append-only trajectory 时按 ID 只取最新 revision。真实累计为 baseline 26、Candidate 29，
  Reward `-0.4 / 0.5931`、Wilson 0.6545、Candidate false-success=5、日期 2/3，故仍 blocked。
- sustained-business 独立门仍只有 3 个交付轮次、单项目、单日期；学习改善与用户可见业务交付没有混算。
- 最新 Observation 689 total / 547 learning / 94 promotion / 12 neutral；全仓回归 726 passed。

> 后续校正：跨日期数据和恢复实验均保留，但 ADR 0059 已停止该 Campaign 常驻服务。后续长期运行不再以
> 日期/周期作为认知触发器，而迁移到项目执行中的实例原生元认知中断；详见 ADR 0059。

## 16. Sprint 完成定义

Sprint 18 只有同时满足以下条件才可标 Completed：

1. A–K 全部完成，或明确的非降级等价实现通过 ADR；
2. 项目 A、B 端到端成功，项目 C 完成真实跨领域三轮压力测试；
3. 至少两个项目各完成一次“失败→主动选择→自动修复→matched 改善→下一选择变化”；
4. baseline/candidate、三日期、Wilson、false-success、负样本和 rollback 达到第 9 节硬门；
5. bounded canary 真实执行并留下 promotion 或 rollback 决策；
6. production 可恢复到 `manual_stable`，无未授权能力扩张；
7. 用户能从消息、产物和文档看懂选了什么、为什么、改了什么、是否真的更好。

单次成功、模块存在、测试全绿、报告生成、Candidate 落盘或策略 JSON 更新，都不能单独满足本定义。

## 17. 2026-09-02 非周期生产运行增量（ADR 0060）

旧 Campaign 常驻层已被实例原生运行时替换。该变更作为 S18-I/J 前的运行基座完成：五项目、最多双槽、项目终态
即时续跑；失败或知识缺口先归约 Episode，再执行一次 observe/select/diagnose/repair proposal，验证后回项目。
04 已有多条真实成功学习恢复证据；05 完成项目后让槽，01/02/03 均完成失败→学习→项目恢复，五实例调度覆盖
已经验收。防无限学习占槽与控制器重启不打断在途槽位也已落为合同；完整回归 746 passed。S18-A–D 的学习部件由此接入真实项目运行，但 S18-E–J
原有长期统计、业务改善、跨日期、false-success、抗遗忘和 promotion/rollback 门不降低，Sprint 仍不得标 Completed。
