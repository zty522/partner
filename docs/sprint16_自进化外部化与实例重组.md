# Sprint 16：自进化外部化与实例角色重组

**日期**：2026-08-27 起草；§3.6/§5.4/§7.3/§9.2/§12 在 2026-08-27 22:00+ 多次更新
**状态**：上层设计讨论中；边讨论边追加
**讨论累计决定**（截至当前）：§3.6 LLM API 现状已补全 / §5.4 任务模板草案确认 /
§7.3 01 方案 01-II 确认 / §9.2 05 改定位 + 算力画像确认 / §12 清单更新
**前置**：Sprint 15 五阶段闭环已完成；path-isolated canary 已完成 10 对匹配实跑；
candidate `candidate_preflight_contract_v2` 状态为 `canary`、`production_effective=false`、
`promotion=false`，由用户显式 PromotionDecision 才会晋升。
**本 sprint 范围**：仅做上层设计、定边界、列决策点；不在本 sprint 内做 partner
代码或 prompt 的任何修改；本 sprint 文档本身是讨论载体，会随讨论推进。

## 0. 设计起点（已确认）

1. **03+05 合并成一个"自进化"实例**——因为自进化不仅修自身 bug，还要从外部汲取知识。
   合并后内部**先分两条 task 模板**：
   - `task_internal_bug_fix`：当前 instance_03_minimal_chain_progression.md 的"读→改→验"扩展
   - `task_external_knowledge_ingest`：从 `/mnt/e/work/partner_workspace/external/` 消化 → 生成 candidate skill
   合并后的统一命名/入口/调度细节未决（见 §5）。

2. **对话做 RL 养分**——先**设计新的 trajectory schema**，不沿用 Episode Trace v3 直接套。
   数据源至少含：vscode Hermes 对话、Codex 对话、QQ 对话、内部 dialog_history。
   schema 设计见 §6。

3. **01 = 完整通用 agent**——任意 desktop app + 任意浏览器 + 读任意图片并理解。
   现状是 xhs 运营（专精浏览器+小红书）；这是终态能力，中间阶段未决（见 §7）。

4. **02/04 = 自进化养分生成基地**——任务定位需要重新规划；具体方案未决（见 §8）。
   当前 02=molecular_generation iter=43 迭代探索型；04=literature_github_learning
   iter=31 文献研究型；都已在 RL 闭环里产生 Episode，但 candidate skill 注册路径未打通。

5. **05 = 改任务定位**——不再做生信项目，改做 ollama/gpu 本地模型与硬件。
   当前 05 evidence 账本（agent_self_evolution）+ 05 在跑的生信项目轨迹需要处理
   （迁移/归档/作废/保留）；RL 推理层与本地模型层的关系未决（见 §9）。

6. **消化产物** 是合并后 03 自进化任务的子类型之一（`task_external_knowledge_ingest`）
   ，不是独立一类；位置在 candidate skill registry 还是独立 `skill_cards_external/`
   待 §10 设计。

7. **算力分层**（已确认）：
   - **本地**：Intel Core Ultra 9 185H（CPU16核 + Intel Arc Pro Graphics 共享显存 2GB +
     Intel AI Boost NPU）。能跑小模型推理（qwen2.5 系列量化版 / gemma2:2b /
     phi3:mini）。**不能**跑 >10B 量化、视频/图像生成模型、世界模型训练。
   - **云端通用 LLM** = **MiniMax-M3**（api.json 标"性价比高，默认使用"）。
   - **云端多模态 LLM** = **qwen-image-3.0**（图像生成）+ **qwen3-vl-flash**
     （vision 理解），配置在 api.json 的 `qwen` 条目下。
   - **本地 ollama** = 仅在 05 收到"显式 +ollama 参数"任务时按需启动，
     不主动接管通用 LLM 调度，不进入 production 路径。
   - **缺口**：api.json 当前**没有视频生成 API**（可灵/通义/智谱 video）；
     多模态生成 AI 短剧的"视频片段"层依赖外部 API 补充（待 §12 决策）。

## 1. 讨论方式与本文档维护规则

- 本文档**只在上层设计层**维护；不写 partner 代码改动、不写 prompt 细节、不写
  部署脚本；这些落地物在确定后写入对应 ADR、operations、architecture 子文档。
- 每一节 §X 末尾标**当前状态**：⏳ 讨论中 / ✅ 已决定 / 🚧 草案待评审。
- 用户对某节做出"按 A/B/C/D 做"的决定后，把决定写入对应小节并改状态为 ✅。
- 决定之间**保留原始选项**，方便后续追溯和反悔。

## 2. sprint 期间不动的事（边界声明）

- **不动** Sprint 15 已落地的 path-isolated canary 评估器、planner contract、
  `_preserve_candidate_verified_sources`、`load_candidate_skills` glob 修复、
  Bug #38 preflight 修复、Bug #41 pure-text routing 修复。
- **不动**当前五个实例在跑的 systemd 状态、`active_slots=03+02`、QQ 链路、
  pytest 333→339 baseline。
- **不动**18 个 Codex 8/27 未提交 canary 配套改动（harness.py / manual_runtime.py /
  shadow_replay.py / episode_trace.py / batch_planner.py candidate_contract 区域 /
  external_agent_skills.py + 4 docs + 3 tests + 1 untracked test）。
