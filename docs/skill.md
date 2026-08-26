# Partner Agent 功能清单

## 2026-08-26 新增：Episode 级离线学习与受控 Canary（实验）

- 可把一个持久 task 的 planner/model/tool/artifact/Receipt/交付证据归约为 Episode Trace v3。
- 可计算 truth、business progress、handoff、observability、efficiency、safety 六维奖励。
- 可在 shadow 聚类失败、登记版本化 Candidate Skill，并把历史 baseline 投影与真实 canary 分开统计。
- 手动任务终态自动 best-effort 归约；失败任务也保留。Candidate 不能自动修改或晋升 production。
- 首个 preflight 候选已有 10 个 baseline Episode 与 17 个真实 candidate Episode，其中 4 次完整合格；
  当前 arm 尚未 feature-isolated，所以仍为 `status=canary`、`production_effective=false`。

## 一、核心能力

### 1. 自主任务规划与执行

**功能**: 接收自然语言任务，LLM 生成 batch_plan 执行计划，harness 逐步执行
**代码**:
- 入口: `partner/__main__.py`
- 计划生成: `partner/planner/prompt_builder.py`
- 执行引擎: `partner/mind/executor.py` (batch_plan handler)
- 步骤调度: `partner/mind/harness.py`

**流程**: 用户消息 → routing classify → batch_plan → harness 执行 → LLM_CHECK → 完成/重试

---

### 2. Research Loop 自主循环（替代已删除的 OODA）

**功能**: 自主生成研究任务、执行、学习、继续循环
**代码**: `partner/mind/research_loop.py`

**特点**:
- 质量门控: 最大 5 轮、多样性检查（同类型连 3 次停）、产出验证
- 研究意图判断: 研究类任务循环，一次性动作（截图/列表）直接返回
- 累积知识: 每轮产出归档 share/knowledge/{id}/，下一轮注入上轮摘要（v1→v2→v3）
- 直接 enqueue: 不经过 desktop_inbox，避免消息流冲突
- 协议续跑: 对已定义的研究链，报告中的下一步直接转成新任务
- 证据边界: 无新数据/新假设时明确停止，不用重复动作伪造“持续迭代”
- 结构化承接: 每轮先保存 IterationReceipt/NextAction，收到真实 task ID 后才声称 queued
- 声明式协议: 01/02 的阶段转换来自 `partner/protocols/*.json`；累计轮次可跨协议周期递增

---

### 3. 自愈引擎 (Self-Healing v2)

**功能**: 任务步骤失败时自动诊断→提取技能→尝试修复→重试
**代码**: `partner/evolution/self_heal.py`

| 组件 | 说明 |
|------|------|
| SkillBank | SQLite 持久化修复技能库 |
| SelfHealEngine | 诊断 + 技能提取 + 修复执行 |
| Skill Card | CATEGORY/PATTERN/ROOT_CAUSE/FIX_ACTION 结构化格式 |

**修复类型**:
- `params`: 调整调用参数重试
- `env`: pip install 缺失依赖
- `config`: 修改 YAML 配置文件
- `code`: 标记需 Hermes 协助修改

**集成点**: `executor.py` — core_step_failed 时先自愈再 break

---

### 4. 网络搜索

**功能**: DuckDuckGo 网页搜索
**代码**: `partner/v2/outer_loop.py:atomic_v2_web_search()`

**使用方式**: batch_plan 中写 `web_search` 步骤

---

### 5. QQ Bot 消息推送

**功能**: NapCat WebSocket 连接 QQ，收发消息、推送文件
**代码**: `shells/frontend/qq_bot/qq_official_bridge.py`

**实例**:
| ID | QQ App ID | 用途 |
|----|-----------|------|
| 01 | 1904072984 | 可见浏览器与逐步视觉回执（当前暂停） |
| 02 | 1904082527 | 分子评估与连续研究（当前暂停） |
| 03 | 1904095253 | Partner 框架与前端优化（inactive） |
| 04 | 1904095257 | 文献/GitHub 拉取、复现与学习（当前受控槽） |
| 05 | 1904110644 | Agent 自进化探索与验证（当前受控槽） |

---

### 6. 文件推送

**功能**: PDF/PNG/CSV/MD 等文件通过 QQ 发送
**代码**: `partner/__main__.py:_push_file_to_last_user()` + `partner/v2/push_events.py`
**成功语义**: 运行时渠道 callback 返回真实确认；写 `delivery_queue.jsonl` 只是记录，不算发送成功

---

## 二、工具与 Agent

### 已集成 Agent

