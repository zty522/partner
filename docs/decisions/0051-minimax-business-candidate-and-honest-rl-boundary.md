# ADR 0051：MiniMax 单模型证据、真实业务 Candidate 与长期 RL 边界

**状态**：Accepted；Candidate 继续 shadow，生产仍 blocked  
**日期**：2026-09-01  
**授权边界**：只允许把不可逆脱敏后的项目派生上下文发送给 MiniMax-M3；本阶段不调用 DeepSeek

## 背景

ADR 0050 建立了生产成熟度总门，但当时没有获得外部模型数据外发授权，也没有真实 04 业务
baseline/candidate 执行。用户随后明确授权 MiniMax-M3，明确排除 DeepSeek v4-flash，并要求把通用 LLM、
真实业务产物与长期 RL 三个命题分别验证，不能用测试数量替代业务证据。

## 决策与实现

1. `direct_api` 默认且本阶段唯一允许的 provider 为 MiniMax；缺少 MiniMax 配置时 fail closed，不再隐式
   fallback 到 DeepSeek。只有调用方显式指定 `provider=deepseek` 才可能进入 DeepSeek 分支。本阶段 API
   audit 的新记录全部是 `api=minimax, model=MiniMax-M3`。
2. Research-Adoption 任务固定走 Event-first：Candidate 使用
   `execute_candidate → research_adoption_context_shadow → generate_text → create_file`；baseline 使用
   `select_context → generate_text → create_file`。两者均以确定性计划编译，Planner LLM 调用为 0。
3. Candidate Event 输出 typed `verified_source_evidence`。治理层重新打开源码/PDF、核对 SHA-256 与逐字引文，
   再运行 Claim Ledger 语义门；计划内 `push_files` 被移除，只有真值门通过后统一交付层才能发送。
4. 修复 `generate_text` 规范结果被硬截到 8,000 字符的问题。显式 Claim 块必须具备八个字段；半截 Claim
   不再被默认成 `not_found`。带空格的 typed `source_paths` 被识别为输入，不会把 `Updates.pdf` 误判为
   待生成产物。
5. pair-14 的真实负样本显示模型把 Hermes 的上下文管理误标为 `event_recording`，把 WebRL 课程学习误标为
   `task_lifecycle`。没有降低语义门；该失败被转化为 Candidate prompt 的轴约束后另开 v3 实验。

## 实验证据

### MiniMax 单模型通用任务门

两个独立外部实验均使用 MiniMax-M3 和不可逆脱敏 payload：

- `experiment_0345902bd8fa`
- `experiment_9a43658fbba8`

共 6 个 matched pair、3 类冻结任务；两次均 baseline 0/3、Candidate 3/3，payload audit 无违规。
这只证明 `single_authorized_general_purpose_model` 范围，不是跨模型泛化证明。

### 04 真实业务链与严格 matched pair

- Canary-11 首次打通 typed 双源、Claim 真值、统一交付、Episode/Reward，但随后复核发现报告在 C007
  中途截断；历史记录不改写，该轮不作为权威正样本。
- Canary-12 修复后完成：双源验证、7 个完整 Claim、Reward 0.85、业务进展为真、生产未变。
- `experiment_research_business_v3 / jitrl_hermes_pair_15`：
  - baseline：冻结来源 2 个、验证 0 个，真值门拒绝，Reward -0.4；
  - Candidate：冻结来源 2 个、验证 2 个、10 个 Claim 全通过，Reward 0.8；
  - matched delta `+1.2`，两臂同 query、MiniMax、预算、输出合同与真值门，唯一实验变量为上下文策略。

pair-15 的文件 push callback 返回 `ok=true`，但任务由 local inbox 注入，没有 QQ 文本收件人，步骤/总结
消息没有真实渠道 ACK。因此治理只接受为 durable local experiment observation，`delivery_confirmed=false`；
不得把它计入“完整渠道送达业务轮”。

## 当前成熟度结论

最新 readiness attestation 仍为 `blocked`：

- MiniMax 单模型通用任务门：通过；不是跨模型门。
- 持续业务门：3/6 个完整渠道送达合格轮次、1/2 项目、1/3 时间窗；未通过。
- 长期 RL 门：未通过。v3 只有每臂 1 个样本；全局历史仍包含调试期 false-success，且没有 20/arm、
  两项目、三时间窗、Wilson 下界或回滚演练证明。
- Event ledger：有效。

因此本阶段证明的是“一个 Candidate 在一个真实 04 业务任务和 MiniMax 上能产生受验证改善”，不是
“业务持续变好”或“长期 RL 已成熟”。`control_policy.json` 未修改、未 promotion、未 activation、未启动
Campaign。

## 验证

- 聚焦回归：34、18、11 等多组套件均通过。
- 完整回归：`666 passed in 153.82s`。
- readiness：`production_ready=false`，attestation 位于
  `share/mind/governance/experience_guided_policy/production_readiness/candidate_research_downstream_a420574f08ad.json`。

