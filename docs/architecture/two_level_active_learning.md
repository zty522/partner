# 双层主动学习与自进化

**状态**：Canonical；对象层、外部知识层与 Agent 元层均有 Event-first 入口，长期效果仍待跨日期验证  
**更新日期**：2026-09-08

## 2026-09-08：术语与外部知识链固定

本文历史上的“Agent 元层主动学习”需要进一步拆开：**主动学习面向外部知识**，回答“现在读什么/试什么最能减少未知”；
**自进化面向 Partner 自身**，回答“自己的哪个机制需要改变”。二者可共享信息增益、Episode 和 Reward 接口，但不能共享成功标签。

04 的 `external_knowledge_scout` 以三次 LLM 语义判断连接真实获取：知识缺口与查询 → 候选 repo/paper 信息增益选择 →
源码/PDF 证据综合与反证。确定性层验证 URL、clone、PDF magic、读取页数、hash、novelty 和落盘；结果进入
`workspace/external`。只有某条知识需要改变 Partner 时，才进入自进化 Candidate，而不是把研究札记直接当能力。

自进化 Candidate 的 LLM diagnosis/critic 是提议者与反驳者，机器 baseline/candidate、pytest、rollback 和
PromotionDecision 是裁判。详见 ADR 0072。

## 2026-08-31：研究资料成为元层主动学习的真实观察动作

04 新增受限研究链，将“阅读哪个 GitHub 文件/论文”作为可比较动作，而不是让通用 Planner 任意总结：

```text
真实问题+来源指纹 → VOI/novelty 选择 → 逐段证据 → belief posterior → 下一轮不同选项
```

实机首轮选择 Hermes context compressor，续轮因已读降权而转选 JitRL；JitRL 假阴性经
`semantic_alias_v2` append-only 纠正。研究成功记为 `learning_progress`，不记为业务进展，也不具备
production promotion 资格。该闭环仍停在“选证据/更新信念”；证据到代码 Candidate、真实项目 matched
改善和显式 PromotionDecision 是后续独立硬门。

## 2026-08-31：证据采用硬门与最小代码 Candidate

ADR 0048 已完成上段所说的下一硬门：`research_adoption.py` 不把一篇笔记直接提升为 Skill，而是要求
code+paper 两种来源、三类机制 Claim 和 Partner 本地三个运行合同同时成立，才注册一个
`kind=event` Candidate。候选只改变 shadow 上下文组装：保护最新 Receipt、检索同项目 reward trajectory、
附来源摘录。五项目真实快照的证据召回提高且无跨项目泄漏，但下游业务尚未执行，所以自进化 Policy 仍为
inconclusive。主动学习获得的信息只有通过这种“证据→最小干预→真实对照→决策”链，才算进入自进化。

## 1. 为什么是两层，而不是继续做函数选择

`partner_test` 的两个孵化目录研究的是主动学习的两个互补尺度，迁移后不再形成第二套 runtime：

| 孵化来源 | 主问题 | Partner 中的正式含义 |
|---|---|---|
| `bdk_transformer` | 已有有限观察时，下一次观察什么最能区分竞争解释？ | **对象层主动学习**：观察→联想→多假设→证据更新→选择下一数据/实验→同化或顺应 |
| `hermes_external_learning` | Agent 面对失败、资料和工具时，下一步做什么最能减少决策不确定性并推进任务？ | **元层主动学习**：Episode→失败假设→诊断/repair/resample 候选→匹配实验→反馈记忆→策略修订 |

函数族与 RMSE 只是对象层早期使用的透明、可证伪测试台。它们适合检查“假设有没有覆盖真实结构、下一点
有没有信息价值”，不等于 Partner 的最终目标。Agent 层的评价必须回到业务 truth、可见进展、证据完整性、
成本和安全，不能用曲线 RMSE 替代。

## 2. 统一获取函数

两层共用一个决策骨架：

```text
acquisition = information_gain × task_value × novelty − cost − risk
```

- `information_gain`：该观察/动作的可能结果能把当前竞争假设区分多少；
- `task_value`：即使获得信息，它对真实项目是否重要；
- `novelty`：是否只是重复已知证据；
- `cost/risk`：时间、模型、外部副作用及安全风险。

