# Partner 当前进度与运行基线

**基线日期**: 2026-08-28
**当前阶段**: 手动稳定核心 + 11 个 framework bug 修复（#38-#50）+ 13 个 ADR（0007-0019）；03 自主度 75%、05 自主度 85%；无人监督自动迭代仍暂停

> 本文档是当前状态的权威摘要。Sprint 文档保留历史设计，
> `change_log.md` 保留问题与修复过程，`evolution_journal.md` 保留长期演进轨迹。


## 2026-08-28 最新状态：11 个 framework bug 修复 + 03/05 自主能力显著提升

**会话代号**: Hermes 接管 Codex（2026-08-28 凌晨 ~ 15:30）
**commit**: d536870 pushed to origin/main

### 11 个 framework bug 修复（ADR 0007-0019）

| Bug | ADR | 修复点 |
|---|---|---|
| #38 | 0007 | manual_stable 三步拓扑 + preflight 占位判定放宽 |
| #39 | 0008 | candidate_skills glob 模式 (`candidate_*.json` → `*.json`) |
| #40 | 0009 | handoff shape-(a)/(b) + ignore_handoff_check opt-in |
| #41 | 0010 | generate_text 走 report purpose（无工具 + 单轮）|
| #42 | 0011 | QQ bridge send_file future.result timeout 30s → 90s |
| #43 | 0012 | required_output_exts 否定句过滤 |
| #44 | 0014 + 0019 | generate_text prompt 注入上游 step content（preflight + execute 两端）|
| #45 | 0015 | allowed_read_roots 跨实例 + 实例 state 子目录 |
| #47 | 0015 | `(word\|docx)` word boundary（避免 `false_word` 子串误匹配）|
| #48 | 0015 | TaskInstance mark() 写顶层 status 字段（dataclass 字段加 status）|
| #50 | 0017 + 0018 | preflight + execute 接受 `paths` list alias（multi-source cross-instance review）|

### 03 + 05 自主能力演进

**03 (partner_framework_frontend)**：
- 自主度 50% → **75%**
- 第九轮端到端 verified（finding_report.md 真发到 QQ）
- 5/5 步：atomic_inspect_file + execute_code + generate_text + create_file + push_files
- 3 对 verbatim source_path + evidence_quote 双行引用（来自 step1 真读 harness.py）
- 诚实边界：grep 计数未提供时标 `proposed`（未执行）——不编造

**05 (agent_self_evolution)**：
- 自主度 40% → **85%**
- 第九轮端到端 verified（cross_instance_review_v9.md 真发到 QQ）
- 9/9 步：4 个跨实例 atomic_inspect_file（paths= list）+ execute_code + generate_text + create_file + push_files
- 真跑了 evaluate_isolated_preflight_canary（10 对 metrics）
- 3 对 verbatim source_path + evidence_quote 双行引用（来自 Aether/SESA/CytoBridge）
- 独立 PromotionDecision：inconclusive（promotion=false 状态不宣称晋级）

### 测试基线
- **333 → 351 passed**（+18 个新测试，0 回归）
- 13 个新测试覆盖 Bug #38/39/40/43/44/45/47/48/50
- 全量 `351 passed in 12.18s`

### 业务实例接管
- 01 + 02 active + healthy + QQ ready（xiaohongshu_operations + molecular_generation）
- 03 + 05 inactive（等下一轮 inbox）

### 重要诚实边界
- 03 还没"自主识别 partner framework bug"能力——所有 ADR 的修复都是 Hermes 做的
- 03 没"自主决定改什么"能力——所有 holdout/task 文本都是 Hermes 写的
- 05 不能自动 promote（partner framework by-design，需用户显式 approve）
- LLM 拒绝编造：发现截断/缺失证据时一律标 proposed，不编造数字

---

## 2026-08-26 最新状态：五阶段学习闭环与首个受控 Canary

- DeepSeek Harness、OpenAI Codex、Hermes Agent、OpenClaw 已固定版本并完成统一设计；只借鉴事件事实、
  离线 reducer、session/memory 和 candidate skill 生命周期，不复制运行时根基。
- 新增 Episode Trace v3 和六维 Reward Vector。truth/safety 是不可补偿硬门；05 风格 shadow 只能提出
  Candidate Experiment，不能修改 production 或 `control_policy.json`。
- 复核生产任务 `45cbe78a-bc36-46a3-9961-02b645baf7d3` 时发现成品把上一轮“无 shell/file-write”
  错误当作当前事实，和本轮真实 `create_file` 矛盾。Receipt `receipt_680db01279ab` 已追加作废，原先
  “production 冒烟成功”的结论失效；来源逐字门已补上运行事实一致性检查。
- Episode 批量归约、自动终态归约、Candidate Skill Registry、六策略空间/Shadow、04/05 双槽受控
  Canary 五阶段均已实现。Experiment `experiment_bf3cf4963540` 的候选
  `candidate_preflight_aware_planning_v1` 当前为 `canary`，但 `production_effective=false`。
- 10 个历史 baseline 反事实回放投影 preflight failure 6→1、semantic repair call 9→2。追加三轮
  跨来源承接验证后，真实 candidate Episode 共 17 个：completed/policy-eligible=4，失败 13 个全部保留；
  这仍是顺序调试与泛化证据，不是独立 A/B。
- 最终任务 `20267094-ca30-4295-9b77-76cc75c831b2` 生成 9,228 B 成品，真值 2/2，Receipt
  `receipt_3c8508a0fdfc`，Episode `episode_a844edfc1c673f2b` reward=1.0。当前全量回归为
  `351 passed in 12.18s`（333 baseline + 18 新测试）。

### 2026-08-26 追加：三轮跨来源承接与因果隔离硬门

- 04 连续完成 iteration 29–31，形成“内部 Harness/ADR → DeepSeek/Codex 源码 → 当前状态/RL 文档”承接链：
  `receipt_c8d056aa01c6`、`receipt_f67dabb4d5a2`、`receipt_ee1489e68318`。三轮最终成品均逐源
  truth=3/3、delivery=true、handoff=true、policy_eligible=true；Episode 分别为
  `episode_c0e52bffa24bb41e`、`episode_9b3cb380d28ab37b`、`episode_f98601ac5f11025e`。
- 为得到这三次成功，前置失败暴露并修复了：错拼输入被误当输出、验收失败原因误报为渠道失败、逐字证据
  在二次摘要中丢失、Markdown `output_spec` 被状态封装器处理、503 fallback 写入“File-mutation verifier”
  状态包装、以及历史问题复盘被误判为本轮能力矛盾。失败 Episode 和负奖励均未删除。