- **不动**任何 production control policy；sprint 16 决定的 candidate 策略全部以
  canary 状态存在，`promotion=false` 维持。

## 3. 当前 partner 自进化 / RL 现状摘要（讨论起点的事实基础）

> 仅列**事实**，不列设计意图；意图由 sprint 16 决定。

### 3.1 自进化基础设施（Sprint 15 + 2026-08-27 增量）

- Episode Trace v3 + 六维 Reward Vector（truth / business_progress / handoff /
  observability / efficiency / safety）。
- truth/safety 不可补偿硬门（任一失败 → `policy_eligible=false` + scalar=0）。
- Candidate Skill Registry（版本/边界/反例/回滚/append-only revision）。
- 6 个 decision key 已登记 + Shadow 评估器。
- 路径隔离 canary 已完成 10 对匹配实跑（experiment_7736f187bcad）。
- candidate: 10/10 completed, truth 10/10, observability 10/10, reward=0.9。
- baseline: 6/10 completed, truth 8/10, observability 6/10, reward=0.538。
- 机器评价：`intervention_isolated=true / sample_gate_passed=true /
  quality_gate_passed=true → ready_for_explicit_decision`。
- 状态：`promotion=false`、`production_effective=false`。
- pytest 基线：333（sprint 15 末）→ 337（Bug #38 + 4）→ **339（Bug #39 + 2）**。

### 3.2 已策划的外部源（external_learning.md §2）

8 个策划源当前为 indexed，未 integrated：

- Polar Agentic RL on Any Harness at Scale.pdf
- RLVR-World-main README
- SESA-Self-Evolving-Search-Agents-master README
- Just-In-Time Reinforcement Learning Continual Learning in LLM Agents
  Without Gradient Updates.pdf
- DeepSeek Harness docs/architecture.md
- OpenAI Codex codex-rs/rollout-trace/README.md
- Hermes Agent agent/trajectory.py
- OpenClaw docs/agent-runtime-architecture.md

**集成数仍记为 0**——这是有意的证据边界。

### 3.3 外部资料现状（`/mnt/e/work/partner_workspace/external/`）

- `code/`：13 个项目（AI2BMD / Aether / Biomni / CytoBridge / RLVR-World /
  SESA / ViSNet / amber / deepseek-harness / hermes-agent / mmpbsa /
  openai-codex / openclaw / win-gui-test-skill）。
- `literature/`：33 篇 PDF（含 CellVoyager / Evo1 / Evo2 / Polar / RLPF /
  RLVR-World / Self-Play / SciQu / SP140 / Transformer / Trustworthy AI 等）。
- `content/`：inbox.jsonl。
- `tools/`：bcftools / iqtree / plink。
- `experiments/`：SESA-live。
- `others/`：2 个中国动物学会/会议 PDF。
- `wechat_share/`：10 个微信文章 PDF（含清华 Loop+Harness 自进化 Agent 解读、
  阿里 Qwen 神级自进化 Skill、上海 AI Lab PaperFlow 等）。
- 顶层还有 `PocketFlow / ProRL-Agent-Server-stable / targetdiff / literature_v4.md`。

### 3.4 当前 5 实例状态（dashboard 实测）

```
mode=manual_stable    active=2/2    healthy=2/5    user-ready=2/5
pytest=339 passed
campaign 7f635d0333a9 cancelled (completed=10)

01   active   healthy=True   QQ=ready  xiaohongshu iter=23         ✓ completed
02   active   healthy=True   QQ=ready  molecular_generation iter=43 ✓ completed
03   inactive healthy=False  QQ=error  partner_framework iter=68     completed
04   inactive healthy=False  QQ=error  literature_github iter=31     completed
```

active_slots 当前 = `["03", "02"]`（不是默认 `01/02`）。

### 3.5 当前 share/ 与外部消化基础设施（已存在）

- `share/evidence/`：5 个 evidence 账本（xiaohongshu_operations /
  literature_github_learning / molecular_generation /
  partner_framework_frontend / agent_self_evolution）—— **agent_self_evolution
  已经是独立 evidence 账本**。
- `share/projects/`：~300 个项目目录（含 8 个 `agent_self_evolution` 任务，
  含 `partner 自身结构改进完善_分析` r1-r4 反复，含
  `分析 PocketFlow_r1_r2_r3_r4`、`研究 targetdiff_r1...r4`）——说明之前 03
  实例已经做过"消化外部代码库 → 写报告"的事，但没有真正变成 candidate skill。
- `share/mind/external/` 已经有 5 个子目录（external / governance / system /
  user / skill_cards.jsonl）—— 已有外部治理结构。
- `share/mind/governance/rl/` 12 类轨迹/策略/评估目录。
- `share/mind/governance/experiments/` 15 个 experiment JSON。
- `share/mind/governance/episodes/` 已归约多个 episode。
- `share/mind/governance/experiment_observations/experiment_7736f187bcad/` 16
  个 observation 文件（path-isolated canary 配对样本）。

### 3.6 LLM API 现状（`/mnt/e/work/partner_workspace/config/api.json`）

