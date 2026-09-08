# Event-first 自进化架构

**状态**：Canonical  
**更新日期**：2026-08-30  
**生产边界**：`manual_stable` 不变；本文建立治理合同，不开启自主长跑

## 1. 核心决策

Partner 必须坚持 **Event-first**：一次能力变化只有在事件链中发生，才算发生。Skill、Prompt、配置、
代码补丁、上下文策略、模型策略和 BDK kernel 配置都只是 Candidate Artifact；文件存在、状态标签或
`control_policy.json` 映射不能替代执行与效果证据。

```text
observation/event failure
  → issue/recorded
  → candidate/proposed
  → candidate/execution_requested
  → candidate/execution_completed
  → experiment/started
  → baseline/candidate 独立执行
  → experiment/completed
  → policy/promoted | policy/rejected | policy/inconclusive
```

项目推进与系统进化是两条账本。自进化验证结束后必须返回原项目，不得用治理报告取代项目工作。

## 2. Event、Candidate、Skill、Policy 的边界

| 对象 | 定义 | 不等于 |
|---|---|---|
| Event | 发生、请求、执行、验证和决策的权威事实 | 文件、计划或描述 |
| Candidate Artifact | 一个待验证改变，可为知识、Skill、事件策略、配置、代码或模型策略 | 已生效能力 |
| Skill | 有稳定输入/输出、可调用入口、适用边界和回归证据的可复用能力 | 一篇笔记或分类标签 |
| Experiment | 同一问题下的 baseline/candidate 隔离比较 | 手填奖励或一次演示 |
| Policy | 对已验证 Candidate 的选择规则 | Candidate registry 中的状态字符串 |

外部笔记进入 Partner 时只能先成为 `knowledge_draft`。它没有 Event 入口，因此
`execution_ready=false`，不得 shadow/canary/promote。只有完成实现并补齐 `execution_contract` 后，才能成为
可执行 Candidate；验证稳定后才可能沉淀为 Skill。

## 3. Candidate 可执行合同

2026-08-31 的 `candidate_evidence_trajectory_context_v1` 是首个由互补源码/论文 Claim 和 Partner 本地实现
指纹共同编译出的 context-policy Candidate。它仍只通过 `research_adoption_context_shadow` Event 执行；
第一次硬门失败和第二次修复通过分别属于两个 Experiment，禁止覆盖失败后只展示成功结果。即便五项目
上下文证据召回改善，未测下游业务结果时 Policy 仍必须是 inconclusive。

当前合同保存在 Candidate record 的 `execution_contract`：

```json
{
  "ready": true,
  "kind": "event",
  "event_type": "targetdiff_bdk_function_pool",
  "allowed_instances": ["02"],
  "default_params": {"epochs": 200}
}
```

硬门：

1. 只允许 `kind=event`，禁止 Candidate 存储任意 Python import、shell 或代码字符串后直接执行。
2. `event_type` 必须位于调用方提供的白名单 handler registry。
3. instance 必须在 `allowed_instances` 中。
4. `production` 执行要求 Candidate 自身 `production_effective=true`；shadow 不会自动进入生产。
5. Promotion 指定 Candidate 时，Candidate 必须可执行且绑定同一个 Experiment。
6. 每次尝试无论成功、失败或被阻止，都写 requested/completed Event；`execution_id` 绑定 Candidate，重放
   返回原结果而不重复副作用。
7. Event ledger 的 sequence/hash/append 在跨进程文件锁内完成，支持最多双槽并发写入。

实现位置：

- `partner/governance/evolution_events.py`：哈希链 Event 账本与验链。
- `partner/governance/candidate_execution.py`：可执行合同与白名单执行门。
- `partner/v2/candidate_events.py`：确定性 `execute_candidate` Event。
- `partner/governance/candidate_skills.py`：兼容旧存储，同时写 `candidate/proposed` Event。

`candidate_skills/` 目录名是历史兼容，不再表示其中每条记录都是 Skill。

## 4. BDK 的实际含义与适用边界

> 2026-08-30 原意校正：本节的 FunctionPool 是既有数值实验器，不是 BDK 北极星。世界模型主线见
> [`world_model_bdk.md`](world_model_bdk.md) 与 ADR 0034。

当前 BDK FunctionPool 是一个数值函数学习器：Linear、Quadratic、Fourier、ExponentialDecay 四个分支，
由 gate network 为输入分配权重并加权预测。Partner wrapper 当前用 MSE + weight decay 训练；
`ComplexityPenalty` 虽在沙箱定义，但尚未用于 Partner fitter。Ocamms 是训练后的核激活数量检查。

