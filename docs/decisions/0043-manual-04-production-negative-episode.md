# ADR 0043：04 手动生产负样本与主动学习/自进化缺口审计

- 日期：2026-08-30
- 状态：已审计，待修复；本 ADR 不修改运行代码、不启动 Campaign
- 实例：04
- 项目：`literature_github_learning`
- Task：`d2cb657c-f79d-4146-93d2-cdec40dc084e`
- Episode：`episode_4d3f78f10ae387af`
- Issue：`issue_038f7feaeffb`
- Trajectory：`traj_manual_4d3f78f10ae387af`
- 前置决策：ADR 0032、0037–0042

## 1. 背景和这次实验要回答的问题

Partner 当前生产默认仍是 `manual_stable`：用户消息触发一次有界任务，实例必须复述任务、逐步说明真实
操作和发现、交付合格产物、给出诚实总结，然后停止。Campaign、05 离线学习和长期自动循环是单独授权的
实验路径，不能替代手动路径。

此前已经完成的关键基础包括：

1. **Event-first**：Event 是执行和事实入口；Candidate、Skill、Prompt、配置和代码只是待验证制品。
2. **Episode v3 / Reward Vector**：能够保留对话、模型、工具、产物、交付和失败证据。
3. **双层主动学习**：对象层选择下一观察；Agent 元层从真实 Episode 选择诊断、repair 或 resample。
4. **限域策略学习**：`planning.semantic_preflight` 在三组 04 matched 文件证据任务中 Candidate 3/3、
   Baseline 1/3，随后经 PromotionDecision 和 activation Event 限域进入 04 生产。
5. **长期 Campaign 实验**：五 lane、最多双槽、学习波次和失败样本聚合曾在受控 Campaign 中运行；该
   Campaign 后来由用户显式停止，当前不得写成仍在运行。

本次用户手动要求 04 真实读取四份 Harness 文档，按五个维度比较，逐步发送有语义的进展消息，并生成
详细 Markdown 和 PDF。本次任务用于检查：晋升后的 preflight 是否在普通手动生产任务中保持真实有效，
以及失败是否会触发预设的主动学习和自进化闭环。

## 2. 本次运行的真实结果

### 2.1 已完成

- 用户消息经 04 的手动路径进入 `batch_plan`，不是 Campaign WorkItem。
- 生产策略确实生效：`strategy_id=candidate_preflight_contract_v2`、
  `policy_decision=literature_github_learning:planning.semantic_preflight`、`policy_arm=production`。
- Planner 创建 8 步 DAG；四个 `atomic_inspect_file` 并行成功，四个命名输入均被读取。
- `extract`、`generate_text` 和 `create_file` 成功。
- 生成了 12,914 字节的
  `instances/04/state/tasks/d2cb657c-f79d-4146-93d2-cdec40dc084e/harness_comparison.md`。
- 最终产物硬门发现 Markdown、确认 PDF 缺失，将任务诚实标为 `failed`；外层没有把失败伪装成成功。
- Issue、Episode 和负 reward trajectory 已持久化。

### 2.2 未完成

- 第 8 步 `generate_detailed_pdf` 使用相对 `source_path=harness_comparison.md`，没有解析为第 7 步刚生成
  的任务目录文件，也没有直接消费 `$step7` 或 `$step6` 内容；返回
  `no content (provide content or source_path)`。
- 同一组确定性错误参数原样重试三次，只增加 2/4/8 秒等待，没有改变路径、输入或执行策略。
- 没有生成 PDF；只交付了 Markdown；没有完成用户要求的最终结论、证据、限制和明确下一步总结。
- 系统向用户说“等待用户提供正确文件路径”，但四个用户输入路径均正确。错误来自 Partner 内部生成产物
  的引用合同，不能归责用户。

因此本轮整体结论是 **failed with partial artifact**，不是 completed，也不是“报告任务完全失败”。

## 3. 四类问题

### 3.1 执行和产物引用合同

根因位于 Planner/PlanExecutor/Event 之间的生成产物引用：一个上游 `create_file` 成功并返回绝对路径，
下游 PDF Event 却收到未解析的相对路径。当前 preflight 重视用户输入源，却没有覆盖“本任务中新生成的
文件如何传给下游 Event”。

