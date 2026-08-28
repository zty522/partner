# Partner 文档体系

> ⚠️ **维护纪律**：Partner 自进化/自愈引擎每次触发时自动读取这些文档。
> 修改任何 Partner 代码后，必须同步更新对应文档。文档是 Partner 自我认知的唯一来源。

> **当前运行基线（2026-08-28）**：生产默认为 `manual_stable`。用户手动发消息，
> 实例确认收到、逐步汇报并在一次有界任务后停止。Campaign、自动迭代、Research Loop、RL
> 自主循环和自主 cron 仍暂停；04 的一个最终成品真值门已通过受控 canary 显式晋升，但不会自动续轮。先读
> [`architecture/manual_stable_core.md`](architecture/manual_stable_core.md)。

---

## 零、当前进度入口

当前运行状态、已完成闭环、实机证据、已知边界和下一阶段优先级统一见
[`current_status.md`](current_status.md)。其他 Sprint 文档均按历史记录理解，不应用来判断当前服务状态。

**2026-08-28 进展**: 11 个 framework bug 修复（#38-#50） + 13 个 ADR（0007-0019） + commit d536870 已 push 到 origin/main。03 + 05 自主能力从 50%/40% 提升到 75%/85%（9 轮任务验证）。pytest 333 → 351 passed。详见 `current_status.md` 的 2026-08-28 节。

新模型或新开发会话必须先读仓库根目录 `AGENTS.md`，再按
[`handoff/reading_order.md`](handoff/reading_order.md) 加载；禁止一次性把全部历史文档塞进上下文。

---

## 一、自进化与自愈能力范围（历史实验能力，当前默认关闭）

这些模块仍保留用于研究和追溯，但不能在 `manual_stable` 下自动触发，也不能作为普通用户消息的旁路。

### 层面 1：改进 Partner 本身的代码和机制

| 可改进内容 | 方式 | 示例 |
|-----------|------|------|
| prompt 模板 | batch_plan LLM 生成改进版 → 替换 | `prompt_builder.py` 执行策略 |
| wrapper 脚本 | 诊断产出问题 → 生成修复脚本 → 执行 | `pocketflow_wrapper.py` 保存 .smi |
| 事件处理器 | 新增 execute_code 事件 → 注册 handler | `harness.py` execute_code handler |
| 配置参数 | 诊断超时 → 修改 yaml | `external_calls.yaml` 超时调整 |
| 路由/循环门控 | 发现重复或无新证据 → 停止/切换实验 | `research_loop.py` 轮数、多样性和协议门控 |
| 自愈技能库 | 失败 → 提取 Skill Card → 持久化 | `state/skill_bank.db` |

### 层面 2：围绕研究项目的改进（如"分子生成技术创新探索"）

| 可改进内容 | 方式 | 示例 |
|-----------|------|------|
| 研究方法 | web_search 前沿方法 → 对比分析 → 提出改进 | DiffDock vs PocketFlow 对比 |
| 代码实现 | execute_code 写 Python 脚本 → 运行 → 分析结果 | RDKit 分子生成脚本 |
| 实验设计 | 分析失败 → 调整参数 → 重新实验 | 温度参数扫描 |
| 文献分析 | 读取论文 PDF → 提取方法 → 借鉴到项目 | SESA → Skill Bank |
| 外部工具集成 | git clone → pip install → 写 wrapper → 测试 | ViSNet/Amber 集成 |

---

## 二、外部知识获取来源

Partner 在自进化和研究过程中，从以下来源获取知识：

| 来源 | 路径 | 内容 | 使用方式 |
|------|------|------|---------|
| 外部代码库 | `/mnt/e/work/partner_workspace/external/` | PocketFlow, CytoBridge, SESA, ViSNet, Amber, AI2BMD 等 | wrapper 调用 / 代码借鉴 |
| 论文 PDF | `external/literature/` | Self-Play.pdf, VeriSkill.pdf, Polar.pdf, ERA(Nature).pdf | 阅读→提取方法→借鉴 |
| 网络搜索 | web_search (DuckDuckGo) | arXiv, GitHub, 学术搜索引擎 | batch_plan 中自动搜索 |
| 实例运行记录 | `instances/XX/state/logs/` | evolution.jsonl, agent_runs.jsonl, code_runs.jsonl | 分析失败模式与交付证据 |
| QQ 对话记录 | `instances/XX/dialogue/qq_chat_history.jsonl` | 用户反馈、任务结果 | 提取需求、核验用户实际收到的内容 |
| 本文档体系 | `/mnt/e/work/partner/docs/` | 全部文档 | 自我认知、已知问题、修复经验 |

---

## 三、文档地图