- Shadow 结果新增机器硬门：`intervention_isolated=false`，promotion blocker 明确为 baseline/candidate
  尚未走不同执行路径，以及仍缺独立匹配任务。候选保持 `canary`、`production_effective=false`、
  `promotion=false`；三个连续成功不能据此晋升。

## 2026-08-26 实机基线：04 项目承接 + 05 离线候选门

当前已经打通的不是“自动永续运行”，而是手动稳定路径上的可验证项目承接：用户明确触发一轮，
执行器发收到/计划/逐步/最终消息，读取上一轮真实产物和新来源，经过 Harness 与交付验收后，
治理层唯一生成 Receipt 和 RL 轨迹，然后停止等待。Planner 不得自行写 Receipt、发末尾总结或启动下一轮。

- 04 连续有效轮次 20–22：任务 `fd72ab17-75da-4d26-9c5a-6ff677033acb`、
  `bbbe91a4-5123-432c-90fc-b950cdb22483`、`2f1528c8-4539-47ba-9a74-1ee3054e84b5`；
  Receipt 分别为 `receipt_8b656cfe9116`、`receipt_a5b7193a1d11`、
  `receipt_69e946f2e687`。三条轨迹均有不同 outcome fingerprint、承接上一轮、包含新来源、reward=1.0。
- 错误轮次不会靠覆盖历史“洗掉”：`receipt_ac46f71bb43d` 因产物虚假声称没有写文件能力，已在
  `receipt_corrections.jsonl` 追加 invalidate；RL 样本筛选会排除它，项目状态恢复到有效轮次。
- 05 任务 `d002008a-7cd0-4193-a787-9b866c9c772c` 对 04 三条有效样本执行硬门审查，生成
  `experiment_c5f8bc67f9ac`。状态仅为 `candidate`，`promotion=false`，没有改生产代码、没有自动晋升。
- 本轮受控执行配对为 04/05 双槽；收口时进程已停止。QQ 网络是否可达不作为业务真值；本地 `qq_chat_history.jsonl`、
  `dialog_history.jsonl`、task log、文件、Receipt 和轨迹共同构成可核验链。

### 2026-08-26 下一阶段完成：受控 Canary 与显式晋升

- 旧实验 `experiment_c5f8bc67f9ac` 被标记 `inconclusive`：严格证据改动已经进入基线，无法再做因果归因。
- 新实验 `experiment_5af99917bea9` 隔离比较当前基线 `manual_stable_grounded_v1` 与候选
  `manual_stable_truth_audit_v2`。候选额外重新打开每个真实输入，核验最终成品中的
  `source_path/evidence_quote` 对；不是只信 extract 中间结果。
- 04 完成三组成对实跑，共 6 个独立任务。candidate 3/3 完成、mean reward=1.0、
  false-success=0；baseline 2/3 完成、mean reward=0.5167、1 个 Citation 假成功被拦截，reward gain=0.4833。
- 该阶段全量回归证明为 `307 passed in 9.23s`（后续基线已更新为 327 passed）。05 通过用户显式触发的 `decide_manual_canary` 写出
  PromotionDecision=`promoted`，最终 Receipt=`receipt_03db4def9a27`，Markdown 与 JSON 均进入交付证据，
  `next_actions=[]`。
- 控制策略只在 decision key `literature_github_learning:manual_final_artifact_truth` 下晋升 v2；
  不代表整个 RL 系统、其他实例或自动续轮被放开。普通 04“读取来源→生成 Markdown/TXT”任务现在会
  自动使用 production 真值门；聊天、代码产物及 01/02/03/05 不受该门误伤。

04 非实验生产任务 `45cbe78a-bc36-46a3-9961-02b645baf7d3` 当时完成来源逐字冒烟：框架在三个来源读取与
报告生成之间自动插入确定性 truth extract，当时记录 3/3 来源、reward=1.0；但 Episode v3 发现运行事实
矛盾后已作废 Receipt=`receipt_680db01279ab`，该样本现为 false-success、reward v3=0。此前生产试跑暴露的丢失步骤引用、抽取长响应、
回读成品被误算为外部输入和 HTML 标记被模型改写，均保留为失败轨迹并转成回归。

当前下一优先级是设计下一个独立、可回滚的候选；每个候选仍需重新采样和显式决策，不能继承本实验的晋升资格。

---

## 1. 运行状态

> 当前生产配置为 `runtime.mode=manual_stable`。用户消息是唯一任务触发器：先确认收到，
> 再发送每个步骤的开始/完成，最后发送真实结果并停止。历史 Sprint 11–13 和 Campaign
> 记录仅用于追溯，不能作为当前运行入口。

| 实例 | 状态 | 当前用途 | 运行策略 |
|------|------|----------|----------|
| 01 | 手动，默认槽 | 小红书账户与内容维护 | 用户消息触发；关键网页步骤截图+视觉说明；不得擅自发布 |
| 02 | 手动，默认槽 | 分子生成方法与实验 | 用户消息触发；真实数据、运行、分析和领域报告 |
| 03 | 手动，按需换入 | Partner 框架与前端 | 用户消息触发；代码、测试、兼容性和回滚证据 |
| 04 | 手动，按需换入 | 文献和 GitHub 学习 | 用户消息触发；官方来源、版本与最小复现 |
| 05 | 手动，按需换入 | Agent 自进化研究 | 用户消息触发；不得自动修改生产路径或 promotion |

调度器硬限制同时最多两个实例。产品默认槽位仍是 01/02；本轮 scheduler 当前记录意图槽
`active_slots=["04","05"]`，但 2026-08-26 收口检查时 04/05 systemd 均为 inactive，不能把槽状态写成
进程正在运行。03–05 的能力没有删除，重新启动/切换必须继续经过受控 `switch`。

所有活动 Campaign 已取消，持久 Campaign 实例 unit 已移除；Campaign 模板和历史状态保留作实验代码与证据。
`automatic_campaigns`、`automatic_iteration`、`automatic_self_heal`、`autonomous_cron` 均为 false。
调度器仍强制最多两个实例，01/02 是默认槽，03–05 只能显式手动 `switch` 换入。

最后活动的 `campaign_7f635d0333a9` 已明确取消；取消时 10/10 WorkItem 完成、0 failure，
但这不代表持续运行方案达到产品要求。此前实跑已经证明多控制器、多消息协议和固定报告路径会让
手动体验退化，因此它们不再是当前默认。

2026-08-24 已将 DeepSeek Harness 与 OpenAI Codex 官方仓库浅克隆到 workspace
`external/code` 并固定 revision；Partner 只独立借鉴事件证据、离线归约、生命周期和
策略边界，不复制其 TypeScript/Rust 根基。详见 `architecture/harness_reference_adoption.md`。