| Agent | Manifest | Wrapper | 功能 |
|-------|----------|---------|------|
| pocketflow | `agents/manifests/pocketflow.json` | `agents/wrappers/pocketflow_wrapper.py` | 基于蛋白口袋的分子生成 |
| cytobridge | `agents/manifests/cytobridge.json` | `agents/wrappers/cytobridge_wrapper.py` | 单细胞轨迹推断 |
| bionemo | `agents/manifests/bionemo.json` | 无 wrapper | BioNeMo 生物模型 |
| bioinformatics | `agents/manifests/bioinformatics.json` | 无 wrapper | 通用生信分析 (RDKit 等) |
| enrichment | `agents/manifests/enrichment.json` | `enrichment_wrapper.py` | 通路富集分析 (gseapy/Enrichr) |
| plink | `agents/manifests/plink.json` | `plink_wrapper.py` | GWAS 关联分析 (PLINK 1.9) |
| iqtree | `agents/manifests/iqtree.json` | `iqtree_wrapper.py` | 系统发育建树 (IQ-TREE 2.4) |
| bcftools | `agents/manifests/bcftools.json` | `bcftools_wrapper.py` | 变异位点分析 (bcftools 1.19) |
| diffexp | `agents/manifests/diffexp.json` | `diffexp_wrapper.py` | 单细胞差异表达 (scanpy) |

**调用方式**: batch_plan 中写 `call_agent_skill(agent_name)` 步骤

**代码位置**:
- 分发: `partner/skills/external_agent_skills.py`
- 预检: `partner/agents/preflight.py`
- 注册: `partner/agents/registry.py`

---

### 可用事件 (Events)

**代码**: `partner/v2/` 目录下各模块

| 模块 | 事件数 | 核心事件 |
|------|--------|---------|
| outer_loop.py | web_search, github_search, knowledge_record | DuckDuckGo 搜索 |
| capability_events.py | capability_inventory | 能力盘点/学习计划 (C3 补缺动作) |
| gap_events.py | ensure_tool | 工具保障: 检测/自动下载补缺 (C3) |
| vision_events.py | read_image | qwen VL 读图核查 (截图内容检查) |
| browser.py | browser_open, xiaohongshu_open_publish_editor, xiaohongshu_inspect_upload_requirements | 可见浏览器、前台操作、逐步截图 + qwen 描述 + 真实发送 |
| push_events.py | send_user_text, push_files | 文字/文件真实交付、回执验收和内容去重 |
| pdf_events.py | generate_detailed_pdf | Unicode 中文、分页、图表、长度/章节/证据质量门 |
| molecular_events.py | molecular_generation_benchmark | 候选分子生成、RDKit 有效性与 QED 评估 |
| molecular_diversity_events.py | molecular_diversity_benchmark | Bemis–Murcko 骨架和 Morgan 指纹多样性 |
| molecular_iteration_events.py | molecular_synth_baseline_benchmark, molecular_goal_optimization_benchmark | SA/随机基线对照与 QED/SA 多目标候选选择 |
| perception.py | screenshot, ocr, ui_detect, hardware_info | 桌面感知 |
| control.py | mouse, keyboard, clipboard, app_control | 桌面操控 |
| media.py | chart, screenshot_annotate, visual_report | 图表生成 |
| multimodal.py | web_fetch, image_analyze, image_compare | 多模态分析 |
| loop_engine.py | goal_parse, rolling_plan, gap_detect | 循环控制 |
| capability_events.py | capability_inventory, write_design | 能力盘点 + 写总设计 |
| governance_events.py | select_context, record_iteration, request_next_action, record_issue, start/decide_evolution_experiment, observe_evolution_signals | 分级上下文、项目收据、运行信号与进化 promotion gate |
| campaign_events.py | campaign_status, create/enqueue/pause/cancel_campaign | 持久长跑 Campaign 的受控管理事件 |

---

## 三、自进化体系