| API | 用途 | 当前 model | key 状态 | 备注 |
|---|---|---|---|---|
| minimax | 通用 LLM（默认） | MiniMax-M3 | ✅ 已配置 | "性价比高，默认使用" |
| qwen | 多模态 | qwen-image-3.0 + vision_model=qwen3-vl-flash | ✅ 已配置 | 阿里云百炼（图像生成 + 视觉理解） |
| deepseek | 通用 | deepseek-v4-flash | ✅ 已配置 | 备注"涨价了，暂时不用" |
| openai | 通用 | - | ❌ 空 | 未配置 |
| anthropic | Claude | - | ❌ 空 | 未配置 |
| zhipu | 智谱 GLM | - | ❌ 空 | 未配置 |
| moonshot | Kimi | - | ❌ 空 | 未配置 |
| gemini | Google | - | ❌ 空 | 未配置 |
| 视频生成（可灵/通义/智谱 video 等） | 视频片段 | - | ❌ **缺口** | 多模态 AI 短剧需要补充 |

**partner_config.json** 关键字段（`/mnt/e/work/partner_workspace/config/partner_config.json`）：
- `runtime.mode = manual_stable`
- `automatic_campaigns / automatic_iteration / automatic_self_heal / autonomous_cron` 全 false
- `agent.backend = hermes`
- `agent.dynamic_ollama = {}` / `agent.ollama_pool = {}`（空，未启用）
- `scheduler.interval_minutes = 30`、`max_tasks_per_cycle = 1`

**当前 ollama 状态**：
- ollama binary 已安装 `/home/os/.local/bin/ollama`
- ollama server **未启动**（probe status = "ollama_only_simple_direct_reply" 或
  "disabled_or_purpose_not_allowed"）
- `partner/ollama_pool.py` 已实现 4 模式 pool（off/lite/project/all）
  + `adapter.py` 已集成 ollama 路由（purpose ∈ {classify, action_think, report}）
  ，但 production 路径默认走云端 LLM，ollama 是 best-effort fallback

## 4. 设计目标（待你确认）

**目标 A**：合并后的 03+05 实例承担 Partner 自进化的全部职责——
修内部 bug + 消化 external/ + 持续学习真实使用痕迹（对话、运行）。

**目标 B**：自进化的养分从"只来自 partner 任务轨迹"扩展到三源——
①任务轨迹（现状） ②对话记录（vscode hermes / codex / qq，待新 schema）
③外部知识（external/，待 ingest pipeline）。

**目标 C**：01 → 完整通用 agent，02/04 重新规划，05 改定位；
实例间的能力栈重新分配后，每个实例的 task 边界清晰、不重复。

**目标 D**：所有 candidate 仍走"canary + promotion=false + 用户显式决策"
路径；不绕过 Sprint 15 已定的 RL 边界。

⏳ 状态：以上目标 A-D 是讨论起点，等你确认或修改。

## 5. 议题：03+05 合并与 task 模板定义

⏳ 状态：讨论中。

### 5.1 当前两个实例的能力差异（事实）

| 能力 | 03 | 05 |
|---|---|---|
| 改 partner 代码 | ✅（已修 Bug #38/#39） | ❌（observer only） |
| 读 partner 代码 | ✅ | ✅（只读 governance 目录） |
| 读 external/ | ✅ | ❌ |
| 改 production control policy | ❌ | ❌ |
| 自动 promotion | ❌ | ❌ |
| 自动 PromotionDecision 生成 | ❌ | ✅（candidate 创建后产出建议） |
| Candidate Experiment 创建 | ❌ | ✅ |
| 写 Episode v3 | ✅（经由任务治理） | ✅（shadow 路径） |
| 当前项目 | partner_framework iter=68 | agent_self_evolution（生信产物） |

### 5.2 合并候选方案

**方案 X：完全合一**——03/05 合并为单一实例，承担 5 种任务类型
（internal_bug_fix / external_knowledge_ingest / candidate_skill_review /
experiment_synthesis / promotion_decision_draft），由 planner 统一调度。

优点：单一实例身份统一，长期学习一致。
缺点：能力栈跨度大，单实例 planner 复杂度上升；
candidate_skill_review 和 promotion_decision_draft 都是"观察+决策"，
internal_bug_fix 和 external_knowledge_ingest 是"读+改+写"，
合并后 task 状态机会多，governance 复杂度上升。

**方案 Y：内部双模板**（你已选方向）——合并后实例内部保持两条 task 模板：
① `task_internal_bug_fix`（沿用 instance_03_minimal_chain_progression.md 框架）
② `task_external_knowledge_ingest`（消化 external/）。
candidate_skill_review / experiment_synthesis / promotion_decision_draft
作为共享子能力，由合并实例**在两种 task 完成时按需触发**，不单独作为顶层 task 类型。

优点：合并实例身份 + task 状态机少 + governance 复杂度可控。
缺点：review/decision 的可观测性需要嵌入 task 模板，不能单独观测。

**方案 Z：合并但保留内部 project 类型**——实例合并，但 project_id 仍分两类
（project_id=internal_bug_fix vs project_id=external_knowledge_ingest），
和现有 04 的 literature_github_learning project 结构对齐。

这是方案 Y 的具体实现版本，待你确认是否一致。

### 5.3 合并后实例的命名