历史 Campaign 状态只作为运行证据，不代表当前仍在运行。实时状态必须通过
`python scripts/partner_campaign.py status`、scheduler、systemd 和 heartbeat 联合读取；静态文档不硬编码 active PID。

### 1.1 最新手动核心修复摘要（优先读取）

2026-08-25 已完成 04/05 暴露的 P0 框架修复：Planner 执行前语义预检、读写权限分离、
`manual_stable` 自治事件 fail closed、完整 plan 优先解析、步骤引用规范化、空文件拒绝、
LLM 调用真实计数，以及 `extract` 的显式输入/完整 JSON/逐字逐源证据门。全量回归
该阶段当时为 `252 passed in 12.30s`；当前包含受控晋升与生产冒烟的全量基线为
当前为 `327 passed in 14.45s`。

04 最终生成 3,728 B 的双 Harness 来源对照，6/6 引文逐源匹配；05 从 immutable evidence
bundle 学习这组三阶段失败轨迹。首版因错误提出恢复已知坏 v2 prompt 被拒；修订版生成
`evolution_candidate_canary_v3.md`（3,741 B），保持 `promotion=false`，并真实记录 2 次模型调用。
本轮 canary 期间 04 QQ 已获得 token、WebSocket ready 并完成本地用户消息链；05 的历史日志也记录过
WebSocket ready/reconnect。收口时两个服务均已停止，因此当前不声明 user-ready 或远端 QQ 到达。
详细实现、证据路径和历史纠正见第 6 节；此前“只能 run_shell 绕过”“复杂任务不可达”不是当前结论。

## 2. 已闭环能力

### 2.1 真实消息与文件交付

- `send_user_text` 和 `push_files` 通过运行时 QQ 回调发送；只有渠道确认后才返回成功。
- `delivery_queue.jsonl` 只作为追踪记录，不再被解释成“已发送”。
- 显式发送和任务结束自动发送之间有内容签名去重，避免同一文件重复推送。

### 2.2 01：可见浏览器与逐步视觉回执

小红书上传要求流程已实机跑通：

1. 打开可见的小红书创作发布页并置前。
2. 每个协议定义的关键步骤后保存截图。
3. 调用 `qwen3-vl-flash` 读图，生成中文页面描述。
4. 把截图附件和读图说明分别发送给用户。
5. 同时使用 DOM、文件控件和页面文字验证操作结果；不以视觉模型的概率性描述代替确定性验收。

实机产物：

- `xhs_step_01_open_publish.png`: 73,288 B
- `xhs_step_02_image_text_tab.png`: 81,501 B
- `xhs_step_03_upload_requirements.png`: 81,501 B

三张图片及对应的三条视觉说明均获得真实 QQ 发送确认。

### 2.3 02：四轮分子实验链

| 轮次 | 实验 | 完成边界 |
|------|------|----------|
| 1 | 生成并评估候选分子 | 85 个有效候选落盘 |
| 2 | Bemis–Murcko 骨架和 Morgan 指纹多样性 | 使用真实 CSV 计算并发送详细 PDF |
| 3 | SA 合成可及性与可复现随机基线 | 两组各 85 个候选，报告中的下一步被自动执行 |
| 4 | QED/SA 多目标选择与头部骨架集中度 | PDF 和 Top-20 CSV 都发送后才结束 |

第三轮实测：

- 规则候选：唯一率 1.0000，平均 QED 0.537781，平均 SA 2.174044。
- 随机基线：唯一率 0.647059，平均 QED 0.533471，平均 SA 2.230464。

第四轮实测：从 170 条记录中选 20 条，只有 9 个唯一结构和 4 个骨架，
平均 QED 0.598151，平均 SA 1.457814。这表明多目标排序提高了指标，
但头部候选出现明显结构集中，是后续需要解决的真实研究问题。

## 3. 验证基线

| 层级 | 结果 | 说明 |
|------|------|------|
| 当前全量回归 | 327 passed | 2026-08-26 执行；含手动核心、Episode v3 自动/批量归约、Candidate Registry、因果隔离阻断、逐字证据传递、生成 fallback 与历史复盘语义回归 |
| Sprint 10 历史分层测试 | 147 项通过 | L1/L2/L3/L5/L6 的历史基线，本轮未全部重跑 |
| 01 实机 | 通过 | 可见页面、3 张截图、3 次读图、6 次真实消息/文件交付 |
| 02 实机 | 通过 | 第 3/4 轮自动续跑，两份 PDF 和候选 CSV 获得发送确认 |

01/02、03/04、05/01 已按最多双槽完成启动轮换，五个实例均能加载新代码并保持 healthy，最后恢复
01/02 默认槽。手动实机 canary 已把 planner 收敛到两步并成功读取共享配置，但测试期间 QQ token/gateway
连续网络失败，部分消息没有渠道回执，因此完整“收到—逐步骤—最终结果”QQ 链**没有标为通过**。
代码现在会把任一消息回执失败降级为交付失败并结束，不再误写成功；dashboard 分开显示 runtime
`healthy` 和真实 `user-ready`。网络恢复后需再做一次单实例 canary。

## 4. 新治理基础（2026-08-23）

### 4.1 分级文档与动态上下文

- `docs/catalog.yaml` 是机器可读的顶层目录，文档分为 L0 索引、L1 常驻规则、
  L2 项目/操作手册、L3 最新运行收据和默认不加载的 L4 历史。
- 规划阶段按实例和任务选择上下文；Harness 在每个步骤前按“实例 + 事件 + 步骤”
  重新选择，并记录 selection ID、来源和字符预算。
- `select_context` 事件可让便宜模型做语义选择；无模型或模型失败时使用确定性回退，
  不会阻塞任务。

### 4.2 项目迭代闭环

- 每轮必须写 `IterationReceipt`，包含真实输入、执行动作、产物、发现以及结构化
  `NextAction` 或明确停止原因。
- 下一动作先是 `proposed`；只有入队回调返回真实 task ID 后才能标为 `queued`。
- 后一轮必须承接上一轮产物。01 的历史流程已迁移为 2 份收据；02 已迁移为
  4 份收据，并以“缺少真实目标/活性数据”为阻塞边界。
- 01/02 的续跑顺序由 `partner/protocols/*.json` 声明，不再散落为硬编码分支；
  项目累计轮次单调递增，因此协议完成后可开始新周期而不覆盖历史。

### 4.3 证据型自进化

- 运行时只把明确失败、必需产物缺失、必需交付未确认或连续三轮同事件记录为 Issue；
  不把一般文本猜测直接当根因。