需要的合同不是把任意相对路径都猜成任务目录，而是显式支持并验证：

- `$stepN.result.path` 或 `$stepN.files[0]` 的 typed output reference；
- 相对输出路径只能在 TaskInstance working directory 内安全解析；
- PDF Event 执行前检查 resolved source 存在、属于当前任务且非空；
- deterministic retry 必须修改可疑参数或停止，禁止原样重试。

### 3.2 语义证据质量

preflight 确保了四个 `source_path` 存在，`evidence_quote` 也能逐字属于来源，但自动抽出的引文大多不支持
报告结论：DeepSeek 是语言切换链接、Codex 是隐私标题、Hermes 是 banner HTML、OpenClaw 是 frontmatter
标题。这说明当前门主要验证 **literal membership**，没有验证 **semantic relevance / claim entailment**。

报告还混淆来源并过度推断。例如把 DeepSeek 文档中的 `core/session`、`session/event`、append-only 语义
写成 OpenClaw 机制；把 Hermes 的 cron 直接推断成“周期性自愈 + 任务补跑”；把隔离子代理推断成失败不会
影响主会话。这些结论没有被所列来源的逐项证据充分支持。

因此 `truth=1` 不能只依赖“外层没有 false-success”或“引文属于某个文件”。每个重要 claim 都需要绑定
正确来源和支持它的语义片段；无法证明时必须标为 inference / proposed / not_found。

### 3.3 用户可观测性和报告质量

本次有很多进度消息，但多数是“`N/8 [内部操作]`”“读取了多少字节”或截断 JSON。它们证明事件发生，
却没有满足用户要求的“实际读取了什么、发现了什么”。这是 **表面可观测性**，不是有意义的工作沟通。

Markdown 本身也有卫生问题：开头残留 Markdown 代码围栏，结尾带模型元话语，且自述文件名与实际文件名
不一致。报告生成与用户消息应复用结构化的 step finding，但不能把原始机器 JSON 直接发给用户。

### 3.4 Episode、Issue、Reward 和策略学习归因

- Issue 只有通用 `category=verification` 和 task ID，没有记录 PDF output-reference contract 根因。
- Episode failure class 只有 `tool.generate_detailed_pdf.failed`，没有形成
  `planning.output_reference_contract` 或等价机制级标签。
- Episode 的 `truth=1` 未发现报告的跨来源混淆和语义过度声明。
- trajectory 将 action 压成 `04:manual_project_iteration:generic`，产物为 `[]`，尽管 Markdown 真实存在并
  已发送；这丢失了 partial success 和具体 action identity。
- 失败 trajectory 的 reward 为 `-0.45`，但 components 仍含 `accepted_completed=0.05`、
  `artifact_contract=0.05`，与 failed、artifact list empty 的表示不一致。
- 当前 Campaign policy 主要聚合 `project_iteration`；本次 `manual_project_iteration:generic` 虽被记录，
  但不会自然进入同一策略 action 的学习统计。

## 4. 与预设主动学习、自进化和 RL 的差距

### 4.1 主动学习

预设：观察失败 → 维护竞争根因假设 → 选择信息增益最高的诊断 → 获取新证据 → 更新假设 → 选择
repair/resample。

本次：观察 PDF 失败 → 原样重试三次 → 建立通用 verification Issue → 停止。

因此主动学习基础设施存在，但没有被自动接到普通手动失败的终态上。当前达成的是“证据可记录、过去有过
显式诊断实验”，不是“每次真实失败都能自主选择下一诊断”。

### 4.2 自进化

预设：Issue → 机制级诊断 → 最小 Candidate → Event-first 隔离执行 → matched baseline/candidate →
真实业务和安全评价 → promoted/rejected/inconclusive → 回到原任务。

本次停在 Issue/Episode/negative trajectory，没有 Candidate、fresh canary、PromotionDecision，也没有用
修复后的路径重新完成原任务。记录一次失败不等于发生自进化。

### 4.3 RL / contextual bandit

RL 不是自进化本身。Partner 当前只在重复、可比较、奖励可信的策略动作上做保守 offline contextual
bandit。本次有负 reward，但 action key 泛化、产物表示错误、truth 有盲区，而且 manual 轨迹没有进入目标
policy aggregation；所以不能说 RL 已从本次错误学会了更好的下一动作。

