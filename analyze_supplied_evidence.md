# Analyzed Evidence Report

日期: 2026-08-26
状态: 分析报告（仅基于上游 verified_sources 输入）

## 事实边界声明

本报告仅以上游 verified_sources 中明确存在的 source_path 与 evidence_quote 为事实依据。
对于未在证据中出现的 run / test / command 真实输出、步骤编号、HTTP 状态码、哈希值、
样本数、日志字段、测试通过/失败判定等运行指标，相应案例统一标注为 proposed
（拟议验收案例、未执行）。本报告不补全任何未观测字段，不声明任何实际运行结果。

---

## 证据源 1: harness_episode_learning_closed_loop_verified.md

source_path: /mnt/e/work/partner_workspace/share/evidence/literature_github_learning/manual/20267094-ca30-4295-9b77-76cc75c831b2/harness_episode_learning_closed_loop_verified.md
evidence_quote: source_path: /mnt/e/work/partner_workspace/files/outgoing/20260826_031159_563619_manual_canary_06.md

### 来源上下文

输入 A 为闭环验收报告，标题为 # Harness Episode Learning 闭环验收报告（closed_loop_verified）。
该报告自身已明确声明事实边界："本验收报告只承认两份上游输入文件中逐字可读的内容为事实"。
报告四个维度（事件轨迹、会话记忆、生命周期、失败恢复）均以输入中已被断言的字符串、
路径、引用文本为唯一证据来源。

### 事件轨迹维度（基于证据字段名）