- 改动先成为有基线、假设、成功标准和测试的 EvolutionExperiment。
- 只有全部标准通过且回归通过才能 promotion；失败决策必须带 rollback 信息。
- 项目状态与进化实验分离：修复结束后通过 `project_id/resume_action_id` 回到原项目，
  避免“做了一次反思就结束项目”。

### 4.4 持续运行 Campaign

- `scripts/partner_campaign.py` 可创建 30 分钟、数小时或一天的持久 Campaign，并用 user systemd
  transient unit 脱离外部 Agent 会话运行。
- Controller 在五实例间自动选择最多两个槽位；每个 WorkItem 有租约、真实 task ID、状态、产物、
  交付回执和重试预算。重启后从 task log 恢复，不重复注入。
- Campaign WorkItem 只运行一轮，禁用旧的内存 Research Loop 续跑；下一轮由 Receipt/NextAction
  回到持久 Controller，避免双控制器。
- 三轮相同事件和产物内容会熔断；真实发布、支付、购买、密码/凭证等动作自动进入人工门。
- 到达时间、任务、失败、模型或成本预算后，只允许最终日报；真实交付或明确失败后结束。
- 120 cycle 确定性模拟完成 241 个 WorkItem（122 ticks），最大槽位始终为 2，0 失败，生成 25 份报告并正常收敛。
  这是控制器模拟证据，不等同于真实模型、QQ 或业务整夜验收。

### 4.5 30 分钟实机 canary（campaign_a06e75ccfa0f）

- 01 真实进入已登录的小红书图文入口，发送 3 张关键截图及 3 份 qwen3-vl-flash 描述；
  DOM 核验到 1 个文件控件、16 条上传要求，未上传或发布，随后以明确恢复事件进入 blocked。
- 02 在最大深度、排除目录和文件数量上限内完成数据就绪度扫描，生成详细 Markdown、PDF、
  校验 JSON 和数据契约；PDF 与摘要获真实发送回执。因缺分子身份+靶点+活性联合数据而 blocked，
  没有机械重复 QED/SA。
- 实跑发现并修复：blocked 状态不调度阶段报告、确定性报告被旧 LLM planner 接管、终态回调重复、
  数据目录无界扫描、Campaign 噪声消息及视觉/规划模型调用漏账。12:53 的修复重试已真实发送阶段报告。
- 13:10 最终报告真实送达，Campaign 在校正“报告误触业务重复熔断”后状态为 completed；
  01/02 均恢复 active。30 分钟 canary 完成，但保留两次旧报告路由失败作为失败证据，不能写成零缺陷运行。

### 4.6 两小时实机审计与离线 RL（campaign_46a3b906ffee）

- 实际轮到 01–05 且未超过两活动槽，但 WorkItem 18/12、失败 16/3；最终日报送达后仍继续派发，因此未通过验收。
- 02 确定性审计是唯一正奖励业务动作；01 归属分裂，03 任务身份重复，04 超时，05 出现 Issue 派生风暴。
- 已修复总预算/最终报告预留、停止 latch、边界后取消未开始任务、`task_instance_id` 复用和 evolution 递归物化。
- 03/04/05 默认改为确定性合同审计、外部资料索引和离线 RL 事件。10 个旧业务终态已写入轨迹，产生 candidate policy 和首个正式 candidate Experiment，没有自动 promotion。
- 详细见 `docs/operations/campaign_2h_audit_2026-08-23.md`；修复后的新两小时实机长跑尚未执行。

### 4.7 修复后五实例干净 canary（campaign_653873ef41c2）

- 五个声明式主阶段只创建 5 个 WorkItem，依次轮转 01–05，最大活动槽为 2；未再物化历史
  Issue 风暴，也未回退到泛化“继续写下一轮”。
- 01 真实打开已登录的小红书发布页，3 个关键步骤均保存 PNG、调用
  `qwen3-vl-flash` 描述，并分别获得 QQ 图片和文字发送确认；未上传、未发布。
- 02 扫描 3001 个有界文件后，以缺少“分子身份+明确靶点+活性测量”数据的证据边界 blocked，
  生成 Markdown、2 页 PDF、校验 JSON 和数据契约并真实发送。
- 03 实际运行 30 个 Campaign/RL 合同测试全部通过；04 核验 Polar、RLVR-World、SESA、
  JIT-RL 四类外部资料并明确 `indexed != integrated`；两者均生成证据文件和 QQ 回执。
- 05 写入可审计轨迹、更新保守 candidate policy，并把上一轮 9 次
  `generic_or_unobserved` 风暴识别为最低收益动作，建立正式 candidate Experiment；没有自动 promotion。
- 实跑发现 05 会早于慢任务完成、导致只学习半轮结果。已新增硬依赖：05 等待 01–04 终态；
  Campaign 最终报告前再做幂等 RL final sync，补齐晚完成任务和 05 自身。
- 5 分钟检查点和最终日报均由 01 真实发送；最终账本为 7/7（5 个主阶段、检查点、最终日报）、
  0 failure、0 retry、3 次视觉模型调用。截止前 final sync 补入 01 与 05 两条轨迹，
  五个主阶段全部进入 RL 账本；Campaign 正常 completed 并恢复 01/02 常驻槽。
  完整证据见 `docs/operations/campaign_clean_canary_2026-08-23.md`。

### 4.8 Sprint 11：从审计闭环进入执行闭环

- 新增 execution profile：01–04 各执行两波，05 等整轮完成后做 RL/Experiment 决策。
- 每波强制“真实输入、Python 源码、实际退出码、JSON 结果、分析 Markdown/PDF、QQ 回执”。
- 01 使用 content inbox 做发布前证据准备；02 已发现 TargetDiff 的 184087 条 affinity 记录并进入真实数据分析；
  03 编写 Campaign 历史指标分析器；04 真实 clone/fetch SESA 并抽取 Skill Bank 适配面；05 进行候选回放。
- 01/02/03 的真实输入 smoke 已通过，详细 PDF 内容质量门达到 9 节、约 1274–1345 个正文字符。
- 当前 Sprint 详见 `docs/sprint11_执行型持续迭代.md`；下一运行使用 `--profile execution`，
  不再重复上一轮 audit profile。
- `campaign_cf78d794f832` 的预声明执行链已完成三波与两次 05 汇总：14/14、0 failure、0 retry，
  所有项均有源码/退出码/JSON/报告/QQ 回执。随后的 3 个自由规划动态项全部未通过：
  超时、错输入路径、占位 `NotImplementedError`、产物和真实交付缺失均被验收门拒绝。
  已修复 planner 不得降级 `.py/.json/.md/.pdf` 合同，并修复读取输入被误判为输出；下一动态 canary 将按这个新基线运行。