这意味着 MaxVar、BO、函数选择、上下文选择、工具诊断都只是同一接口下的不同 option/provider，不能把
任一启发式硬编码成“主动学习本身”。

## 3. 已进入 Partner 的实现

### 对象层

- `partner/cognition/active_learning.py`：精确离散期望信息增益、Gaussian 信息增益、通用 option 排序和
  Beta 成败记忆。
- `partner/cognition/world_model/engine.py`：下一观察由“预测分歧的信息增益 × 覆盖距离”选择，不再是纯
  MaxVar；输出 acquisition strategy 与数值证据。
- `partner/cognition/world_model/`：假设 provider、独立证据评价、联想记忆和 append-only 研究事件。

### Agent 元层

- `partner/governance/active_learning.py`：从真实 Episode 选择失败类和诊断/repair/resample 实验；读取
  `task_log.jsonl`，缺失时回放 Episode `trace.jsonl`；反馈只凭显式 evidence 更新记忆。
- `partner/v2/active_learning_events.py`：`select`、`diagnostic_shadow`、`refine_taxonomy`、`feedback` 四个
  Event-first 入口；Candidate 只能调用 allowlist 中的诊断与 taxonomy Event。
- 失败标签不再被当成固定真理：当一个标签的 step signature 异质时，生成版本化 Candidate taxonomy，
  保留原 Episode 字节不变；后续查询可在全局问题队列或显式 `focus_failure_class` 局部链上运行。

## 4. 2026-08-30 真实闭环证据

```text
48 个失败 Episode
 → 全局选中 lifecycle.unclosed_tool
 → 17/17 task_log 或 Episode trace 可读
 → 发现 6 类未闭合 step，最高占比仅 0.2571
 → diagnosis: heterogeneous_failure_class
 → Candidate taxonomy 拆为 6 个子类型，历史 Episode byte-identical
 → 聚焦查询选中 lifecycle.unclosed_tool/generate_text（9 条）
 → 9/9 可读，diagnosis: systematic_failure，confidence=0.95
 → 诊断成功反馈写入 active-learning memory
```

第一次 taxonomy 实验 `experiment_c4a6c70690fa` 诚实保留为未通过：它把“全局最高优先级”和“当前链
续接”混为一谈，下一查询选中了数量更多的 `outcome.no_business_progress`。修复是新增显式 focus，而不是
改分数强迫结果。第二次 `experiment_3f0fd4cc62bd` 五项条件全过；匹配诊断实验
`experiment_e8f5c512f4b0` 五项条件全过。三者治理决策均为 `inconclusive`，production 未变。

## 5. 与自进化、RL 的关系

- **主动学习**决定“下一条最值得获取的证据/最值得做的实验是什么”。
- **自进化**是更大的治理闭环：发现问题、修改假设空间或策略、匹配验证、保留/拒绝/回滚、再投入真实项目。
- **RL/bandit**在有重复状态、可比较动作和可信奖励时，从多轮反馈优化 action value；它不是上述两者的同义词。

当前已实现主动诊断、“顺应”（修改失败 taxonomy）以及第一个 subtype-scoped bounded repair：
`dependency-skipped` 终态观测修复。历史 matched replay 中 no-op 保留 10 个 unclosed，repair 只消除 9 个有
明确 skip 证据的假阳性并保留 1 个未知中断；resample 因没有新执行证据保持 unknown。它证明了第一种安全
repair 模式，不是通用 repair executor。下一阶段需对未知中断收集 fresh execution，再建立另一种 Candidate；
在 outcome reward 可辨识前不得称为 RL 已生效。

selector v2 会把最新 diagnosis 作为 hypothesis posterior，降低重复诊断的新颖性，并用 posterior expected
success 与 Beta memory 计算 repair/resample 任务价值；算法版本属于 decision identity，禁止覆盖旧决策。

ADR 0039 进一步证明，repair evaluator 也必须是可修订假设：v1 只看 remediation 得到 9/10；v2 回放 plan
DAG、参数引用和上游 terminal 后纠正为 10/10。随后隔离 fresh PlanExecutor canary 真实验证并行成功分支与
级联 skip 分支都有唯一终态。旧评价结果保留，评价器升级版本化，防止“学习系统的裁判”成为不可质疑的黑箱。