⏳ 待你定：
- 保留实例号 03（语义"自进化"）还是改成新号（如 06）？
- 合并后 systemd unit 名（`partner-03.service` 还是 `partner-self-evolution.service`）？
- 合并后实例在 dashboard / heartbeat / dialog_history 的标识字段？

### 5.4 合并后 task 模板细节（方案 Y 草稿）

**`task_internal_bug_fix` 模板**：
- 输入：partner 代码里的一个真实可观察 bug（候选源：之前 Codex/手动修复列表）
- 输出：① 1 个 partner/ 文件修改 ② 1 个针对性 pytest ③ 1 个 fix_explanation.md
- 成功门：pytest 339→N passed（≥339）+ git diff 限定 1-2 文件 + 03 真实发四阶段消息
- 评估器：沿用 Sprint 15 的 path-isolated canary 框架（candidate arm 可选）
- 可观测性：每步 step_start/step_complete + 最终 Receipt + Episode v3

**`task_external_knowledge_ingest` 模板**（**草案**，等 §6/§10 一起细化）：
- 输入：external/ 子目录里的 1 个项目/论文（如 `code/CytoBridge/` 或
  `literature/Polar Agentic RL on Any Harness at Scale.pdf`）
- 输出（三件）：
  ① **digest.md**：结构化消化笔记（项目摘要 + 至少3 个具体技术点 +
  与 Partner 的关联分析 + 借鉴建议 + 真值引用 ≥3 对 source_path/evidence_quote）
  ② **candidate_skill.yaml**：候选 skill 草案（适用边界 / 不适用边界 /
  来源 Episode链接 / 反例 / 成功标准 / 回滚步骤）
  ③ **ingest_report.md**：本次消化过程的元报告（读了哪些文件 / 用了哪些 method /
  用了多少时间 / 失败节点）
- **多模态评估**：含图像/截图/视频帧的外部源，可选调用 `qwen3-vl-flash`（vision）
  做视觉证据核验，证据留 image_hash + vl_extracted_text 双行引用
- 成功门（全部必须满足）：
  - digest.md 字节 > 2000 且能 grep 到 ≥3 对 source_path/evidence_quote 双行
  - candidate_skill.yaml 通过 `load_candidate_skills` 可见（即新 ID 也被读到，Bug #39 修后保证）
  - pytest 全量 ≥339 passed 不退
  - 三件产物真实写盘 + 文件 mtime 在 task 时间窗口内
  - 03+05 合并实例真实发"收到/计划/每步完成/最终结果"四阶段消息
- 评估器：复用 Sprint 15 的 path-isolated canary 框架；candidate arm = "用了 external skill"，
  baseline arm = "未读 external/，纯靠 partner 内部知识"。这样可以测出"消化 external/ 真的有用"。
- 算力分配：消化过程用 MiniMax-M3 通用 LLM；如含视觉证据用 qwen3-vl-flash vision。
  本地 ollama **不**进入此模板（除非 05 显式标记）

**关键前置**：要先把 `external/` 内容做**索引+可达性**预处理（catalog.json），
合并实例才知道哪些源可以选作为输入——这个索引在 external_learning.md §2 已经有设计，
但没落地。

### 5.5 合并的迁移路径

⏳ 待你定：
- 当前 05 的 `agent_self_evolution` evidence 账本怎么处理？
  - 选项 a：保留为合并实例的 evidence 子目录 `share/evidence/agent_self_evolution/`
  - 选项 b：改名 `share/evidence/self_evolution/`
  - 选项 c：归档到 `_legacy/`
- 当前 05 的生信项目轨迹（Horvath / GrimAge / PhenoAge / RNA-seq age
  prediction 一批）怎么处理？
  - 选项 a：保留作为历史 RL 样本（不迁移、不作废）
  - 选项 b：作废并从 RL 评估中排除
  - 选项 c：迁移到 02 的 `molecular_generation` evidence 账本
- 03 的 `partner_framework` evidence 账本保留为 `share/evidence/partner_framework/`
  作为 internal_bug_fix 的子目录

## 6. 议题：对话 trajectory 新 schema

⏳ 状态：讨论中（你已定方向，本节细化）。

### 6.1 四种对话源的当前结构（事实）

| 来源 | 存储位置 | 结构 |
|---|---|---|
| vscode Hermes 对话 | Hermes session DB（FTS5-backed SQLite；`session_search` 可查） | sessions 表 + messages 表（id/role/content/timestamp/tool_calls） |
| Codex 对话 | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | OpenAI rollout JSONL，tool_calls/structured_outputs/turn-based |
| QQ 对话 | `share/evidence/{project}/dialog_history.jsonl` | role/source/channel/content/timestamp |
| 内部 dialog_history | `instances/{N}/state/dialog_history.jsonl` | 同 QQ 结构 |

四种源的**核心差异**：
- vscode hermes = 人↔AI 双人对话，LLM tool 不可见外部
- codex = 人↔AI + agent 多步 tool 调用
- qq = 跨多 AI 代理的多人混合对话
- internal dialog_history = AI↔system 事件 + AI↔user QQ 镜像

### 6.2 现状对齐（Episode Trace v3 不直接适配的原因）

- Episode Trace v3 的输入是 TaskInstance + task_log + step result + Receipt +
  trajectory（task-bound 持久 WorkItem）。