- 活动 Campaign `campaign_6201619b614b` 截止 20:39：已完成五实例基线执行，已保留并摄取第一组 4 个动态 planner 失败；当前正运行新进程下的 01–04 修复后重试，最终由 05 补账。
- 当前动态链已从“只有伪源码”推进到“脚本真运行、JSON/Markdown 真产出”，但首个结果发生 Vina 身份泄漏并缺合格 PDF，已 blocked 且写入语义门。修复后五实例治理回放已终态：03/04/05 completed，01/02 在真实授权/数据边界 blocked。Controller 等待新证据到 20:39，不机械重复旧计算。

### 4.9 Sprint 12：集中跑通 02 TargetDiff 项目

- 真实 pickle 审计确认旧的“无活性数据”结论错误：184087 条记录中 76803 条 `pk>0`，覆盖 1041 个有效 key 首段组；错误第 18 轮收据已追加失效，项目恢复 active。
- 02 固定为五个相互承接的有界阶段：字段合同、分组基线、非线性候选、残差失败组、五折稳健性；`pk` 是唯一目标，`vina/rmsd` 只能作为特征，训练/测试组重叠必须为零。

### 4.10 Sprint 13：五项目组合首个实机波次（campaign_cd18347f0857）

- 输入门首轮只准入 01、03、04；02 因本地没有官方 TargetDiff split 显示 `waiting_input`，没有伪造运行。
- 调度顺序为 01+03（双槽）→04 接棒→03 代码变化审计→05；任意时刻未超过两个活动槽。01、04、三次 03 审计和两次 05 均生成真实产物并获得 QQ delivery 回执。
- 首轮发现旧 TargetDiff Controller 在取消后退出过慢，错误恢复 01+02 并中断新 Campaign 的 03。该 WorkItem 保留一次 failure，第二 attempt 完成；修复后终态 Controller 只有在自己仍是 active Campaign 时才能恢复槽位。
- 新增空队列释放运行槽：当前五条 lane 分别为 01/03/04/05 `waiting_change`、02 `waiting_input`，Campaign blocked 等待新证据，真实 scheduler slots 为 `[]`，没有让 05 空占资源。
- 当前账本 7/7 completed、1 次已解释故障、0 controller retry 计数；离线轨迹已摄取 6 条，受中断后成功的 03 reward=0.82，其余已摄取本轮动作 reward=0.90。最后一条 05 自身结果将在 deadline final sync 幂等补入。

### 4.11 六小时持续 Portfolio（campaign_744a39317fad）

- 运行到 2026-08-24 06:31，最多双槽、100 WorkItem 和 10 failures 硬预算；从上一 Campaign 继承指纹，不重跑 01/04 基线。
- TargetDiff 作者 README 指向的 Google Drive 当前返回 HTTP 404；从 Zenodo 公开镜像取得 `split_by_name.pt`，15,284,527 B，MD5 `d782da9499096612ca7115cb94313aa2` 与发布记录一致，使用 `torch.load(weights_only=True)` 安全加载。
- 下载时曾因文件持续增长产生三份旧式“存在即通过”审计，已全部改为 cancelled 并写明 `unstable_partial_download_not_structurally_verified`；新门要求两次稳定指纹和结构/校验和验证。
- 正式 split 含 train 100,000、test 100 个 identity；与 affinity_info 精确匹配且 `pk>0` 的训练/测试样本为 40,617/27，训练 786 组、测试 26 组，组交集为零。
- 官方边界点估计：线性 RMSE 2.2678，HGB 2.2181，差值 -0.0497。1,000 次靶点组 bootstrap 差值均值 -0.0457，95% CI `[-0.2084, 0.1139]`，HGB 更优概率 0.707；结论 inconclusive，不允许 production promotion。
- 01/03/04 主动课程已完成，02 benchmark/bootstrap 已完成；一次 PDF 内容门失败被保留为 blocked，修复门槛后重跑成功。当前已自动进入 15 分钟轮转 scout，首项为 01，之后每项仍由 05 摄取。
- 新增 `--profile molecular`。02 同实例串行推进，05 必须等待所有 02 项终态，且只能根据真实产物/交付/失败轨迹做保守 RL 审计。
- 正式运行前 smoke 得到五折线性基线 mean RMSE=1.5971、std=0.0533、mean MAE=1.2616；这是有限统计预测力，不是药效因果或创新完成证明。
- `campaign_0587a59dfe22` 已完成 Stage 1–7 和两次 RL 审计；异常值裁剪五折平均改善 0.0312，HGB 五折平均改善 0.0648 且 5/5 折改善。
- `campaign_f6cfb4e0ed9d` 已完成 Stage 8 来源审计：pK 语义与构建路径有本地源码证据，但官方 split 文件缺失，当前分组明确降级为独立近似。两轮最终日报均真实送达。
- `campaign_a5f3c0c41760` 使用 `molecular-continuous` 自动补给 Stage 9–13，并在 Stage 10/13 后插入 05；7/7 completed、0 failure、0 retry，所有业务 JSON 的 lineage 均为 consumed=true。
- 配体聚合后 HGB 五折平均改善 0.0592；靶点等权改善 0.0403，bootstrap 95% CI [-0.0683,-0.0148]，按预注册门保留 candidate、禁止自动生产晋升。
- 当前 profile 在证据图末端进入 waiting，不用 batch_plan 伪造工作。下一架构步是 portfolio scheduler。

### 4.12 持续推进与 RL 控制闭环 v2

- 已实现 EvidenceManifest 持久归档、语义 outcome fingerprint、可执行 Receipt NextAction、
  baseline/candidate 交替选择与 PromotionDecision 门；全量回归 **184 passed**。
- RL 奖励已改为业务增量优先；PDF、QQ、completed 只占小额合同分，scout/05/no-change 不进入策略。
- 新增 01–04 有限推进链，下一轮由 Campaign 实际入队，而不是只在 Markdown 写“下一步”。
- `campaign_76550fd7382a` 实跑证明失败产物会进入持久 EvidenceManifest，且 QQ 失败时
  `business_progress=false`；早期故障累计达到 8 次后按预算 completed，最终日报真实送达。
- 实跑同时修复：Campaign PDF 不再触发旧 strict_reflect/next_iteration；retry 的 message ID、指令文本和
  Executor dedup key 都纳入 attempt/recovery。代码通过不等于长跑/RL promotion 已通过。