第二条 outcome 链从 `outcome.no_business_progress` 的异质治理原因出发，拆出
`governance.unlinked_previous_receipt`，并在 10 条证据上诊断为 systematic。ADR 0040 没有通过降低硬门来
“优化成功率”，而是修正状态表征：`inputs` 是材料关系，`continue_from_project` 才是承接意图。fresh
Event canary 同时要求 standalone 放行、明确漏交接拒绝、正确交接通过。这说明元层主动学习已经能从运行
Episode 选择问题、细化假设、修改局部合同并反证安全边界；仍不能等同于通用 RL 或长期自治。
同时，RL read path 对缺少显式 intent 的旧 manual trajectory 保守扣除 `handoff_consumed` bonus：历史记录
不改写，但不再让模糊旧标签影响 action value。

## 6. 不回退约束

1. Event-first；知识笔记、Candidate 文本、模型输出不能直接执行。
2. 历史 Episode、Receipt、Event append-only；taxonomy 是版本化解释层，不反写历史。
3. 全局调度与局部实验续接是两个显式模式，不用隐含权重混合。
4. 信息获得不等于业务改善；诊断成功只能更新诊断 action 的证据，不能冒充 repair 成功。
5. `partner_test` 只保留 provenance/benchmark；Partner runtime 禁止重新依赖它。
6. production 保持 `manual_stable`；自动 repair、canary、promotion 仍需各自的证据门。
7. 优化失败率不得偷换合同：每个 repair 必须同时预注册“该放行的正例”和“必须继续拒绝的反例”。

## 下游可消费性硬门（ADR 0049）

资料选择层的 coverage 或 context recall 通过后，还需以冻结任务验证下游 Agent 是否能恢复正确 Claim、来源
和下一动作。消费者与 evaluator 必须分离；truth/safety 失败硬归零。该门产生学习证据，不自动产生业务
Reward。若测试需要把真实或派生项目状态发给外部模型，必须先取得用户明确授权。

## 7. 2026-08-30：从诊断闭环进入首个真实业务策略学习

`planning.semantic_preflight` 链已继续完成“选择 → 证据诊断 → taxonomy → bounded repair → 三组真实
baseline/candidate → PromotionDecision → 独立生产激活”。`experiment_6ef2b9be620b` 的 Candidate 在三组
04 文件证据任务中 3/3 完成，Baseline 1/3；逐对 reward gain 为 `[0.9,0.9,0.0]`。

新增 sustained gate 不接受单个均值：至少三对、全部不回退、至少两对严格改善。这个结果首次允许在限域内说
“策略学习让业务持续变好”，但其作用域只有 04 的文件证据报告。05 后续 RL 必须继续从 Campaign 的真实
business wave 取样；诊断成功、本地文件、服务存活和 no-change scout 都不能充当跨项目奖励。

## 8. 2026-08-30：手动生产负样本证明闭环尚未自动接通

04 Task `d2cb657c-f79d-4146-93d2-cdec40dc084e` 使用已激活的 `candidate_preflight_contract_v2` 后，能读取
四个真实来源并生成 Markdown，但 PDF Event 因上游生成文件的相对路径未解析而失败。系统原样重试三次，
只建立通用 verification Issue，未自动选择机制诊断、repair 或 resample。

这次还证明 literal evidence 不是 semantic evidence：四条逐字引文大多只是标题/banner，报告出现跨来源
混淆；Episode 却未将其识别为 truth 问题。因此 Agent 元层下一阶段必须同时扩展：

1. typed output-reference failure taxonomy 和 partial-artifact 表示；
2. claim→source→quote 的语义支持图，而不只是 quote membership；
3. manual Episode 到 active selector 的明确接线和预算门；
4. 诊断成功、repair 成功、业务恢复分别记账；
5. repair 后返回并完成原用户任务。

在完成 matched fresh canary 前，本次只能作为负面 Episode；不得说系统已从它主动学习或完成自进化。
详见 ADR 0043。