- 对话**没有 TaskInstance**——它是 stream-of-consciousness / interactive。
- Episode v3 的 reward components 假设有明确 task outcome，对话没有 outcome 概念。

### 6.3 新 schema 选项（候选）

⏳ 待你定：

**选项 A：`ConversationTrajectory` 与 Episode Trace v3 平级**

新类型，单独进 `share/mind/governance/conversations/` 目录，schema 包含：
- `conversation_id` / `source`（hermes/codex/qq/internal）/ `participants` /
  `started_at` / `ended_at` / `message_count` / `topic_summary` /
  `tools_used`（hermes/codex 有，qq/internal 多无） / `outcomes`（人工标注
  或 LLM 提取）/ `embedding`（向量检索用）

不直接进 Reward Vector，作为"待评估样本池"由合并后的 03/05 实例
按 task 模板过滤后评估（不是每条对话都做 reward）。

优点：与 Episode v3 解耦，新 schema 不破坏现有 canary 评估。
缺点：与 Episode v3 不互通，RL 链路分两条。

**选项 B：把对话作为 Episode v3 的新 source_kind**

新加 `episode_source_kind=conversation`，Episode v3 框架复用但 schema 增字段
（`participants` / `topic` / `embedding`），reward components 重新设计
（truth 不再适用；business_progress 改写为"是否产生决策/策略输入"）。

优点：RL 链路统一。
缺点：破坏现有 v3 schema；10 对 path-isolated canary 评估器需要兼容；
truth/safety 硬门对对话难以定义。

**选项 C：双 schema，对话 schema 是"营养"、Episode v3 是"成品"**

对话 schema 提供候选样本池；合并后 03 实例按 task 类型过滤
（如"用户说过'修 bug'的对话 + Codex 修 bug 过程"），过滤后的样本进入
一个内部 `task_conversation_distill` task 模板，产出 distillation 报告
（不是新 skill），报告再作为内部 task 的输入（类似 Receipt 的角色）。

优点：对话不直接进 RL，作为 task 准备阶段的素材；
schema 简单；不动 Episode v3。
缺点：需要新增 task 类型。

### 6.4 字段设计细节（按选项 A 的草稿，等你确认选哪个后再细化）

⏳ 待 §6.3 选项确定后细化：

```
conversation_id: uuid
source: "vscode_hermes" | "codex" | "qq" | "internal_dialog"
participants: list[{role, id, display_name}]
started_at: iso
ended_at: iso
message_count: int
turns: list[{role, content_ref, timestamp, tool_calls?}]

# 提取层
topic_summary: str          # LLM 提取
decision_points: list[{turn_idx, decision_text, context_ref}]
learnable_patterns: list[{pattern_text, evidence_ref, generalization}]
outcomes: list[{outcome_type, evidence_ref}]   # 人工或 LLM 标

# 检索层
embedding: list[float]      # 用于"找类似对话"
tags: list[str]             # "bug_fix" / "design_discussion" / "config_change" / ...
project_links: list[str]    # 关联到 share/projects/ 路径
episode_links: list[str]    # 关联到 share/mind/governance/episodes/ 路径

# 元数据
schema_version: "1.0"
created_at: iso
created_by: instance_id
```

## 7. 议题：01 = 完整通用 agent 的阶段设计

⏳ 状态：讨论中。

### 7.1 当前能力（事实）

- `app_*` 系列：app_focus / app_close / app_launch / app_list /
  app_list_elements / app_list_windows / app_screenshot_window /
  app_send_keys / app_click_element。
- `browser_*` 系列：browser_open / browser_click / browser_execute /
  browser_extract / browser_navigate / browser_press / browser_scroll /
  browser_snapshot / browser_type / browser_vision。
- `read_image`（vision）。

但 03 实例任务模板**显式禁用** `app_*`（"01 XHS 专属"）；说明能力是平台级有，
实例级策略未开放。

### 7.2 "完整通用 agent"的真实差距

⏳ 待你确认这是不是真实差距：

| 维度 | 当前 | 完整通用所需 |
|---|---|---|
| 任意 desktop app 操作 | 部分（app_* 有但仅 01 在用） | ✅ 任意 app + 视觉理解 |
| 任意浏览器操作 | ✅（browser_* 较完整） | ✅ |
| 读图片理解 | ✅（read_image） | ✅ |
| 多显示器管理 | ❌ | ✅ |
| 任意文件格式解析 | 部分（PDF/Excel/Word 有） | ✅ |
| 屏幕录像/回放 | ❌ | 视需求 |
| 跨 app 数据流转 | ❌ | ✅ |
| 操作系统级 hook | ❌ | 视安全边界 |
| 长时间无人值守 | ❌（systemd Restart=on-failure） | 视场景 |

### 7.3 阶段化候选

⏳ 待你定：

**方案 01-I：维持现状小步扩**
- 保留 xhs 主营；扩展到其他 web SaaS（如飞书/钉钉/Notion）；
- 限制 browser_* 范围；先不扩 desktop app。
- 阶段：1-2 个 sprint。