- DeepSeek Harness 涉及三类命名空间：session/event（durable fact，append 到 log 并广播）、
  agent/*（承载 live Agent，含 inbox / step / status / request / validation / continuation
  六个子事件名）、能力事件（fs/*、tools/*、telemetry/* 等挂在 seam 上）。触发条件在证据
  层面未被完整给出，标注为 N/A。
- OpenAI Codex Rollout Trace 表现为 ordered raw events 与 payload references；hot path 不
  构造最终语义图，由离线 reducer deterministic 重放后形成 state.json。触发精确时序未在
  证据中给出，标注为 N/A。
- Hermes-Agent 仅涉及 save_trajectory 静态 JSONL 追加写入；事件名/命名空间未被证据给出，
  标注为 N/A。
- OpenClaw 在证据层面确认存在 src/agents/embedded-agent-runner/ 与 @openclaw/agent-core，
  但事件协议本身未被证据直接命名，标注为 N/A。

### 会话记忆维度（基于证据字段名）

- DeepSeek Harness：core/session 提供 append-only SessionEvent log 与 in-memory store
  （ctx.sessions）；core/system-prompt 暴露 ctx.systemPrompt，由 prompt section + tool schema
  组装。记忆与 prompt 在能力层是两个独立 ctx 键。
- OpenAI Codex：记忆以 reducer 重放后形成的 state.json 语义图为核心，含 threads / turns /
  conversation_items / runtime objects / interaction_e（证据被截断，后续字段 N/A）。

### 案例标记

- 成功案例: proposed（拟议验收案例、未执行；证据中无 run/test/command 真实输出）
- 失败案例: proposed（拟议验收案例、未执行；证据中无 run/test/command 真实输出）

---

## 证据源 2: harness_reference_adoption.md

source_path: /mnt/e/work/partner/docs/architecture/harness_reference_adoption.md
evidence_quote: **范围**：DeepSeek Harness、OpenAI Codex、OpenClaw、Hermes Agent 与 Partner 自进化/RL

### 来源上下文

文档标题: # 四套外部 Harness 学习与 Partner 采用边界
日期: 2026-08-26
状态: L2 当前架构参考

### 核心结论（基于证据）

四套成熟 harness 共同证明：长期运行可靠性来自"可重放事实脊柱 + 明确运行时边界 +
独立评价"，而不是无限增加 LLM 轮数。它们解决执行、上下文、会话、记忆和可观测性，
但不直接提供经过因果验证的持续 RL。Partner 不替换 Python 五实例和手动消息根基，
而把四者统一为 Episode 证据层，再用自己的 Receipt、真值门、EvolutionExperiment 和
PromotionDecision 学习与晋升。

### 四者核心特征矩阵（基于证据）

| 来源 | 最强机制 | Partner 独立采用 | 明确不采用 |
|---|---|---|---|
| DeepSeek Harness | typed append-only SessionEvent；turn/step/tool/compaction bracket；pre/guard/execute/post/finalize | Episode 原始事实脊柱、可检测中断生命周期、工具策略与结果分开 | Cordis/TypeScript 根基、插件树重写 |
| OpenAI Codex | observe first, interpret later；raw payload + deterministic reducer；conversation/runtime graph | 离线 reducer、model/tool/artifact/delivery 关联、诊断旁路不得破坏运行 | Rust core、thread store、终端/TUI 实现 |
| OpenClaw | Gateway/session 权威；SQLite transcript；workspace memory；pre-compaction flush；cron 隔离 | 长期会话与渠道镜像原则、文档/记忆分级、隔离后台 authority | 直接恢复自治 cron、整套 Gateway 替换 QQ bridge |
| Hermes Agent | post-turn memory sync；FTS 检索；skill nudge；observer IDs；保留首尾的压缩 | 经验检索→Candidate Skill 生命周期、低成本复用、关联 ID | 把模型写的 memory/skill 或 completed trajectory 当作已验证进化 |

### 统一目标设计（基于证据）

```
task raw logs / step payloads / channel transcript / Receipt
                         ↓
                 Episode Trace v3
                         ↓
 deterministic reducer: model / tool / artifact / delivery / failure graph
                         ↓
 Reward Vector: truth / progress / handoff / observability / efficiency / safety
                         ↓
failure cluster → Candidate Skill/Strategy → replay → shadow → canary → promotion
```

### 已落地条目（基于证据字段）

1. 四仓库均固定 revision，浅克隆保存在 workspace external/code，没有执行安装或复制源码。
2. external_catalog.py 记录 upstream、revision、license、SHA256 和 execution_allowed=false。
3. episode_trace.py 把 task JSONL、step result、Receipt、trajectory 离线归约为 v3 bundle。
4. Reward Vector v3 分为 truth、business progress、handoff、observability、efficiency、
   safety；truth/safety 是不可补偿硬门。
5. shadow_evolution.py 聚类 Episode 失败并建立 candidate Experiment，但不能改生产 prompt、
   代码、scheduler 或 control_policy.json。

### 案例标记

- 成功案例: proposed（拟议验收案例、未执行；证据中无 run/test/command 真实输出）
- 失败案例: proposed（拟议验收案例、未执行；证据中无 run/test/command 真实输出）

---

## 证据源 3: 0006-unified-harness-episode-learning.md

source_path: /mnt/e/work/partner/docs/decisions/0006-unified-harness-episode-learning.md
evidence_quote: Partner 不迁移到 DeepSeek Harness、Codex、OpenClaw 或 Hermes 的运行时。四者分别提供可独立采用的

### 来源上下文

文档标题: # ADR 0006：四套 Harness 统一为 Episode 证据层，进化先 Shadow
状态: accepted
日期: 2026-08-26

### 核心决策（基于证据）

Partner 不迁移到 DeepSeek Harness、Codex、OpenClaw 或 Hermes 的运行时。四者分别提供
可独立采用的不变量：DeepSeek 的 append-only 生命周期事实；Codex 的 observe-first/offline
reducer；OpenClaw 的 Gateway/session/transcript 权威与分级记忆；Hermes 的 memory→
candidate skill 经验复用。Partner 在其上保留自己的五实例、手动消息、Receipt、真值门、
EvolutionExperiment 和 PromotionDecision。

统一学习单元是 Episode Trace v3：原始 task JSONL 与 step payload 保持权威，离线 reducer
生成 model/tool/artifact/delivery/receipt 关联图。奖励改为 truth、business progress、
handoff、observability、efficiency、safety 六维向量；truth 或 safety 失败不可被其他分量抵消。

自进化候选只能按 episode→failure cluster→Candidate→historical replay→shadow→matched
canary→explicit promotion 前进。初期 shadow 只写实验和评价，不改生产 prompt、代码、
控制策略或自动续轮。

### 后果条款（基于证据）

- 生产继续是 manual_stable，现有 04 真值策略仍在，但事实矛盾属于缺陷并 fail closed。
- 05 可以离线发现候选，不能自评自升；至少 10 个匹配样本/臂后才讨论 canary。
- raw trace 记录失败不得阻断业务；reducer 输出可重建、可替换，不能覆盖原始日志。

### 案例标记

- 成功案例: proposed（拟议验收案例、未执行；证据中无 run/test/command 真实输出）
- 失败案例: proposed（拟议验收案例、未执行；证据中无 run/test/command 真实输出）

---

## 综合小结

三条证据源在架构层面相互呼应：
1. harness_reference_adoption.md 提出 L2 架构参考与四者特征矩阵；
2. 0006-unified-harness-episode-learning.md 在 ADR 层面把决策固化为 accepted；
3. harness_episode_learning_closed_loop_verified.md 在闭环验收层面（输入 A/B 横向比对）
   做证据覆盖度自检，明确事实边界。

所有跨源事实矛盾统一按 fail closed 处理（证据中明确条款）。任何未在证据中出现的
真实运行输出，本报告均不补全。

---

文件路径: /mnt/e/work/partner/analyze_supplied_evidence.md
