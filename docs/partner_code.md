# Partner 代码结构与文件夹说明

## 代码仓库 (`/mnt/e/work/partner/`)

```
partner/
├── partner/                         ← 核心 Python 包
│   ├── __main__.py                  ← 入口: CLI 解析、实例启动、QQ bridge
│   │
│   ├── mind/                        ← 事件循环与任务执行引擎
│   │   ├── executor.py              ← 核心执行器 (500KB): batch_plan 流程、LLM_CHECK、迭代、自愈钩子
│   │   ├── harness.py               ← Harness 调度: 步骤执行、依赖解析、agent 调用
│   │   ├── dispatcher.py            ← Agent 任务分发
│   │   └── research_loop.py         ← 任务完成后的证据归档、协议续跑与停止门控
│   │
│   ├── core/                        ← 核心引擎
│   │   ├── delivery_queue.py        ← 历史交付记录辅助（不代表真实发送确认）
│   │   └── interaction_orchestrator.py ← 交互编排
│   │
│   ├── planner/                     ← 计划生成
│   │   └── prompt_builder.py        ← batch_plan prompt 构建: 上下文注入、习惯、规则、经验
│   │
│   ├── adapters/                    ← LLM 适配器
│   │   └── direct_api.py            ← DeepSeek DirectAPI: 模型切换、fallback、超时管理
│   │
│   ├── evolution/                   ← 自进化模块
│   │   ├── self_heal.py             ← 自愈引擎 v2 (365行): Skill Bank + SESA 风格提取
│   │   ├── self_evolve_engine.py    ← 自进化主循环: 搜索→分析→方案→执行→评估
│   │   ├── evolution_db.py          ← 进化数据库
│   │   ├── skill_evolver.py         ← 技能进化: 创建→检测→改进
│   │   ├── self_description.py      ← 自描述: 输出当前架构结构化 JSON
│   │   ├── architecture_mapper.py   ← 架构映射: 分析外部架构→差距分析
│   │   ├── architecture_improver.py ← 架构改进: 执行 config/code/prompt 修改
│   │   ├── self_review.py           ← 能力盘点/缺口检测: 关键词 token 匹配（修复误报）
│   │   ├── gap_discovery.py         ← 缺口发现（外部工具对比）
│   │   ├── gap_filler.py            ← C3 自动补缺: 工具检测(detect_tool) + 自动下载 + 记录
│   │   └── evaluator.py             ← C1 质量评估器(0-100) + C2 失败反思库 + C4 技能卡片
│   │
│   ├── governance/                  ← 可验证的项目/进化/上下文治理
│   │   ├── models.py                ← 七类结构化记录及跨字段不变量
│   │   ├── storage.py               ← 原子 JSON、JSONL 与项目治理目录
│   │   ├── context_selector.py      ← L0-L4、预算、来源与确定性 fallback
│   │   ├── project_loop.py          ← Receipt、NextAction 与真实 queue ack
│   │   ├── evolution_loop.py        ← Issue、Experiment、promotion/rollback gate
│   │   ├── signal_detector.py       ← 运行结果中的高置信问题信号
│   │   ├── scheduler.py             ← 五角色、最多两个活动实例的硬门
│   │   ├── protocols.py             ← 声明式项目协议运行桥
│   │   ├── campaign_models.py       ← Campaign/WorkItem/Lease/Report 契约
│   │   ├── campaign_storage.py      ← Campaign 持久状态与 runner lock
│   │   ├── campaign.py              ← 长期控制器、恢复、预算、watchdog、报告
│   │   └── campaign_runtime.py      ← inbox dispatch 与 systemd 双槽切换
│   ├── protocols/                   ← 01/02 阶段转换 JSON（代码包内资源）
│   │
│   ├── agents/                      ← Agent 框架
│   │   ├── dispatcher.py            ← Agent 分发器
│   │   ├── registry.py              ← Agent 注册表
│   │   ├── preflight.py             ← 预检: conda env、依赖
│   │   ├── manifest.py              ← Manifest 解析
│   │   ├── server.py                ← Agent 服务管理
│   │   ├── manifests/               ← agent manifest JSON (18个: 健康14 + 未安装4)
│   │   │   └── _stubs_archive/      ← 324 个无端点的 stub (已归档)
│   │   └── wrappers/                ← wrapper 脚本
│   │       ├── pocketflow_wrapper.py ← PocketFlow: 启动 + 结果解析 + 进度跟踪
│   │       ├── cytobridge_wrapper.py ← CytoBridge: 启动 + 结果解析 + 进度跟踪
│   │       ├── enrichment_wrapper.py ← 通路富集 (gseapy/Enrichr, cytobridge env)
│   │       ├── plink_wrapper.py      ← GWAS 关联分析 (PLINK 1.9)
│   │       ├── iqtree_wrapper.py     ← 系统发育建树 (IQ-TREE 2.4)
│   │       ├── bcftools_wrapper.py   ← 变异位点分析 (bcftools 1.19)
│   │       └── diffexp_wrapper.py    ← 单细胞差异表达 (scanpy rank_genes_groups)
│   │
│   ├── v2/                          ← v2 扩展模块 (感知/操控/外循环)
│   │   ├── perception.py            ← 截图(pyautogui/mss/PowerShell)、OCR(pytesseract/Windows OCR)、硬件信息
│   │   ├── control.py               ← 鼠标、键盘、剪贴板、Windows GUI(pywinauto)
│   │   ├── browser.py               ← Playwright 自动化、可见置前、小红书事务、逐步视觉回执
│   │   ├── browser_worker.py        ← 独立 systemd 浏览器 worker 与健康/关闭协议
│   │   ├── media.py                 ← 图表(matplotlib)、截图标注(PIL)、视觉报告(reportlab)
│   │   ├── video.py                 ← 帧截取(opencv)、场景检测、ASR(whisper)
│   │   ├── multimodal.py            ← 网页获取(bs4)、视觉分析(Ollama llava)、对比、生成
│   │   ├── outer_loop.py            ← GitHub 搜索、DuckDuckGo 搜索、知识记录
│   │   ├── loop_engine.py           ← 目标解析、滚动规划、缺口检测、暂停恢复
│   │   ├── capability_events.py     ← 能力盘点/学习计划事件 (capability_inventory)
│   │   ├── gap_events.py            ← ensure_tool 事件 (C3: 工具保障)
│   │   ├── vision_events.py         ← read_image 事件 (qwen VL 读图核查)
│   │   ├── push_events.py           ← 文字/文件真实 callback 交付、回执和去重
│   │   ├── pdf_events.py            ← 中文详细 PDF 生成与内容质量门
│   │   ├── molecular_events.py      ← 候选分子生成与基础评估
│   │   ├── molecular_diversity_events.py ← 骨架和指纹多样性评估
│   │   ├── molecular_iteration_events.py ← SA/随机基线与 QED/SA 多目标迭代
│   │   ├── governance_events.py     ← 上下文、项目收据、问题与进化实验事件
│   │   └── campaign_events.py       ← Campaign 创建、状态、WorkItem 和暂停/取消
│   │
│   ├── state/                       ← 状态管理
│   │   ├── config.py                ← 配置解析: 统一 config_root
│   │   └── ...
│   │
│   ├── workspace/                   ← 工作区路径
│   │   └── workspace_layout.py      ← 路径解析: workspace_root, outgoing_dir, instance_dir
│   │
│   ├── tools/                       ← 工具模块
│   │   ├── run_log.py               ← 代码运行日志 (code_runs.jsonl, execute_code/run_command)
│   │   └── direct_ops.py            ← 直接操作 (capture.ps1 截图等)
│   │
│   ├── skills/                      ← 技能模块
│   │   └── external_agent_skills.py ← 外部 agent 调用: _atomic_execute_skill, dispatch
│   │
│   ├── monitoring/                  ← 运行控制与监控辅助
│   │   └── run_control.py           ← 持久化 pause/resume 状态
│   └── benchmark/                   ← Benchmark 评估框架
│       ├── protocols/               ← 协议: literature_review, data_analysis
│       ├── harness_integration.py    ← Harness 执行引擎
│       └── learning_integration.py   ← 结果 → learning.db
│
├── shells/                          ← QQ Bot 前端（QQ Official WebSocket bridge）
│   └── frontend/qq_bot/
│       └── qq_official_bridge.py    ← QQ Bridge: WebSocket 连接、消息收发
│
├── docs/                            ← 文档
│   ├── catalog.yaml                 ← 分级加载的机器目录
│   ├── contracts/                   ← 治理 JSON Schema
│   ├── architecture/                ← 当前状态机和知识架构
│   ├── handoff/                     ← 后续模型接手约束
│   ├── operations/ + decisions/     ← 长跑运维手册与 ADR
│   ├── playbooks/ + projects/       ← 操作经验与项目事实
│   ├── sprint1_基础架构.md ~ sprint10_严格测试.md
│   ├── external_learning.md         ← 外部知识借鉴记录
│   ├── partner_code.md              ← 本文件
│   └── skill.md                     ← Agent 功能总览
│
├── scripts/partner_campaign.py      ← Campaign CLI/后台 runner
├── scripts/simulate_campaign_soak.py ← 隔离 fake-clock 长跑模拟
├── tests/                           ← 测试代码
└── third_party/                     ← 第三方依赖
```