**方案 01-II：分阶段通用化**（**已确认**）
- Phase 1：浏览器扩展到任意 SaaS + 内容平台
- Phase 2：视觉驱动 desktop app（UIA/COM 等）
- Phase 3：跨 app 数据流 + 操作系统感知
- 阶段：3-5 个 sprint。
- **算力**：vision 调用 `qwen3-vl-flash`（云端 vision，已配置），不是本地 vision。

**方案 01-III：激进一次到位**
- Phase 1 直接把 desktop app 全开放（不区分 web/app 边界）；
- 01 实例作为"完全开放实验场"，但每个 app 都先在 sandbox 验证
- 阶段：长期，每个 sprint 扩 1-2 个 app 类型

### 7.4 与 03 外部汲取的关系

01 = 外部知识收集渠道之一（网页截图/读取 SaaS 内容）；
与 §5 的 task_external_knowledge_ingest 的关系：
- 选项 a：01 跑"读网页"，03 跑"消化产物"，分工不变
- 选项 b：01 直接产出 candidate skill，跳过 03
- 选项 c：01 跑"读"+"初步整理"，03 跑"精炼"+"注册 skill"

⏳ 待你定。

## 8. 议题：02/04 重新规划

⏳ 状态：你说还没想好；下面列当前事实 + 候选方向供你挑。

### 8.1 当前 02 vs 04 对比（事实）

| 维度 | 02 molecular_generation | 04 literature_github_learning |
|---|---|---|
| iter 数 | 43 | 31 |
| 数据形态 | 分子生成 RDKit/Kaggle | 文献 + GitHub repo |
| 实验类型 | 模型对比 + 指标评测 | 报告/摘要生成 |
| 已完成 Receipt | iter 20-22 一批 | iter 29-31 一批 |
| 在 RL 闭环中的位置 | 已产生 1 个正奖励轨迹（reward=0.32） | 已产生 17 个 candidate Episode（4 completed + 13 失败） |
| 与 03 自进化关系 | 间接（产物进 evidence） | 间接（同上） |
| 用户当前活跃程度 | iter=43 仍在跑 | iter=31，最近 holdout 跑过 |

### 8.2 重新规划候选方向

⏳ 待你挑（可多选）：

**方向 P：保留现状，强化"双形态养分"**
- 02 继续做迭代探索型（多轮对比 + 指标）
- 04 继续做文献研究型（跨源摘要 + 真值核验）
- 两者产物都进 candidate skill 候选池（走 §10 接口）
- 不改 task 模板。

**方向 Q：02 改为领域探索+长程任务型**
- 02 项目周期拉长（如 1 周 1 个完整研究）
- 增加"跨项目承接"机制（如本项目结果作为下个项目输入）
- 02 产物增加 candidate skill 候选的强制注册。

**方向 R：04 改为代码学习+外部汲取型**
- 04 直接对接 external/code/，每个 repo 出一个候选 skill
- 04 项目 = "本周读了哪几个 repo + 学到什么"
- 与合并后 03 的 task_external_knowledge_ingest 重叠，需要消歧（§5+§8 协调）。

**方向 S：02/04 改名为"研究模式A/B"**
- project_id 改为 `research_iterative` 和 `research_synthesis`
- 保持 5 个项目目录结构不变
- 强调"作为 RL 养分生成基地"的统一身份

**方向 T：02/04 引入"显式产物契约"**
- 每个项目结束必须产出：① Receipt ② Candidate Skill 草案 ③ 与现有
  Episode 的明确关联。
- 不产出契约的任务不算 completed。

### 8.3 与 §5（03+05 合并）的边界

⏳ 这部分要一起定：
- 02/04 的产物如果要走 candidate skill 注册，是 02/04 实例自己注册（自荐）
  还是交给合并后的 03+05 实例审核后注册（他荐）？
- 他荐的优点：05 实例 observer 角色保留；02/04 不会被"自利偏差"污染 candidate
- 自荐的优点：链路短，02/04 直接成为养分工厂

## 9. 议题：05 改任务定位 = ollama/gpu

⏳ 状态：讨论中。

### 9.1 当前 05 在跑什么（事实）

- 05 projects/ 下是生信项目（Horvath / GrimAge / PhenoAge / RNA-seq age
  prediction），多个 b1683b80 后缀（同样的 bioinst 任务）。
- 05 evidence = agent_self_evolution 账本（与 03+05 合并后冲突，待 §5.5 决定）。
- 05 当前 systemd 状态：inactive（不活跃）。
- 05 QQ=ready（与 04 不同）。
- 05 dialog_history 主要是自进化/RL 相关。

### 9.2 "05 = ollama/gpu" 的具体定位（已确认）

**05 实例 = 本地推理基础设施管理员（被动响应）**

具体职责：

```
┌─────────────────────────────────────────┐
│ 05 实例 = 本地推理基础设施（被动响应）     │
├─────────────────────────────────────────┤
│ 1. ollama server 生命周期                │
│    - 不默认启动 ollama serve              │
│    - 只有收到 "use_ollama=True" 任务才起   │
│    - 任务结束自动停（节省资源）            │
│                                          │
│ 2. 本地硬件感知                           │
│    - Intel Core Ultra 9 185H（CPU16核）  │
│    - Intel Arc Pro Graphics（共享2GB）   │
│    - Intel AI Boost NPU                  │
│    - 健康指标进 dashboard                 │
│                                          │
│ 3. 候选模型评估                           │
│    - 默认 qwen2.5:7b（GPU/CPU）          │
│    - 备选 gemma2:2b / phi3:mini（NPU）    │
│    - 跑出对比报告作为 candidate skill     │
│                                          │
│ 4. 不接管 production 推理                │
│    - production 路径默认走云端 LLM       │
│    - 只有用户显式 ollama 才走本地         │
└─────────────────────────────────────────┘
```

