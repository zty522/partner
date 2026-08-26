# 四套外部 Harness 学习与 Partner 采用边界

**日期**：2026-08-26  
**状态**：L2 当前架构参考  
**范围**：DeepSeek Harness、OpenAI Codex、OpenClaw、Hermes Agent 与 Partner 自进化/RL

## 1. 结论

四套成熟 harness 共同证明：长期运行可靠性来自“可重放事实脊柱 + 明确运行时边界 + 独立评价”，
而不是无限增加 LLM 轮数。它们解决执行、上下文、会话、记忆和可观测性，但不直接提供经过因果验证的
持续 RL。Partner 不替换 Python 五实例和手动消息根基，而把四者统一为 Episode 证据层，再用自己的
Receipt、真值门、EvolutionExperiment 和 PromotionDecision 学习与晋升。

## 2. 四者的核心特征与边界

| 来源 | 最强机制 | Partner 独立采用 | 明确不采用 |
|---|---|---|---|
| DeepSeek Harness | typed append-only SessionEvent；turn/step/tool/compaction bracket；pre/guard/execute/post/finalize | Episode 原始事实脊柱、可检测中断生命周期、工具策略与结果分开 | Cordis/TypeScript 根基、插件树重写 |
| OpenAI Codex | observe first, interpret later；raw payload + deterministic reducer；conversation/runtime graph | 离线 reducer、model/tool/artifact/delivery 关联、诊断旁路不得破坏运行 | Rust core、thread store、终端/TUI 实现 |
| OpenClaw | Gateway/session 权威；SQLite transcript；workspace memory；pre-compaction flush；cron 隔离 | 长期会话与渠道镜像原则、文档/记忆分级、隔离后台 authority | 直接恢复自治 cron、整套 Gateway 替换 QQ bridge |
| Hermes Agent | post-turn memory sync；FTS 检索；skill nudge；observer IDs；保留首尾的压缩 | 经验检索→Candidate Skill 生命周期、低成本复用、关联 ID | 把模型写的 memory/skill 或 completed trajectory 当作已验证进化 |

DeepSeek 的关键不变量是“model-visible means logged”；Codex 的关键不变量是原始观察与语义解释分离；
OpenClaw 强调 session/transcript 的持久权威和后台隔离；Hermes 提供最实用的 memory→skill 经验复用。
Harness 是环境与数据生成器，RL 是评价和选择层，二者不能混为一个“反思循环”。

## 3. 统一目标设计

```text
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

01–04 产生真实业务 Episode；05 只做离线归约、聚类与实验定义；确定性 evaluator 判定；03 只有在
实验定义后才实现候选；用户保留 production PromotionDecision 权限。

## 4. 已落地

1. 四仓库均固定 revision，浅克隆保存在 workspace `external/code`，没有执行安装或复制源码；Hermes
   本地 checkout 来自 `zty522/hermes-agent` fork，机器目录同时保留 NousResearch upstream 与 fork remote。
2. `external_catalog.py` 记录 upstream、revision、license、SHA256 和 `execution_allowed=false`。
3. `episode_trace.py` 把 task JSONL、step result、Receipt、trajectory 离线归约为 v3 bundle；raw 保持权威，
   `state.json` 可重建。
4. Reward Vector v3 分为 truth、business progress、handoff、observability、efficiency、safety；
   truth/safety 是不可补偿硬门。
5. `shadow_evolution.py` 聚类 Episode 失败并建立 candidate Experiment，但不能改生产 prompt、代码、
   scheduler 或 `control_policy.json`。

## 5. 下一步

- 把每条用户进度消息的 runtime ack、context selection、vision、代码运行和 parent episode 完整关联。
- 清分 historical/manual/canary/production 数据集，避免旧 Campaign 噪声污染手动核心学习。
- 只优化宏观 Harness 策略：上下文选择、evidence skeleton、extract-then-report、失败分类恢复、
  validation/readback/vision；不让 RL 直接生成任意生产代码。
- Shadow 至少 10 个匹配样本/臂（推荐 20）后才讨论 canary；自动收集可持续，生产变更继续有门。

## 6. 不采用

- 不重写 Partner 的 Python 根基、QQ/browser 交付、五实例和双槽合同。
- 不复制四者的工具、TUI、provider、Gateway、多 agent 或持久层实现。
- 不把仓库 present/indexed 写成 integrated，不把一条漂亮 memory/skill 当作进化成功。
- 不因这次接入重启 Campaign、Research Loop、自动 cron 或无人监督 production mutation。

固定版本和许可证见 `third_party/harness_design_references.md`；决策见 ADR 0006；机器索引见 workspace
`share/mind/external/catalog.json`。