---

## 工作区 (`/mnt/e/work/partner_workspace/`)

```
partner_workspace/
├── config/                          ← 全局配置（唯一配置位置，实例级不保留 config）
│   ├── qq_config.json               ← QQ Bot 配置（含 instance_id 隔离）
│   ├── api.json                     ← API 凭证统一管理（deepseek=对话, qwen=图片）
│   ├── external_calls.yaml          ← 外部调用超时: batch_planner 120s, classify 45s
│   ├── routing_rules.yaml           ← 消息路由规则
│   ├── global_config.json           ← 实例注册表
│   ├── partner_config.json          ← Partner 主配置
│   └── agents/                      ← agent manifest
│
├── share/                           ← 共享数据（原 shared_* 收拢于此）
│   ├── knowledge/                   ← 累积知识库（每实例 latest/ + 轮次版本）
│   ├── mind/                        ← 跨实例记忆（habits.json 等）
│   ├── projects/                    ← 研究项目池（registry.json + .lock）
│   │   ├── SP140/                   ← SP140 蛋白项目
│   │   ├── molgen_exploration/      ← 分子生成探索
│   │   └── _legacy_instances/       ← 实例级残留项目归档
│   └── _legacy_config/              ← 旧实例 config/ 归档（历史数据不丢）
│
├── instances/                       ← 实例目录（只保留运行时隔离状态）
│   ├── 01/                          ← 当前 active：可见浏览器/视觉回执
│   ├── 02/                          ← 当前 active：分子连续研究
│   └── 03/ 04/ 05/                  ← 当前 inactive，保留历史状态
│       ├── state/                   ← 实例运行状态（隔离）
│       │   ├── desktop_inbox.jsonl  ← 任务注入入口（实例级，poller 只读这里）
│       │   ├── active_plan.json     ← 当前活动计划
│       │   ├── delivery_queue.jsonl ← 推送队列
│       │   ├── agent_sessions/      ← Hermes 会话
│       │   └── logs/                ← hermes_chat, agent_runs
│       ├── dialogue/                ← 每日 QQ 对话日志
│       ├── system/                  ← hermes_work 等运行时
│       ├── partner_data/            ← 实例学习库（活跃；systemd 未设 PARTNER_DATA_DIR 时在用）
│       ├── ooda_data/               ← OODA 循环数据
│       ├── projects/                ← 实例级项目（遗留）
│       ├── qq_config.json           ← 实例 QQ 配置（fallback）
│       ├── instance.pid
│       └── partner.log
│
├── external/                        ← 外部工具与代码库
│   ├── PocketFlow/                  ← 分子生成
│   ├── CytoBridge/                  ← 单细胞轨迹推断
│   ├── SESA-Self-Evolving-Search-Agents-master/ ← 自进化参考
│   ├── AI2BMD/                      ← AI 分子动力学
│   ├── ViSNet/                      ← 等变图神经网络
│   ├── amber/                       ← Amber MD 套件
│   ├── mmpbsa/                      ← 结合能计算
│   ├── Biomni-main/                 ← Bio 基础模型
│   ├── Aether/                      ← 世界模型
│   └── literature/                  ← 论文 PDF
│
├── partner_data/                    ← 全局数据（PARTNER_DATA_DIR）
│   ├── learning.db                  ← 学习/进化数据库
│   ├── skills_registry.db           ← 技能注册表
│   └── capabilities.md              ← 能力盘点
│
├── conversations/                   ← 每轮流水线快照
├── state/                           ← 全局状态（screenshots、logs）
│   ├── instance_scheduler.json      ← 双槽调度真相源
│   └── logs/
│       └── api_calls.jsonl          ← API 调用日志（deepseek/qwen 成功失败）
└── files/                           ← 共享文件（uploads/incoming/outgoing）
```

---

*最后更新: 2026-08-23*
