# Partner 外部资料学习与采用状态

**基线日期**: 2026-08-23
**权威性**: 当前（L2）

## 1. 三种状态不得混淆

- `present`：文件在 `/mnt/e/work/partner_workspace/external` 存在。
- `indexed`：Partner 记录了路径、大小、SHA256 和候选用途。
- `integrated`：已有可执行接口、针对性测试、实机证据和 promotion decision。

文件存在或被 LLM 总结绝不等于已集成。历史文档中“OODA 是 SESA Proposer”、
“03/05 并行就是 Polar rollout”和“success_count 即 RL”的说法均不再作为当前事实。
OODA 已删除；实例并行也不等于完整的 RL rollout 系统。

## 2026-08-24 TargetDiff split 来源

- 作者仓库：<https://github.com/guanjq/targetdiff>，README 声明数据文件与生成合同。
- 作者 Google Drive 在本次访问时返回 HTTP 404，未伪装成已下载成功。
- 替代镜像：<https://zenodo.org/records/17107488>，只下载 15.3 MB `split_by_name.pt`，未下载 1.6 GB 数据包；MD5 与 Zenodo 记录一致。
- 状态：`present + checksum_verified + structurally_audited + experiment_consumed`；来源仍标注 mirror，不提升为作者官方托管。

## 2. 已策划的自进化/RL 核心源

| 源 | 真实路径 | 当前采用 | 状态 |
|---|---|---|---|
| Polar Agentic RL | `external/literature/Polar Agentic RL on Any Harness at Scale.pdf` | harness / trajectory / evaluator 分层，异步 rollout 设计 | indexed, design reference |
| RLVR-World | `external/code/RLVR-World-main/README.md` | 任务特定的可验证奖励 | indexed, design reference |
| SESA | `external/code/SESA-Self-Evolving-Search-Agents-master/README.md` | 失败队列、技能卡、Proposer/Solver 分离 | indexed, design reference |
| JIT-RL | `external/literature/Just-In-Time Reinforcement Learning Continual Learning in LLM Agents Without Gradient Updates.pdf` | 不更新模型权重的经验复用 | indexed, design reference |
| DeepSeek Harness | `external/code/deepseek-harness/docs/architecture.md` | durable/live 事件分离、可重放 Session、tool pipeline、可检测压缩边界 | indexed, design reference |
| OpenAI Codex | `external/code/openai-codex/codex-rs/rollout-trace/README.md` | raw evidence→离线 reducer、模型可见/运行时分离、thread store、exec policy | indexed, design reference |
| Hermes Agent | `external/code/hermes-agent/agent/trajectory.py` | memory/检索/候选 Skill、observer 关联与上下文压缩 | indexed, design reference |
| OpenClaw | `external/code/openclaw/docs/agent-runtime-architecture.md` | Gateway/session/transcript 权威、分级记忆、cron 隔离 | indexed, design reference |

机器目录由 `partner/governance/external_catalog.py` 生成到
`share/mind/external/catalog.json`。当前 8/8 策划源已找到并哈希，集成数仍记为 0；
这是有意的证据边界。

四套 Harness 的官方仓库、固定 revision、许可证、逐项学习结论和不采用边界见
`docs/architecture/harness_reference_adoption.md` 与
`third_party/harness_design_references.md`。浅克隆只用于阅读，没有执行安装脚本，
也没有把 TypeScript/Rust 源码复制进 Partner。

## 3. 已落地的借鉴

Partner 新增的离线 RL 层不微调 LLM 权重，而是将持久化 Campaign WorkItem 转成
`state -> action -> outcome -> reward` 轨迹：

- 正证据：最终验收、新产物、QQ 真实送达、有 `resume_event` 的受控阻塞。
- 负证据：缺产物、缺交付、重试、超时、watchdog 和失败终态。
- 策略：保守的离线 contextual bandit，输出始终是 candidate。
- 晋升：样本数、均奖励和成功率达标只允许 canary；生产仍必须通过
  `EvolutionExperiment -> PromotionDecision`。

实现见 `partner/governance/rl_evolution.py`，设计见
`docs/architecture/rl_evolution.md`。

## 4. 两小时运行的实际学习结果

`campaign_46a3b906ffee` 的 10 个非报告终态已转换为轨迹。当前唯一正奖励的
确定性方案是 02 `molecular_data_readiness_audit`（奖励 0.32）；
01 本轮 Campaign 任务、03/04 泛化规划和 05 的多个实验任务均为负奖励。
没有任何动作达到 canary 门槛。

这个结果不是“RL 已练好”，而是第一次将“为什么下一轮要换策略”变成可重算的证据。

## 5. 其他外部项目

AI2BMD、Biomni、CytoBridge、ViSNet、Amber/MMPBSA、PocketFlow 和 TargetDiff 均保留在
`external/`。它们属于 02/04 后续的领域学习候选，未通过单独复现、资源上限、
许可证和回归门槛前，不得被文档写成“Partner 已集成”。

## 6. 为什么不直接运行全量 RL 训练栈

Polar/SESA 类训练流程需要明确环境隔离、大量 rollout、GPU/分布式资源和单独评估。
当前电脑已受制于最多两实例，盲目安装全栈会扩大故障面。先完成与这些研究兼容的
轨迹、奖励、评估和晋升接口，之后才能在隔离设备上替换训练器。

## 7. 2026-08-26 四 Harness 统一与生产样本纠正

- DeepSeek Harness、OpenAI Codex、Hermes Agent、OpenClaw 已固定版本并统一为 Partner 自己的
  Episode Trace v3 设计；外部源码没有复制或执行，`indexed != integrated` 仍成立。
- 重新以 Episode 粒度审查任务 `45cbe78a-bc36-46a3-9961-02b645baf7d3` 后发现，逐字来源门只证明
  引文来自旧成品，不能证明旧成品对当前运行能力的描述仍为真。最终文件实际由 `create_file` 写成，
  却声称本回合没有 shell/file-write；Receipt `receipt_680db01279ab` 已追加 invalidate。
- 生成时与最终治理时现在都会拒绝和真实写文件事件矛盾的能力声明。首个自进化尝试只在 shadow
  创建 Candidate Experiment，`promotion=false`，不修改 production。