**本地算力真实边界**（**已确认**，本机 Intel Core Ultra 9 185H）：
- ✅ 能跑：qwen2.5:0.5b/1.5b/3b/7b（量化）+ gemma2:2b + phi3:mini + 小嵌入模型
- ❌ 不能跑：>10B 量化模型、视频生成、图像生成 LoRA、世界模型训练
- Intel Arc Pro Graphics = 共享显存2GB，可走 PyTorch XPU 后端（IPEX）
- Intel AI Boost NPU = 可走 OpenVINO 跑小模型（phi3:mini / gemma2:2b 量化）

**与云端算力的分工**：
- 本地 ollama = 仅在 05 收到"显式 +ollama 参数"任务时按需启动
- 云端通用 LLM = MiniMax-M3（默认所有任务）
- 云端多模态 = qwen-image-3.0（图像生成）+ qwen3-vl-flash（vision 理解）
- 云端视频生成 = **缺口**（api.json 当前没有视频生成 API；多模态 AI 短剧需要补）

⏳ 选项说明保留（已选定为"被动响应"+ Intel Ultra 9 185H + Arc + NPU）：
- 选项 I：本地 LLM 推理层（已选，被动模式）
- 选项 II：仅 GPU 硬件感知（不选，太浅）
- 选项 III：仅本地模型评估（不选，与 §5 重叠）
- 选项 IV：I+III（不选，II 含训练超算力）

### 9.3 与 RL 闭环的关系

如果 05 改定位为 ollama/gpu：
- 05 不再直接产生业务 Episode（生信项目作废/迁移）
- 05 变成**基础设施**——为合并后 03 实例提供本地模型推理能力
- 05 自身不参与"自进化"业务，但被自进化路径消费

⏳ 这条边界**与 §5.5 的 05 evidence 迁移路径直接相关**，建议合并讨论。

### 9.4 调度上的影响

当前 05 systemd unit = `partner-05.service`；
如果 05 改定位，systemd unit 名是否改？
- 选项 a：保留 `partner-05.service`（最小改动）
- 选项 b：改 `partner-local-models.service`（语义清楚）
- 选项 c：拆成 `partner-ollama.service` + `partner-gpu.service`（更细）

⏳ 待你定。

## 10. 议题：消化产物的位置（candidate skill registry vs 独立目录）

⏳ 状态：你说"后面要设计"；本节先列候选位置供你未来挑。

### 10.1 候选位置

| 位置 | 描述 | 优 | 缺 |
|---|---|---|---|
| candidate_skills/ | `share/mind/governance/rl/candidate_skills/`（现状） | 已有 evaluator/registry/版本控制；Bug #39 修后 glob 行为正确 | 与 existing RL 评估器耦合；外部 ingest 的 candidate 是否走同一 canary 流程待定 |
| skill_cards_external/ | `share/mind/external/skill_cards_external/`（新增） | 与现有 external 治理结构对齐；schema 可不同 | 需要新建 evaluator；与 RL 闭环不通 |
| external/skill_drafts/ | `share/mind/external/external/skill_drafts/`（在 share/mind/external/external/ 下） | 紧贴 external/ 原始资料；metadata 完整 | 与 partner/governance 评估器脱节 |

### 10.2 schema 差异化

- candidate skill（partner_framework 学习成果）：结构化能力声明 + 适用边界 + 测试
- skill card（外部学习成果）：自然语言摘要 + 候选能力描述 + 待评审标记

两者最后**要不要统一**？统一时机：
- 选项 a：保持差异，05 转换器把 external skill card 转为 candidate skill
- 选项 b：合并 schema，external 消化直接产出 candidate skill

⏳ 与 §6.3 选项有耦合——如果对话 schema 选 B（Episode v3 兼容），
外部消化 candidate 也应选 a（转换器）；如果选 A 或 C，外部消化 candidate 可选 b。

## 11. 议题：跨议题依赖与建议讨论顺序

⏳ 状态：建议（待你认可）。

依赖图（谁依赖谁）：
```
§5 (03+05 合并)
   ├─→ §5.5 (05 evidence 迁移路径)
   ├─→ §10 (candidate skill 接口)
   └─→ §6 (对话 schema，方向相关但不强依赖)

§6 (对话 schema)
   ├─→ §10 (skill card vs candidate skill 统一性)
   └─→ §9 (05 推理层是否消费对话 schema 作为记忆)

§7 (01 通用 agent)  几乎独立
   └─→ §5.4 task_external_knowledge_ingest 是否走 01 收集

§8 (02/04 重新规划)  几乎独立
   └─→ §5.4 + §10 (candidate skill 注册路径)

§9 (05 ollama/gpu)   几乎独立
   ├─→ §5.5 (05 evidence 迁移)
   └─§6.3 (对话 schema 是否作为 05 推理层的检索源)
```

