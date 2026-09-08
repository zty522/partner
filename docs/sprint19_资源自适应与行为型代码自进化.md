# Sprint 19：资源自适应与行为型代码自进化

**状态**：Three Loops Implemented / Three Production Candidates Passed / Superseded by Sprint 20  
**日期**：2026-09-08

## 目标

把 Sprint 18 的“发现问题与记 Reward”推进为三个彼此独立、可观察、可反驳的生产闭环，同时让五实例按本机实时资源运行。

## 先澄清：Candidate 是什么

Candidate 是“候选改进方案”，不是 Event 的替代品，也不是已经生效的能力。它可以是参数策略、上下文策略或
一个真实代码 diff，但必须由 Event 提出、由相同输入/预算下的 baseline-candidate 实验验证，再产生
`promoted/rejected/inconclusive` 决策。只有通过生产硬门并写出 `policy/activated` 的 Candidate 才有
`production_effective=true`。反思文字、PDF、重复确认一个已生效策略都不算新 Candidate。

## 三条闭环怎样实现

### 1. 迭代推进项目

`Receipt → project/action_selected → 领域业务 Event → 新产物/指标 → 新 Receipt → 下一动作`。
验收不看进程是否常驻，而看相邻 Receipt 是否承接、语义动作是否改变、真实产物是否存在、是否产生下一动作或
诚实停止条件。`project_iteration_audit` 单独核验这条链。

### 2. 主动学习

真实失败、重复结果或知识缺口先归约为 Episode；随后执行
`observe → select → diagnose → repair proposal/matched experiment`。完成学习后不永久停留在报告中，而由
`active_learning/project_action_selected` 把干预应用到下一项目动作，记录原选择、替代选择、原因和证据。
`active_learning_effect_audit` 只有在“观察、完成、后验存在、下一动作确实改变”同时成立时才通过。

### 3. 自进化

自进化消费主动学习找到的问题，但二者不是同一个东西：主动学习决定“最值得学/试什么”，自进化决定
“系统本身改什么”。当前受限路径为：Episode 机制 → 白名单 RepairRecipe → 真实 unified diff → 冻结
baseline-fail/candidate-pass → 聚焦回归 → 原子实施/回滚 → Event-first 激活。`self_evolution_effect_audit`
拒绝把反思、报告和 `already_effective` 重复记录算成新进化。

`sprint19_acceptance` 同时给出三条结果，但不允许一条通过掩盖另一条失败。

## 已完成

- [x] 固定双槽改为 CPU/load/可用内存驱动的 1–5 槽，并实现资源收缩自然排空。
- [x] 五实例唯一消息脚本、显式消息优先、子集复测。
- [x] 05 测试消息不再强迫每轮生成 Candidate；按契约盘点→失败回归→缺口整理→有证据时受限改进轮转，
  避免饱和 recipe 被反复调用。
- [x] 关闭 comment-only 伪 Candidate。
- [x] 从真实重复 Episode 生成首个行为型代码 diff。
- [x] baseline-fail/candidate-pass、聚焦回归、preimage rollback、任务本地产物。
- [x] Event-first Candidate/Experiment/Promotion/Activation 账本。
- [x] 第二个跨领域 Candidate：04 Harness 源码轮换，真实 Codex 文件读取与 ACK。
- [x] 第三个行为型 Candidate：05 生产代码面轮换；baseline 无 `code_variant`，Candidate 按 turn 改变，15 项聚焦回归通过并激活。
- [x] 修复 `already_effective` 重复冒充新 Candidate：现在返回 `no_new_candidate / production_effective=false`，同时单列既有策略状态。
- [x] 修复无新 Candidate 的 Reward/Receipt 串线：真实 JSON 证明 `no_new_candidate` 时只写学习观察，
  `project_state_mutated=false / business_progress=false`；同一结论再次出现按重复样本记 `-0.1`。
- [x] 新增动作选择证据 Event 与四个三环验收 Event；学习后的下一动作必须留下反事实选择记录。
- [x] PDF 改为五种领域版式（编辑简报/研究记录/实验手记/源码札记/工程审查），不再共享居中封面和统一蓝色模板。
- [x] 五实例实跑与 Reward 审计；05 的无新 Candidate 路径另做生产复验并通过。
- [x] 历史多 writer ledger 异常可见化；旧记录不改写，新 Event 保持严格链。
- [x] 明确主动学习=外部知识、自进化=Partner 自身、Reward 驱动策略选择；该机制自 Sprint 20 起正式称 EGPL，不再简称 RL。
- [x] 04 真实 GitHub/论文获取进入 `workspace/external`，读取源码与 PDF 证据、形成可证伪想法；QQ 默认只交付 PDF。
- [x] 自进化 Candidate 增加 LLM 因果诊断和独立 critic，机器对照/回归/回滚仍是唯一晋升硬门；饱和时诚实 `no_new_candidate`。
- [x] 修复 `external_knowledge_scout` 被 real-action contract 假判失败，并增加 API token 审计。

## 未完成硬门

- [x] 2026-09-08 01:49 五实例脚本实跑；01/02/04/05 得到 `business_progress=true / duplicate=false / reward=0.85`。03 首次因热进程缓存旧 Event 类型诚实失败并自动进入学习链。
- [x] 03 完成学习 redrive 并真正改变下一动作：正常选择为 integrator smoke，学习干预改选 temperature sweep；4/4 模拟过门、最坏相对能量漂移 `1.9449e-05`、Receipt `receipt_9b7735ed7653`、Reward 0.85。
- [x] `sprint19_acceptance` 在生产账本上通过：五项目迭代 5/5、主动学习六门全过、自进化四门全过。
- [x] 05 生产复验 Task `86e1e1d6-ce35-4bb7-95aa-5a36f4f17102`：无新 Candidate 只得学习
  Reward `0.60`，不生成项目 Receipt、不领取业务 Reward；相同复查由回归保护为 `-0.10`。
- [ ] 第二、第三种 failure mechanism 各完成独立行为型 Candidate 与 rollback 演练。
- [ ] 跨三个真实日期形成足够样本并证明策略后验改变了下一动作选择。
- [ ] 通用 LLM patch proposal 在脱敏真实项目上通过独立冻结验收；不得让生成者自评自批。
- [ ] 将至少一个新外部知识想法编译为 Partner-owned Candidate，并证明它改善后续真实项目动作，而不只是产出札记。
- [ ] 外部选源质量跨三轮达到领域相关、证据可读、非重复，并用反证结果校准 acquisition policy。

## 完成定义

短期工程完成门要求三环 audit 独立通过、五实例消息中能看见具体动作/指标/学习干预、PDF 领域版式不同；长期门
另要求至少三个不同机制、三个真实日期均存在可重放 matched 对照、零 false-success、所有生产实施有回滚证据，
以及高/低资源压力下无并发越界或在途任务丢失。长期门达成前只称“受限自进化与主动学习可用”，不得称
“长期 RL 成熟”。

## 本轮验证

- ADR 0072 完整仓库：960 passed，2 warnings，129.32 秒；定向外部学习/Candidate/验收/交付：150 passed。
- 聚焦三环、路由、用户体验和领域报告：25 passed；热升级兼容新增后相关测试 13 passed。
- Candidate 聚焦回归：15 passed。
- 完整仓库：949 passed，2 warnings，136.95 秒。
- 生产总审计：`share/mind/governance/active_learning/sprint19/acceptance_latest.json`，status=`passed`。