- QQ bridge 现在原子写 `state/qq_delivery_state.json`；冷启动/断线时 Controller 不派发要求真实送达的任务，
  `delivery_ready=true` 后下一 tick 自动继续，不需要人工恢复。
- 新活动 Campaign `campaign_fa136a7c6833`（截止 14:30）已真实完成：baseline 合同复核 → 05 →
  evidence-graph candidate → 05 → policy-integration follow-up → 05。所有成功项均有 QQ 回执和持久证据；
  terminal Receipt 明确到达声明的外部输入/批准边界。当前等待 12:46 evidence scout，槽位为 `[]`。
- 本轮 baseline reward=0.65、candidate=0.85；但双臂有效样本未达各 3 个，canary decision 为空，
  没有自动 promotion。旧 candidate 首次因详细 PDF 正文不足被拒绝，扩充报告后直接回归和实跑均通过。
- 13:04 修复并实机验证课程耗尽后的空转：01+02 与 03+04 分别同 tick 双槽派发，完成 claim evidence matrix、
  TargetDiff error slices、runtime recovery canary 和 adapter contract，均有 EvidenceManifest 与 QQ 回执；随后 05
  一次摄取 5 条新轨迹。Scout/no-change 已从业务波次摘要排除，健康策略最低项不再伪造高严重度 Issue。
- 当前全量回归 **189 passed**。这一结果证明本轮双槽和新课程生效，不等同于整夜稳定性或策略 promotion 已通过。

### 4.13 用户可观察性、领域报告与防回退基线

- 新增 L1 `product_principles.md`，把过程可见、领域表达、迭代承接、证据型自进化、自主边界和已验证能力防回退定义为所有实例的共同产品合同；`self_awareness.md` 已去除过期静态运行状态。
- deterministic Campaign 业务任务现在在执行前、处理器返回后、文件交付后分别发送 `started/executed/finished`；三份真实回执写入 `campaign_progress_update`，Campaign 验收缺任一阶段即失败。
- 浏览器任务保留此前 01 已验证的逐关键步骤截图、视觉模型描述、图片与说明送达；普通三阶段消息不替代该强合同。
- 01/03/04 continuous 事件改为领域 renderer；02/05 保留各自实验/RL 报告结构。PDF 公共层只负责封面、颜色、标题、代码块、页眉和页码，不再用一套章节套所有项目。
- `campaign_104d093f2287` 首次 canary 为 3/3 completed、0 failure，验证三阶段消息和新版 PDF；实跑发现文件推送的 `ok+pushed/total` 与文本推送的 `delivered` 合同不同，导致成功报告被文案误写为“未确认”。
- 修复后 `campaign_4faa4352f48b` 为 2/2 completed、0 failure、0 retry；QQ history 明确记录 `报告送达：已确认`。新版实例 03 PDF 已真实渲染检查，2 页、中文字体、标题层级、证据块与页码正常。
- 当前全量回归 **195 passed in 8.32s**。这证明用户体验合同和直接事件回归通过，不代表五实例整夜 soak 已通过。

### 4.14 Portfolio 课程耗尽与 05 抢跑修复

- `campaign_85c957ea5353` 实跑 17/17、0 failure，但只有 4 个业务增量且全部来自 03；另有 4 次 05 和 8 次 no-change Scout。每批 Scout 仅运行几十秒，随后双槽空闲约 14 分钟，证明 runner 存活不等于项目持续推进。
- 根因一是 Portfolio 在 `materialize_project_actions` 之前决定是否运行 05，导致每个 03 小步骤后都触发一次 RL；根因二是继承状态中的旧课程全部 `curriculum_complete`，01/02/04 没有新指纹时只能 Scout。
- tick 现先物化 Receipt-owned NextAction，再判断整波 05；新增四项目 v3 业务课程及确定性机器分析。近期窗口业务密度低于 0.25 且 Scout 至少 6 项时，重复 Scout 自动受抑制。
- 全量回归 **200 passed in 10.77s**。新实跑 `campaign_9785f703da0b` 于 20:23 启动，deadline 22:23；首 tick 同时准入 01/02/03/04 四个不同业务项，01+02 后由 03+04 接棒。到 20:26 已完成 14/14、0 failure、0 retry：其中 12 项为真实业务增量，2 项为波次级 05，0 Scout；所有业务项均 `delivery_confirmed=True`。顺序已实证为四项目波次→03 完整 continuation 链→一次 05→四项目第二波→一次 05。

### 4.15 继承课程完成态后的实机恢复（campaign_6e312e6bb4f3）

- 2026-08-25 00:48 启动 30 分钟追踪后，Campaign 虽有 enabled/active 的持久 systemd unit，却保持 0 WorkItem 并标记 blocked。根因是新 Campaign 继承了全部 `curriculum_complete` 状态，但 Scout 准入仍要求“当前 Campaign 已有可学习业务结果”，形成先有结果才能 Scout、无新输入又没有结果的循环依赖。
- 修复后，继承自已完成 Portfolio 的新 Campaign 可直接进入低频 Scout；仍受双槽、下一检查时间和低业务密度抑制门约束，Scout 不触发 05，也不算业务进步。新增专门回归，当前全量 **201 passed in 12.14s**。
- 重启持久 runner 后，框架代码指纹变化先真实唤醒 03：完成 4 个业务 WorkItem，再由 05 一次摄取 4 条新轨迹；随后 03+04 双槽 Scout 各完成一次并如实记录 `monitor_only=true / no_change=true`。截至 00:50 共 7/7 completed、0 failure、0 retry，下一 Scout 安排在 01:05。
- QQ history 已核对而非只信 WorkItem 字段：03、04、05 均存在 started、executed、PDF file、finished 四类真实消息记录和附件服务器路径。05 保持 conservative candidate，未因本轮成功自动晋升。

### 4.16 用户过程回执 v2 与继续运行（campaign_7f635d0333a9）

- 用户实机反馈指出旧三阶段仍过于接近“只发结果文件”：started 只列预设计划，executed 只发一次汇总，验收没有要求复述收到的指令或逐步骤说明。
- `user_progress_v2` 将完成硬门升级为 `instruction_received → started → executed → verified → finished`。呈现继续使用既有项目化标题和语气，分别展示清理后的业务指令、开始与承接、精简的实际事件/命令、机器验收与 PDF 送达、最终结论/下一步；缺任一真实 callback 都不得完成。旧 v1 仅用于历史任务兼容。
- 全量回归 **202 passed in 9.23s**。实机中 03 已展示完整指令与真实 pytest 命令；新 30 分钟 Campaign `campaign_7f635d0333a9` 又以双槽完成 01/02 Scout：01 展示实际 Python 命令及 `records=2, unique_urls=1`，02 展示 TargetDiff provenance audit，二者均含五阶段文本和 PDF 附件，且被如实标为 monitor/no-change。
- 新 Campaign 当前 4/4 completed、0 failure、0 retry，仍在持久运行；下一次低频 Scout 为 01:20:45，deadline 约 01:35。
- 用户指出首版 v2 改变了已验证的消息格式并暴露内部标签。修正后 `work_67f649425f13` 实机 canary 使用 `📋 收到本轮任务 / ▶️ 开始本轮执行 / ⚙️ 关键操作完成 / 🧪 结果核验完成 / ✅ 本轮结果`，任务正文已去除控制 marker，命令已去除绝对路径；完成且 `delivery_confirmed=True`，并保持 monitor/no-change 语义。