| 模块 | 文件 | 功能 |
|------|------|------|
| 自进化主循环 | `evolution/self_evolve_engine.py` | 搜索→分析→方案→执行→评估 |
| 自愈引擎 | `evolution/self_heal.py` | 失败诊断 + 技能提取 + 修复 |
| 架构自描述 | `evolution/self_description.py` | 输出当前架构结构化 JSON |
| 架构映射 | `evolution/architecture_mapper.py` | 分析外部架构→差距分析 |
| 架构改进 | `evolution/architecture_improver.py` | 执行 config/code/prompt 修改 |
| 技能进化 | `evolution/skill_evolver.py` | 创建→检测→改进技能 |
| 进化数据库 | `evolution/evolution_db.py` | 进化记录持久化 |
| 质量评估器 (C1) | `evolution/evaluator.py` | 任务产出量化打分 0-100（文件/非空/非模板/实质内容） |
| 失败反思库 (C2) | `evolution/evaluator.py` | 失败模式自动沉淀 → 下一步决策注入（Reflexion 式） |
| 缺口自动补缺 (C3) | `evolution/gap_filler.py` + `v2/gap_events.py` | 工具检测/自动下载/手动指引 + ensure_tool 事件 |
| 技能卡片 (C4) | `evolution/evaluator.py` | 成功经验沉淀 share/mind/skill_cards.jsonl → 决策注入（Voyager 式） |
| 能力盘点 | `evolution/self_review.py` | 会什么/缺口/学习计划（token 匹配，缺口 19→真实缺口） |
| 治理状态机 | `governance/evolution_loop.py` | Issue 去重→候选实验→标准验证→promotion/rollback |
| 运行信号 | `governance/signal_detector.py` | 从明确失败、缺产物、缺交付和重复事件中产生可核验证据 |
| Episode reducer | `governance/episode_trace.py` | observe-first 原始事件归约、失败分类与六维不可补偿奖励 |
| Candidate registry | `governance/candidate_skills.py` | 版本、来源、适用边界、反例、回滚与显式晋升合同 |
| Shadow replay | `governance/shadow_replay.py` | 历史反事实投影与真实 candidate canary 分臂统计 |

## 四、上下文与实例治理

- `governance/context_selector.py` 根据 `docs/catalog.yaml` 在字符预算内选择 L1/L2/L3；L4 历史默认不加载。
- `governance/project_loop.py` 保存项目状态、轮次收据和队列确认，防止“写了下一步”等同“执行了下一步”。
- `governance/scheduler.py` 把最多两个活动实例作为硬门，而不是仅写在 prompt 里的建议。
- `scripts/partner_control.py switch 01 02` 是切换双实例槽位的运维入口。
- `governance/campaign.py` 把数小时/整夜目标拆成可恢复 WorkItem，执行租约、双槽、预算、watchdog、QQ 报告和项目/进化返回路径。
- `scripts/partner_campaign.py start ... --detach` 让 Campaign 脱离外部 Agent 会话继续运行。
- Campaign 完成业务 WorkItem 后把产物归档为 EvidenceManifest，并由 Receipt 提出可执行 NextAction；
  RL v2 会实际选择 baseline/candidate 下一事件，达到双臂证据门后才允许 PromotionDecision。
- Campaign 直接业务路径具备确定性的 started/executed/finished 用户进度回执；文件投递合同统一归一化后再生成收尾结论，缺阶段回执时验收失败。
- continuous 项目报告按实例使用领域 renderer；公共 PDF 能力只提供封面、层级、代码块、页眉和页码，避免固定模板覆盖业务表达。

---

## 五、Benchmark 评估

**代码**: `partner/benchmark/`

| 组件 | 功能 |
|------|------|
| BenchmarkProtocol | 统一评估协议 |
| literature_review | 文献综述 3 任务 |
| data_analysis | 数据分析 2 任务 |
| harness_integration | Hermes 子进程执行 |
| learning_integration | 结果→learning.db |

---

## 六、多模态感知 (v2)

| 能力 | 代码 | 后端 |
|------|------|------|
| 桌面截图 | `v2/perception.py` | pyautogui → mss → PowerShell CopyFromScreen |
| 文字识别 | `v2/perception.py` | pytesseract → Windows OCR API |
| GUI 操控 | `v2/control.py` | pywinauto (Windows UIA) |
| 浏览器自动化 | `v2/browser.py` | Playwright |
| 视觉分析 | `v2/multimodal.py` | Ollama llava/moondream |

---

## 七、当前限制

| 限制 | 说明 |
|------|------|
| 模型 | 文本和长生成按 purpose 路由；读图使用 qwen 视觉模型，视觉结论仍需结构化证据交叉验证 |
| 代码修复 | 需 Hermes 子进程协助，不能自修改 |
| 分子对接 | 未集成 (Amber/MMPBSA 待配置) |
| 多实例协同 | 知识可归档到 `share/knowledge/`，但当前没有通用的多实例任务协同编排 |
| 浏览器视觉回执 | 已在小红书特定协议中强制，尚未泛化到每个浏览器事件 |
| 分子研究 | 已完成本地四轮评估；缺目标活性/对接/实验数据，不宣称真实药效优化 |
| Embedding 检索 | 自愈技能检索用关键词匹配(非 embedding) |

---

*最后更新: 2026-08-26（Episode/Shadow/Canary 五阶段）*