### 4.4 当前成熟度

| 能力 | 当前程度 |
|---|---|
| Event-first、执行留痕、Episode、产物硬门 | 已形成，可用 |
| 04 文件源 preflight 的限域 matched 改善 | 曾通过并已激活，但本次暴露域外/链后半段缺口 |
| 文字级 source/quote 真实性 | 部分完成 |
| claim 级语义支持和跨来源防混淆 | 未完成 |
| 普通手动失败自动进入机制诊断 | 未完成 |
| 自动生成 repair Candidate 并隔离验证 | 仅个别脚本化实验完成，未通用接线 |
| 失败修复后恢复原用户任务 | 未完成 |
| RL 从本次 manual 负样本更新正确 action value | 未完成 |
| 五实例长期自治持续变好 | 未证明；当前 Campaign 已停止 |

## 5. 后续修复优先级和验收门

### P0：恢复本任务的执行真实性

1. 修复生成产物 typed reference 和任务目录安全解析。
2. 对同错误、同参数的 deterministic retry 进行短路；只有参数或策略发生实质变化才可重试。
3. 错误归因必须区分 user input、planner contract、event handler、environment 和 delivery；禁止将内部错误
   写成“等待用户给路径”。
4. 用本次四源任务做 fresh baseline/candidate matched 回归，必须生成实质 Markdown + PDF，并发送最终总结。

### P1：把 literal truth 提升为 claim-level truth

1. 先生成 claim ledger：`claim_id`、claim、source_path、evidence_quote、support_type。
2. evidence selection 以 query/claim 相关性为目标，禁止默认取文件首段。
3. 最终门逐 claim 检查 quote membership、source identity 和 entailment；跨来源内容不得偷换。
4. inference 必须显式标注，关键无证据 claim 使 truth gate 失败。

### P1：恢复有意义的逐步消息

每个用户可见步骤应包含：动作、对象、真实发现、证据/产物、下一步。内部 event 名、原始 JSON、字节数可
作为补充，不能成为消息主体。失败总结必须说明已完成部分、内部根因、缺失产物和可恢复动作。

### P1：接通失败到主动学习的闭环

1. reducer 输出机制级 failure signature 和 partial artifact。
2. active selector 能把该手动 Episode 选为诊断对象，并在明确预算/安全门下提出一次 bounded diagnostic。
3. 诊断只更新 diagnosis memory；repair 只有 matched fresh canary 后才更新 repair action value。
4. 自进化完成后恢复原 Task/NextAction，而不是只产出治理报告。

### P2：校正学习数据

- 为 manual task 保存具体 event/action key、partial artifacts、delivery state 和 strategy attribution。
- 区分 `learning_observation_eligible` 与 `policy_eligible`，但 manual/Campaign 同一策略动作必须有明确、可审计
  的聚合规则。
- reward components 必须与最终状态和 evidence manifest 一致。
- truth reducer 加入 claim-level audit，不能因外层诚实失败就把内容真值记为 1。

## 6. 不可回退约束

- 坚持 Event-first，不创建绕开 Event 的 Skill 快捷路径。
- 生产默认保持 `manual_stable`；修复和实验期间不自动重启 Campaign。
- 保留用户要求的收到/开始/关键发现/产物发送/最终总结消息协议。
- 不降低 PDF、证据、truth、delivery 或安全硬门来提高成功率。
- 不改写或删除本次失败 Task、Episode、Issue、trajectory 和报告；使用版本化 correction/diagnosis。
- 不用 synthetic fixture、手填 reward 或同一路径顺序调试宣称业务改善。
- 不把“记录了经验”“生成了 Candidate”“测试通过”分别冒充 self-evolution；必须有真实干预和对照结果。

## 7. 当前权威结论

Partner 已经从“无法追踪 Agent 做了什么”推进到“能以 Event/Episode/Reward 保留真实运行证据，并在一个
限域 04 策略上完成过 matched 改善和受控激活”。但本次生产负样本证明：它还没有把生成产物引用、语义
证据、用户沟通和 manual failure learning 统一成闭环。当前准确表述应是：

> 证据管道和限域策略学习已部分可用；通用主动学习、自主 repair、自进化闭环和持续 RL 改善尚未完成。