## 5. 当前限制与边界

1. **“每一步”是协议定义的关键操作步骤**，普通 Campaign 已升级为收到指令 + 三个执行/验收步骤 + 收尾；浏览器另有小红书打开发布页、切换图文、读取上传要求的逐步截图与视觉说明强合同，尚未泛化到所有浏览器事件。
2. **视觉模型是概率性的**，可能对小范围 UI 选中态产生误判；成功判定必须继续依赖 DOM/控件/发送回执。
3. **01 尚未完成真实内容发布**，当前验证到已登录的图文上传入口和上传要求；真正发布仍需内容产物、安全检查和明确发布权限。
4. **02 已有可用的 pK/Vina/RMSD 联合记录，但证据仍有限**：当前只支持数据集内分组预测；官方字段语义、官方 split、异常值和实验外推尚未核验，不能写成药效因果。
5. **集中运维面板已落地 `scripts/partner_status.py`**：可读取 systemctl、heartbeat、Project、Receipt、pytest 和活动 Campaign 摘要。
6. **治理层不自动批准生产代码修改**。便宜模型可以提出和测试 candidate，只有满足 promotion gate 才能进入生产；这是一条有意保留的安全边界。
7. **旧两小时 soak 未通过，修复后的短 canary 主阶段已通过**。RL v2 仍缺真实双臂样本，需先完成本轮 canary，再在新代码上重跑 2 小时，
   验证多个检查点、截止收口和长时间资源稳定性；不得直接升级为“整夜已稳定”。

## 6. 2026-08-25 手动核心修复后的当前结论

此前 03/04/05 的 14 轮失败是有效故障证据，但其中“只能用 run_shell 绕过路径安全”、
“atomic_inspect_file 只能读任务目录”、“复杂任务不可达”和“planner 成功率 100%”均已被后续
实现或更大样本推翻，不能继续作为当前能力结论。

本轮已完成：

- 读写权限分离：写仍严格限制在当前 TaskInstance；只读允许 Partner `partner/tests/docs`、
  `external/code`、`external/literature`，以及受治理的 `share/evidence`、`share/mind/governance`、
  `share/projects`。任意其他绝对路径仍拒绝，不使用 `run_shell + cat/cp` 绕过策略。
- BatchPlanner 在执行前做语义预检：核验 event 注册、依赖、真实输入路径、输出约束和依赖引用；
  不合法计划最多做两次定向修复，仍不合法则失败，不让错误级联到执行期。
- `manual_stable` 的 `strict_reflect`、`next_iteration`、Campaign、自愈和 tree search 同时在规划、
  事件处理和写后钩子处 fail closed；普通报告写入不再暗中追加自动反思任务。
- 修复完整 plan 被嵌套 `{path, content}` 抢占、`$ref.step.content`/裸 `step2` 引用、
  `create_file` 空内容成功、读取步骤误显示“生成文件”、atomic LLM 调用漏账等真实性问题。
- `extract` 现在是显式输入闭包：不会注入其他 Partner 文档；有 action prompt builder 时也不会丢失
  `data`；完整外层 JSON 必须可解析，所有 `evidence_quote` 必须逐字存在于输入，命名源还要逐源匹配。
- QQ `RESUMED` 会重新发布 ready 状态；启动超过门限仍未 READY 会写成
  `error/ReadyTimeoutError`，不再永久停在 `starting`；runtime `healthy` 与 `user_ready` 继续分开记账。

实跑证据：

- 04 先后保留三次运行：`no_data_provided`、截断 JSON、严格门通过。最终报告读取 DeepSeek
  `docs/architecture.md` 和 Codex `codex-rs/app-server/README.md`，6/6 引文均逐字存在于对应源，
  生成 `grounded_harness_comparison.md`（3,728 B），实际步骤模型调用正确记为 1。
- 三次 04 轨迹已归档到不可变 evidence bundle，指纹
  `f2b32b5e465dafed0cf0f93b`；05 从 governed evidence 读取失败→修复→通过轨迹，生成
  首版 candidate 因回滚定义错误未验收；重做后生成 `evolution_candidate_canary_v3.md`（3,741 B），
  明确 `promotion=false`、失败时保持 manual fail-closed 且绝不恢复坏 v2 路径，没有修改生产代码。
  05 证据包指纹为 `737adfb1f79470ff549624bb`。
- 04/05 已进入双槽且两个进程 healthy；QQ 上游 TLS 在 WSL 与 Windows 直连均 timeout，当前不能把
  “双进程健康”写成“双 QQ 链通过”。待两个实例同时 `user_ready=true` 后，再从真实用户入口复跑
  “收到—步骤开始/完成—最终结果/文件”链。
- 全量回归：`252 passed in 12.30s`。

下一顺序：先完成 04/05 双 QQ 手动链；通过后恢复默认 01/02。自动迭代和 RL promotion 继续保持关闭，
本轮 05 结果只作为 candidate，不因单次样本进入生产。

## 附录 A：修复前 14 轮失败记录（历史，不是当前能力结论）

### A.1 阶段 1 当时状态（2026-08-25 实机）

- **Bug #36 phase 1+3 已修**：micro planner 成功率从 ~33% 提升到 100%（9 轮实机验证）
- **ADR 0005** 记录全部修复 + 撤回决策；全量 227 passed
- **03 实例真实能力边界**（实机确认）：
  - 可用 endpoint：atomic_read_state / atomic_list_project_files / atomic_inspect_file /
    atomic_write_artifact / atomic_compose_structured_result / create_file /
    smart_llm_structured_action / run_shell / send_user_text / push_files
  - 不持有：app_focus / app_send_keys / app_screenshot_window（01 XHS 专属）；
    analyze / check_quality 在 03 实例上不可用