因此当前 FunctionPool：

- 适合：低维数值回归、函数结构比较、残差分析、受控科学实验 Candidate。
- 不适合直接充当：LLM 推理内核、Agent 状态机、工具调度器、长期记忆、全局自进化控制器。
- 目前也不是严格贝叶斯系统：没有在 Partner runtime 中维护参数后验和校准不确定性。

BDK 研究中更大的“感知—联想—假设—行动—记忆—自反”构想可以作为认知架构设计来源，但不能因为
名字相同就把 FunctionPool 强行放到 Agent 底座。Partner 的底座仍应是：Event runtime、状态机、证据账本、
上下文/记忆和工具协议。BDK只在明确数学问题上作为可替换 model provider；将来若完成真正的
Bayesian uncertainty、PFN/in-context 或动态专家路由，再分别通过 Candidate 实验接入。

## 5. `hermes_external_learning` 是否证明不需要 RL

没有。其 BO/MaxVar 笔记只回答一个窄问题：在有 surrogate model 的低维连续实验空间里，如何挑下一个
最有信息或最有改进潜力的实验点。这个场景可以用 GP + MaxVar、UCB、EI，不必训练 RL policy。

Partner 有三类不同决策，不应只选一个算法：

| 决策 | 推荐方法 | 原因 |
|---|---|---|
| 连续参数/科学实验选点 | BO、MaxVar、active learning | 样本少、可解释、可利用不确定性 |
| 重复的上下文/工具/策略选择 | 保守 contextual bandit 或 offline RL | 需要跨任务利用真实反馈和长期统计 |
| 一次性代码/协议改变 | Issue→Hypothesis→测试→人工治理 | 样本不可交换，奖励难定义，不应假装RL |

因此 RL 不是自进化本身，也不是必选底座。自进化是治理闭环；RL只是当决策具有重复状态、可比较动作和
可信奖励时使用的策略优化器。没有这三项时，回退到确定性规则、BO或人工审批。

## 6. Partner 真正的自进化路径

1. **观察**：真实用户反馈、Episode、Receipt、工具结果和交付回调产生事实 Event。
2. **定位**：聚类重复失败，但保留来源、项目、实例和因果边界。
3. **提出**：生成一个最小 Candidate Artifact；知识笔记不能直接变成 Skill。
4. **执行**：通过 `execution_contract.event_type` 走正常 Event runtime，保留逐步消息与项目主线。
5. **对照**：冻结输入、模型、工具、预算和版本，跑独立 baseline/candidate。
6. **评价**：truth/safety 是硬门；再比较业务进步、可观测性、效率和成本。
7. **选择**：按问题类型使用规则、BO/MaxVar、bandit/offline RL；输出明确决策 Event。
8. **巩固**：promoted 才进入长期经验；rejected/inconclusive 保留反例并不影响生产。
9. **继续项目**：恢复原任务的 NextAction，检验改进是否解决最初 Issue。

持续运行是重复这一闭环，不是让模型不停写反思或生成 Candidate。生产默认继续手动单轮；长跑仍需单独
Campaign、双槽、预算、恢复和用户授权。

## 8. Candidate 下游验证边界（ADR 0049）

`compile → execute_candidate → downstream answer → independent evaluate` 仍是 Event-first shadow 链。
Experiment 可根据失败生成下一版 Candidate，但不得直接改 production。项目数据离开本机是独立权限边界；
网络可用和上下文已脱敏都不能代替用户授权。当前本地三任务通过，Policy 仍为 inconclusive。

## 7. 当前真实状态

- Event-first ledger 和 Candidate执行门已实现并有定向测试。
- `targetdiff_bdk_function_pool` 与 `execute_candidate` 已加入 executor 的确定性 handler 集合，但均不会自动执行。
- 旧 Hermes 笔记 Candidate 是历史记录；未来注册会标为不可执行 `knowledge_draft`。
- 旧 `bdk_closure_*` 使用硬编码 pytest 奖励，已从有效 control policy 移除并写 correction；相关轨迹被策略层排除。
- ADR 0033 已完成第一次真实 baseline/candidate 实验：BDK 1.544721 vs sklearn best 1.541555，结论
  `inconclusive`。这证明执行链可用，不证明 BDK 更优；没有新的 production promotion。
- Experiment/Candidate JSON 是 Event 的查询投影；Policy Event 产生后会回填 experiment status/result 和
  Candidate shadow evidence，避免后续 Agent 读取到陈旧初始状态。