```
docs/
├── README.md                    ← 【本文件】顶层说明：能力范围、知识来源、文档地图
├── current_status.md            ← 🔴 当前权威基线：运行状态、实机证据、限制、下一步
├── product_principles.md        ← 🔴 L1 产品原则：过程可见、领域报告、连续推进与防回退
├── catalog.yaml                 ← L0 机器目录：层级、权威性、标签、实例和预算
├── contracts/                   ← Project/Receipt/Issue/Experiment/Context JSON Schema
├── architecture/                ← 知识、项目迭代、自进化和双槽调度的当前设计
│   ├── user_observability_and_reports.md ← 三阶段消息、领域报告与验收硬门
│   ├── manual_stable_core.md       ← 当前生产路径：手动触发、逐步消息、单轮结束、双槽轮换
├── handoff/                     ← 新模型阅读顺序、改动协议与验真规则
├── operations/                  ← Campaign 启动、恢复和长跑验收手册
├── decisions/                   ← 当前架构决策记录（ADR）
├── playbooks/                   ← 可复用操作经验（当前含小红书可见浏览器）
├── projects/                    ← 项目级目标、证据边界和恢复条件
│
├── evolution_journal.md         ← 🔴 活文档：自愈自动追加 + 重大阶段手动记录
│   ├── 预期目标 (North Star)
│   ├── 进化时间线 (阶段0→现在)
│   ├── 当前能力矩阵
│   └── 已知待解决问题
│
├── change_log.md                ← 问题→修复记录（持续追加）
│
├── external_learning.md         ← 外部资料 present/indexed/integrated 证据边界
├── architecture/harness_reference_adoption.md ← DeepSeek/Codex/OpenClaw/Hermes 固定版本、统一设计与禁用边界
├── decisions/0006-unified-harness-episode-learning.md ← 四 Harness→Episode v3→Shadow 的统一决策
├── architecture/rl_evolution.md ← 轨迹、可验证奖励、候选策略与晋升门
├── architecture/project_portfolio.md ← 五项目输入指纹轮转、双槽与 05 波次门
├── operations/campaign_2h_audit_2026-08-23.md ← 两小时失败证据与修复
│
├── sprint1_基础架构.md          ← 历史 sprint
├── sprint2_核心架构.md
├── sprint3_v2扩展系统.md
├── sprint4_集成稳定化.md
├── sprint5_harness增强.md
├── sprint6_自进化与分子生成探索.md
├── sprint7_全追踪与多模态自进化.md  ← 历史 sprint
├── sprint8_设计.md             ← 历史 sprint（Research Loop + 深度研究闭环）
├── sprint9_自我认知与自主学习.md  ← 历史 sprint（强制写设计 + 能力盘点）
├── sprint10_严格测试.md        ← 历史 sprint（分层工程测试）
├── sprint11_执行型持续迭代.md  ← 历史 sprint（五实例执行 profile）
├── sprint12_单项目证据闭环.md  ← 当前 sprint（02 TargetDiff 五阶段→05 里程碑 RL）
├── sprint14_手动受控Canary与真值门晋升.md ← 04/05 六样本实验、显式 PromotionDecision
├── testing_report_sprint10.md ← Sprint 10 测试记录 + 最新回归附录
│
├── architecture_review.md      ← 架构审视：闭环后的差距分析
├── evolution_loop_design.md    ← 自进化闭环设计（读取→修复→验证→记录）
├── self_awareness.md          ← 自我认知：我是谁、已知问题、改进方向
├── partner_code.md              ← 代码结构地图
│   ├── partner/ 目录完整树
│   └── partner_workspace/ 目录树
│
└── skill.md                     ← 功能清单
    ├── 6 大核心能力
    ├── 已集成 Agent 列表
    ├── v2 事件模块
    └── 当前限制
```

---

## 四、文档更新规则

### 自动更新（由 Partner 引擎触发）

| 文档 | 触发时机 | 谁更新 |
|------|---------|--------|
| `evolution_journal.md` | 自愈触发时 | `self_heal.py:_update_evolution_journal()` |
| `state/skill_bank.db` | 自愈提取新技能 | `self_heal.py:SkillBank.add_skill()` |

> OODA 已于 2026-08-12 删除；当前自主续跑由 `partner/mind/research_loop.py` 负责。

### 手动更新（修改代码后必须同步）

| 改了什么 | 必须更新哪个文档 |
|---------|----------------|
| 修复了一个 bug | `change_log.md` — 加一条问题+修复 |
| 借鉴了外部论文/代码 | `external_learning.md` — 加一条借鉴记录 |
| 新增/删除/移动了文件 | `partner_code.md` — 更新目录树 |
| 新增了一个能力 | `skill.md` — 更新功能清单 |
| 完成了阶段性工作 | `sprint*.md` — 更新对应 sprint |
| Partner 有了重大变化 | `evolution_journal.md` — 追加进化时间线条目 |
| 改了治理记录或状态机 | `contracts/*.schema.json` + 对应 `architecture/*.md` |
| 新增项目/操作经验 | `projects/` 或 `playbooks/` + `catalog.yaml` |

### ⛔ 严禁行为

- 修改代码后不更新文档
- 文档内容与代码实际状态不一致
- 用模糊描述代替具体文件路径和行号
- 删除旧条目而不保留历史

---

*最后更新: 2026-08-26（四 Harness 统一、Episode v3、Reward Vector 与首个 Shadow；生产自治仍暂停）*