- **03 项目主线 9 轮全部失败**（project_brief.md / partner_canary.md /
  __init_canary_stub.py 三产物未生成）：
  - 根因：任务设计与 03 能力错配 + LLM content placeholder 行为 + 03 endpoint 不完整
  - 不是 framework bug；03 真实工作应是"读 partner 代码 → 写 patch + pytest"
- **诚实边界**：当前 project_brief.md 仍是 321 B 历史空模板（8 字段全"待补充"），
  partner_canary.md / __init_canary_stub.py 不存在

### A.2 当时建议

| 优先级 | 工作 | 验收标准 |
|--------|------|----------|
| P0 | 让 03 做真实代码改动任务（读 partner 框架 → 定位 bug → 写 patch + pytest） | 03 真实 run_shell pytest + 真实写文件 + 五阶段 QQ 真实送达 |
| P0 | 把逐步视觉回执抽成通用浏览器事件策略 | 新流程可声明必须截图/读图/发送的步骤，且失败不误报成功 |
| P0 | 在新用户体验合同上做五实例 30 分钟→2 小时 soak | 每个业务项五阶段回执、领域报告、Receipt/NextAction、RL 返回项目均有真实证据 |
| P1 | 建立实例健康与交付仪表板 | ✅ 已落地 `scripts/partner_status.py` + `partner/monitoring/partner_dashboard.py`（deterministic、无 LLM、含 7 项测试） |
| P1 | 分阶段真实 Campaign soak | 30 分钟→2 小时→整夜；每阶段核验 QQ、Receipt、自进化返回项目和成本 |

### A.3 当时废弃方向

- ❌ "让 03 立项目主线（写 brief/canary.md/stub.py）"：能力错配，9 轮已证明不可达
- ❌ "让 03 写 harness.py f-string 回归测试"：MiniMax-M3 用相对路径 ENOENT，1 轮证明不可达
- ❌ Bug #36 phase 4：在 manual_stable prompt 加 content 字段硬约束 → 触发 LLM
  重新规划 + 选 03 不持有的 endpoint
- ❌ Bug #36 phase 2：在 MicroPlanner 加 caller-side retry（production 走 BatchPlanner）

### A.4 03 + MiniMax-M3 当时行为（14 轮）

经过 14 轮 manual_stable 任务实机（03 阶段 1 共 9 轮、03 阶段 2 共 1 轮、04 阶段 2
共 2 轮、05 阶段 3 共 2 轮），识别 MiniMax-M3 在长 prompt 下行为不稳定的具体表现：

1. **Content placeholder**：step content = "Output product 1"（47 字符）触发 partner
   `_is_placeholder_content`（len < 200 for .md/.py）
2. **Endpoint 错配**：prompt 明确禁止某些 endpoint，LLM 仍规划 `analyze` / `check_quality`
3. **路径处理不稳健**：prompt 明确"用绝对路径"，LLM 仍给相对路径导致 ENOENT
4. **Thinking-only 失败**：~50% 概率 LLM 只输出 reasoning 不输出 JSON（已通过 Bug #36
   phase 3 retry budget 1→3 修复，但 05 第 2 轮仍失败——retry 仍不够）
5. **编造文件名**：04 LLM 自主拼出 `index_status.txt`（用户从未要求）
6. **路径安全冲突**：LLM 想读 absolute path 但 partner 框架限制 task_working_dir 内

**共同根因（不只是 LLM 行为）**：
1. partner 框架 path security 设计 vs LLM 直觉冲突
2. BatchPlanner prompt 没教 LLM working_dir 是什么 + 怎么 cp 绕过
3. Bug #36 retry budget 3 仍不够（05 第 2 轮失败）
4. manual_stable 模式下 strict_reflect 仍触发（ADR 0004 未彻底关闭）

### A.5 当时识别的框架缺陷

不是 LLM 偷懒——是 partner 框架设计让 LLM 必然失败：
1. **path security 限制过严**：atomic_inspect_file 强制 working_dir 内，LLM 不知道
2. **BatchPlanner prompt 缺工作环境教学**：不告诉 LLM working_dir 路径 + cp 绕过方法
3. **Bug #36 retry 仍不够**：3 次 retry 至少 3/14 轮仍失败
4. **strict_reflect 治理关闭不彻底**：task_pipeline 显示 manual_stable 仍触发反思

### A.6 当时建议（现已部分实施）

**P0：partner 框架设计层修复（不是改 prompt）**
1. BatchPlanner prompt 增加 working_dir 实际值 + 怎么 cp + 禁止编造文件名
2. 放开 atomic_inspect_file 对 absolute path 读权限（read-only 无安全风险）
3. Bug #36 retry budget 1→3→5 + retry prompt 极简化
4. manual_stable 模式彻底禁用 strict_reflect（修 executor.py 事件注册）

**P1：业务目标重新设计**
- 适合：read-only 调研（run_shell + cat/ls）、1-3 步线性任务、文件读取 + grep
- 不适合：长文档创作、完整代码生成、多步复杂规划

**P2：模型/Adapter 层评估**
- MiniMax-M3 LLM 在长 prompt 下行为不稳定
- 选项：切更稳定模型 / 拆 prompt 到多个 atomic step / harness 层加 output validator

### A.7 当时观察到的能力边界

经过 14 轮实机验证，partner manual_stable 模式当前适合：
- read-only 调研任务（run_shell + cat / ls / grep）
- 1-3 步明确线性任务
- 简单文件读取 + 字符串匹配

不适合：
- 写 200+ 字符的长文档（LLM placeholder 行为）
- 写完整 Python 函数（LLM 容易截断代码）
- 凭空创作项目内容（无上下文 LLM 容易编造）
- 多步复杂规划 + 自主 endpoint 决策

### A.8 当时产物清单

| 期望 | 实际 | 状态 |
|------|------|------|
| project_brief.md 8 字段真实填写 | 仍是 321 B 历史空模板（8 字段全"待补充"） | ✗ |
| partner_canary.md 设计文档 | 不存在 | ✗ |
| __init_canary_stub.py stub | 不存在 | ✗ |
| tests/test_harness_fstring_format.py | 不存在 | ✗ |
| verified_index.md（external/code 调研） | 不存在 | ✗ |
| self_evolution_integration_audit.md | 不存在 | ✗ |
| partner/mind/harness.py + batch_planner.py 共 4 处修复 | 实际生效（227 passed） | ✓ |
| tests/test_micro_planner_extraction.py 14 测试 | 实际生效 | ✓ |
| ADR 0005 决策记录 | 已写 | ✓ |
| change_log.md 完整追踪（6315+2298+4673 字节 = 13286 字节 14 轮记录） | 已写 | ✓ |

---

*创建: 2026-08-23*