**建议讨论顺序**：
1. §5（合并主线）
2. §6（对话 schema，因为 §5 内部 review 子能力需要）
3. §10（消化产物位置，被 §5+§6 引用）
4. §8（02/04，与 §10 绑定）
5. §7（01 通用 agent，最独立）
6. §9（05 改定位，与 §5.5 绑定）

## 12. 未决问题清单（讨论开始时维护）

> 每个未决问题一行；用户确认后变成 ✅ 决定并入相应节。

### 12.1 已决定（2026-08-27）

- [x] §0-7 **算力分层**：本地 Intel Core Ultra 9 185H（CPU+Arc2GB+NPU）+
  云端 minimax 通用 + 云端 qwen 多模态 + ollama 按需
- [x] §0-7 **多模态 LLM = qwen-image-3.0 + qwen3-vl-flash**
- [x] §0-7 **通用 LLM = MiniMax-M3**
- [x] §5.3 **合并后实例号保留 03**（保留历史轨迹承接）
- [x] §5.4 **task_internal_bug_fix** 沿用 instance_03_minimal_chain_progression.md 框架
- [x] §5.4 **task_external_knowledge_ingest** = digest.md + candidate_skill.yaml + ingest_report.md 三件
- [x] §5.5 05 evidence 账本保留为 `share/evidence/agent_self_evolution/`，生信项目轨迹保留作历史
- [x] §6.3 **对话 schema 选项 C**（双 schema，对话是"营养"）
- [x] §7.3 **01 阶段化方案 01-II**（分阶段通用化）
- [x] §7.4 01 与 03 external_knowledge_ingest 关系 a（01 跑读，03 跑消化）
- [x] §8.2 **02/04 重新规划方向 P+T 复合**（保留双形态养分 + 显式产物契约）
- [x] §8.3 **02/04 candidate skill 注册走他荐**（03+05 审核后注册）
- [x] §9.2 **05 = 本地推理设施管理员（被动响应）**
- [x] §9.2 **本机算力承认 Intel Ultra 9 185H（CPU+Arc+NPU），05 不主动调度**
- [x] §9.4 05 systemd unit 名暂保留 `partner-05.service`（最小改动）
- [x] §10.1 **消化产物位置 candidate_skills/**（沿用现有 canary 评估器）
- [x] §10.2 schema 统一时机 a（保持差异，转换器模式）
- [x] **多模态生成 AI 短剧 = Layer1 文本 + Layer5 评估用 partner 本地；
  Layer2/3/4 走云端 API；归 04**
- [x] **世界模型 = RLVR-World 语言部分归 02；Aether/DeepVerse/CellOS 暂存等算力**
- [x] **02 主线节奏**：分子生成（已有） → age_prediction（02 接手） →
  RLVR-World 语言世界模型（依次）
- [x] **04 主线节奏**：生命科学比赛 → 多模态生成 AI 短剧（依次）

### 12.2 仍待决定

- [ ] **视频生成 API 补充到 api.json**：当前 api.json 没有视频生成 API
  （可灵/通义万相/智谱 CogVideoX/字节豆包/腾讯混元等）。
  多模态生成 AI 短剧的"视频片段"层需要至少一个视频 API。
  - 选项 A：补可灵 API（字节）
  - 选项 B：补通义万相 API（阿里，已用 qwen）
  - 选项 C：补智谱 CogVideoX API
  - 选项 D：暂时跳过视频层，多模态 AI 短剧只做"剧本 + 关键帧静态图集"
- [ ] external/ A1-A5 分档（按项目阶段消化）：
  - A1 = 02 当前主项目（PocketFlow/Pocket2Mol/TargetDiff/ViSNet/SP140）
  - A2 = 02 副项目 age_prediction（Evo1/Evo2/CRISPR-GPT/CellVoyager + `/mnt/e/work/age_prediction/`）
  - A3 = 02 下一阶段 RLVR-World（external/code/RLVR-World-main/ +
    wechat_share "强化学习进阶" + "世界模型登Nature正刊"）
  - A4 = 04 主项目生命科学比赛（Biomni/CytoBridge/CellOS/虚拟细胞/Evo1-2 +
    wechat_share "全球AI智能体生命科学大赛"）
  - A5 = 04 副项目多模态短剧（external 暂无合适源，后续按需拉取）
  - B 档 = 通用参考（清华 Loop+Harness / Qwen 神级自进化 Skill /
    Polar/SESA/JIT-RL / DeepSeek-Harness/Codex/OpenClaw/Hermes-Agent）—— 不主动消化
- [ ] §3.5 Share/mind/external/skill_cards.jsonl 当前不存在（路径在 share/mind/external/
  下但实际未建）—— 消化产物落地的具体目录结构待 §10 进一步细化
- [ ] external/ 内容 catalog.json 索引——external_learning.md §2 已有设计但未落地
- [ ] instance_03_minimal_chain_progression.md 是否改名（合并后项目名待定）
- [ ] 04 生命科学比赛具体注册哪个赛事（"全球AI智能体生命科学科研大赛"vs
  "全球虚拟胚胎挑战赛"——都已在 wechat_share 里，但需要确认是同一个还是不同赛事）

---

*下次更新：用户对某条 ✅ 决定后追加对应小节内容，并改 §12 状态。*
