# Partner 文档体系

> ⚠️ **维护纪律**：Partner 自进化/自愈引擎每次触发时自动读取这些文档。
> 修改任何 Partner 代码后，必须同步更新对应文档。文档是 Partner 自我认知的唯一来源。

> **2026-09-08 单向 EGPL 迁移与多模态初验（ADR 0075 / Sprint 21）**：旧 `rl/` 运行目录和旧字段已移除，
> 每个实质项目动作均进行一次 MiniMax 结构化审议；新增长网页 observe、社区只读导航和登录复验三个
> Event。腾讯真实文章链已通过；阿里 CAPTCHA 与小红书登录浮层均 fail-closed；四平台已有 observe
> 证据。本轮 16 次多模态模型调用共 66,074 tokens，全仓 976 passed。详见
> [`decisions/0075-one-way-egpl-and-multimodal-browser.md`](decisions/0075-one-way-egpl-and-multimodal-browser.md)。

> **2026-09-08 EGPL 与项目假设引擎（ADR 0074 / Sprint 20）**：当前机制不再简称 RL，正式称为
> “经验驱动策略学习”。项目连续停滞时，注册 Event `project_hypothesis_propose` 调用 LLM 在白名单 Event、
> 有界参数和真实测量契约内提出 Candidate，隔离检查后才进入动作池。权威可证伪条件由代码生成，LLM 原话只作
> advisory。真实探针已拒绝两条超出 Event 测量能力的候选；完整回归 966 passed。详见
> [`decisions/0074-experience-guided-policy-and-falsifiable-event-candidates.md`](decisions/0074-experience-guided-policy-and-falsifiable-event-candidates.md)。

> **2026-09-08 LLM 引导双学习基线（ADR 0072）**：主动学习只指面向外部知识的选题、检索与证据更新；
> 自进化只指 Partner 对自身策略/Event/代码的受控修改；RL 依据可验证 Reward 帮助选动作，Candidate 是待验证干预。
> 04 已运行三次关键 MiniMax 判断的 GitHub+论文 Event，真实源码/PDF/札记进入 `partner_workspace/external`；
> 自进化增加 LLM diagnosis/critic，但生产仍由 baseline/candidate、pytest、rollback 硬门决定。QQ 默认仅发 PDF，机器 sidecar 留在审计目录。
> 详见 [`decisions/0072-llm-guided-external-learning-and-self-evolution.md`](decisions/0072-llm-guided-external-learning-and-self-evolution.md)。

> **2026-09-08 三环验收基线（ADR 0071）**：项目迭代、主动学习、自进化现在分别验收。项目看 Receipt/动作/
> 真实产物，主动学习看 Episode 到下一动作是否真的改变，自进化看真实 diff、匹配对照、回归和激活；新增四个
> 可调用审计 Event。已有策略的重复确认不再算新 Candidate。第三个生产代码 Candidate 已让 05 按轮读取不同
> 代码面；五实例新实跑中 01/02/04/05 获得新结果，03 的热进程旧白名单失败已进入自动学习、待重试闭合。
> 五领域 PDF 已改为不同视觉/信息架构。详见
> [`decisions/0071-three-observable-loops-and-domain-pdf.md`](decisions/0071-three-observable-loops-and-domain-pdf.md)。

> **2026-09-08 当前生产基线（ADR 0070 / Sprint 19）**：固定“双槽”已经由资源自适应仲裁替代；每 30 秒依据
> `MemAvailable`、5 分钟负载和 CPU 数重算 1–5 个槽，资源收缩时只自然排空、不杀在途任务。五实例脚本已真实
> 投递并验收：02/03 获得新业务结果，01/04 如实记重复负样本，05 首个行为型生产代码 Candidate 已通过
> baseline-fail/candidate-pass、聚焦回归、原子实施和 Event-first 激活链；随后第二个 Candidate 让 04 按轮次
> 读取不同 Harness 源码。旧“追加注释即算 Candidate”路径已封死。
> 这证明一个受限机制可自动实施，不等于通用代码发明或长期 RL 已成熟；详见
> [`decisions/0070-resource-adaptive-runtime-and-behavioral-code-candidate.md`](decisions/0070-resource-adaptive-runtime-and-behavioral-code-candidate.md)。

> **2026-09-07 原生项目闭环修复（ADR 0068）**：已确认“service 在、实例不动”的直接原因是五实例均被旧报告型失败累积锁在 `BLOCKED`。原生续跑现只通过规范 project_id 选择实例专属真实 Event，完成后由 Receipt/Reward 判定；失败形成 Episode，主动学习的 matched 实验必须绑定同一失败机制，随后返回项目。最多双槽、普通消息 `manual_stable`、Candidate 不自批生产均不变。全仓 `920 passed`、0 failed；03 已产生真实数值实验 Receipt，纵向业务改善和长期 RL 成熟仍需跨日期证据。

> **2026-09-07 QQ 生产修复（ADR 0067）**：QQ Official 用户 OpenID 按 Bot App 隔离，禁止把某一实例的 OpenID 复制给其他实例，也禁止用 03 relay 冒充其他实例送达。01–05 已各自恢复目标并通过同轮 `sent + acknowledged=true` 验收。该结果只证明五实例渠道恢复；同轮暴露的 03 false-success/project 串线、04 timeout 未学习和通用报告空转仍是 P0。

> **2026-09-03 生产运行基线（ADR 0059–0060）**：Campaign 只用于批量 benchmark、matched 对照和限额审计，
> 不能充当实例自己的思考方式。旧 Sprint18 常驻服务已停用，02 的每小时阶段报告已关闭。目标运行形态是：
> 每个实例沿项目 Receipt 连续行动；遇到真实失败、冲突、不确定性或知识缺口时插入受限主动学习/自进化，
> 验证后返回项目。全局层只做资源自适应仲裁，不定时替实例选题。该实例原生循环现已与 `manual_stable`
> 兼容方式接入生产：五实例均有真实调度证据，普通手动消息仍保持单次完成后等待。长期 RL 成熟度仍未通过。

> **当前运行基线（2026-09-02，ADR 0058）**：生产默认仍为 `manual_stable`。用户手动发消息，
> 实例确认收到、逐步汇报并在一次有界任务后停止。Sprint 18 隔离 Campaign
> `campaign_74833fad4af8` 已在第二个真实日期自动续跑：现有 `42` 个 WorkItem（`30 completed / 11 blocked /
> 1 cancelled / 0 active`），第二日期 4 个 matched pair 为 baseline `0/4`、Candidate `4/4`。
> 第二真实日期证据与 Campaign 历史继续保留，但专用 `partner-sprint18@...service` 已依 ADR 0059 停用，
> 不再由 02 发送周期阶段报告。它从未是普通消息的默认自治，也未改变生产策略。先读
> [`architecture/manual_stable_core.md`](architecture/manual_stable_core.md)。

> **Sprint 18 实施进度（ADR 0055–0058）**：双资格 Observation、可复算 selector/critic、RepairRecipe、
> 隔离 Candidate、Beta 后验和 Event 注册已实现；最新 `678` 条观察中 `539` 可学习、`94` 可晋升，另有
> 第二日期后最新为 `689 total / 547 learning / 94 promotion / 12 neutral`；旧转换器忽略显式 eligibility 的
> 75 条投影已 append-only 纠正。
> TargetDiff 的 raw uncertainty 诊断发现 Spearman `-0.0625`，因此停止盲调 acquisition 权重；随后由失败
> 自动选择 estimator-comparison Recipe，cross-fitted residual Candidate 在冻结三 seed matched 实验中赢
> `2/3`，但相关性仍接近零，故只进入 acquisition shadow。04 已形成绑定真实 Receipt、两份 Harness 源证据
> 和三条项目轨迹的 context Candidate。学习 observation、项目 Receipt、Campaign monitor 与渠道交付账本已
> 分离；历史误投影均用 append-only correction 修正。成熟度评估现在按 trajectory ID 只取最新 revision，
> 不重复计算 correction。连续实验真实统计为 baseline 26 / Candidate 29、Reward `-0.4 / 0.5931`、Wilson
> `0.6545`、历史 false-success=5；全仓 `726 passed, 2 warnings`。第三日期、Wilson、零 false-success、
> anchor 和 rollback 仍在长期验收，不得宣称已成熟。

> **2026-09-01 完成信号驱动持续学习（ADR 0054 / Sprint 17）**：首个真实日期队列现已结束，最新为
> 51 completed、37 blocked、0 active；因此当前不是仍在后台主动选择新题。56 个初始 WorkItem 曾进入持久队列，
> 每项目 14 组 baseline/candidate。Task 最终治理通过 durable ledger + named FIFO 唤醒 controller，实机从
> 最终失败到下一项进入约 5 秒，不再等 5 分钟。已修复 Candidate 包装器/本地 selector 线程池卡死、
> 受限网络导致 MiniMax DNS 失败、成功 observation 不释放槽位和中间 done 双终态。MiniMax 单模型门现由
> 2 个 accepted + 3 个 rejected 实验共同通过；完整 production readiness 仍 blocked。全仓 688 passed。
> 下一阶段的主动课程、自动修复、跨日期学习和受限晋升已写入
> [`sprint18_主动课程自动修复与受限晋升.md`](sprint18_主动课程自动修复与受限晋升.md)，状态为
> `Core Implemented / Real Longitudinal Validation Running`。
> Sprint 18 v2 已定义项目 A（Partner Harness 真实缺陷自动修复）、项目 B（成熟 Harness 机制实际采用）和
> 项目 C（TargetDiff/分子主动实验跨领域压力测试），以及三日期、统计、canary、rollback 完成门；目前仅
> 核心实现已完成，长期统计、跨日期与生产晋升验收仍在进行。

> **2026-09-01 即时串行执行（ADR 0053）**：实验与长期采样消息现在是不可合并的独立 FIFO 工作项；
> 04/05 各自完成一个 Task 的 Harness/Claim/交付/Reward 终态后立即执行下一个，不等待 30 分钟 scheduler
> 周期。清理宿主残留双进程并收紧跨来源 Claim 单路径合同后，两个真实项目的 v5 matched 结果均为
> baseline `-0.4`、Candidate `+0.8`。完整回归 679 passed；三日期窗、20/arm 与 Wilson 下界仍未满足，
> 所以这证明短期修复闭环与即时队列可用，不代表长期 RL 或 full promotion 已成熟。长期 supervisor
> 旧 supervisor 的每 5 分钟调度已由 ADR 0054 的终态信号替代；30 秒只作丢信号/崩溃 watchdog。
> 同一天不重复创建日期窗，下一真实日期到来时由运行中的 controller 补充。

> **2026-08-31 手动验收闭合（ADR 0046）**：文件交付已移到最终 Claim/来源真值门之后；“预期不存在”
> 固定为一次诚实探测；用户明确授权的只读主动学习固定走四个 Event，并显式保证不修改生产、不晋升。
> 最终全仓 623 passed；04 自动真实 canary 已证明负向观察与只读四 Event 主动学习均完成并获 QQ ACK，
> 当前 04 已用最终代码在线。这不是无界自治或通用 RL 完成。

> **2026-08-31 研究主动学习（ADR 0047）**：04 已用 Event-first 四步链真实读取 Codex/Hermes 源码与
> JitRL 论文，跨轮按 novelty 转选不同来源，并用证据结果更新后验。研究轨迹单独记录
> `learning_progress=true`，不再被错误负奖，也不冒充 `business_progress`。JitRL 的一次语义假阴性已通过
> append-only `semantic_alias_v2` 纠正。Candidate 仍只在 shadow，未修改生产、未开启自动续轮。

> **2026-08-31 证据到代码 Candidate（ADR 0048）**：互补的 Harness/JitRL 证据已被编译为最小
> `research_adoption_context_shadow` Event Candidate，并在 01–05 五个真实项目快照上完成同预算匹配对照。
> 首次失败实验保留；修复预算包装漏计和 Receipt 截断后，证据召回 `0.0667 → 1.0000`、7/7 硬门通过。
> 这证明上下文准备改善，不等于下游业务或长期 RL 已改善；Policy 仍 inconclusive、生产未变。

> **2026-08-31 下游 Agent 对照（ADR 0049）**：Candidate 已在 Receipt 承接、JitRL 机制和 Hermes
> handoff 三类冻结任务上由独立本地消费者完成 3/3，Baseline 为 0/3；过程中真实发现并修复预算截断与
> 跨语言/来源去重缺陷。外部 LLM 对照因项目数据没有明确外发授权而未执行，不能把本地确定性结果写成
> 通用 LLM 或业务持续改善。Policy 仍 inconclusive，生产与 Campaign 状态不变。

> **2026-09-01 生产成熟度总门（ADR 0050）**：学习型 Candidate 现在必须同时通过两个外部模型家族、
> 六轮跨项目/跨时间窗真实交付、每臂至少 20 个长期 RL 样本及回滚演练，才能独立激活。当前
> research-adoption Candidate 的正式 attestation 为 `blocked`，因此没有接入生产；完整回归 653 passed。

> **2026-09-01 MiniMax 与真实业务 Candidate（ADR 0051）**：用户只授权不可逆脱敏派生上下文发送给
> MiniMax-M3，DeepSeek 未调用。MiniMax 单模型门以两次独立实验、6 个 matched pair 通过；04 严格业务
> pair-15 为 baseline `-0.4`、Candidate `0.8`，双源与 10 个 Claim 全通过。但完整渠道送达仅 3/6、只覆盖
> 一个项目和一天，长期 RL 也未达到 20/arm、多项目、多时间窗与回滚门；生产仍 `blocked`。完整回归
> 666 passed。

> **2026-09-01 受限生产接入与长期采样（ADR 0052）**：完整 `promoted` 门保持阻断；新增独立
> `control_policy.canaries`，仅 04/05 的研究/文献/GitHub/源码双源任务可命中，最多 12 个 accepted task、
> 7 天，Claim 失败/false-success/连续失败会自动回退。真实 rollback drill 已通过。04/05 已连接 QQ 与
> MiniMax，首日两个项目登记 5 baseline + 5 candidate；04 首个 Candidate 通过，05 旧进程负例诚实保留。
> 每日采样动作不写 Reward、不伪造日期；独立 supervisor 只有在全部 readiness 门真实通过后才可按 Event-first
> promotion/activation，当前为 `blocked / activated=false`。完整回归 676 passed；这仍不是“长期 RL 已成熟”。

> **2026-08-30 最新生产负样本（ADR 0043）**：长期 Campaign 已由用户显式停止。04 的手动四 Harness
> 对比任务成功读取四源并生成 Markdown，但因下游 PDF Event 未解析上游生成文件的相对路径而整体失败；
> 随后又暴露引文语义无关、跨来源推断、消息有步骤无发现，以及 manual Episode 未自动进入诊断/repair
> 闭环。当前应表述为“证据管道与限域策略学习部分可用”，不能表述为通用主动学习、自进化或持续 RL
> 已完成。见 [`decisions/0043-manual-04-production-negative-episode.md`](decisions/0043-manual-04-production-negative-episode.md)。

> **2026-08-30 消息链修复（ADR 0044）**：04 的旧进程遗留超时通知已终止；文本发送新增跨重启 ACK
> 审计与按 Event 限域的语义去重。四轮真实 canary 逐项修复纯文本误注入文件、未返回真实内容和跨任务误
> 去重，最终完整消息链获 QQ ACK；全仓 609 passed。这不代表 ADR 0043 的主动学习/RL 缺口已闭合。

> **2026-08-30 长期 Campaign 与负样本 RL（ADR 0042，历史实跑快照）**：正式
> `campaign_abf9e34bf6af` 已完成 29/30 WorkItem，01–04 均有真实完成，05 三轮离线学习完成；最多双槽。
> Campaign 后续已由用户显式停止，不是当前运行状态。
> 终态成功不再被二次改写为失败，05 不再被连续业务波次饿死，可归因失败会进入策略 reward 分布但不能
> 自动晋升。最终回归 560 passed。见
> [`decisions/0042-campaign-terminal-wave-and-negative-rl.md`](decisions/0042-campaign-terminal-wave-and-negative-rl.md)。

> **2026-08-30 首个真实业务持续改善与限域晋升（ADR 0041）**：04 在 OpenClaw、Hermes、Codex
> 三组独立 matched 任务中 Candidate 3/3、Baseline 1/3，逐对 reward gain `[0.9,0.9,0.0]`；
> 经阶段 556 项回归、`policy/promoted` 和独立 `policy/activated` Event 后仅对 04 文件证据报告生效。
> 首次 Campaign 已被 ADR 0042 的修复版替代；这不是五项目或模型权重 RL 已完成。

> **2026-08-29 Event-first 纠正**：Event 是 Partner 唯一运行入口；Candidate 是带有
> `execution_contract` 的待验证策略制品，不是绕开 Event 的第二套 Skill 运行时。此前用硬编码奖励测试
> 产生的 BDK “真实晋升”已作废并从生产控制映射移除。先读
> [`architecture/event_first_self_evolution.md`](architecture/event_first_self_evolution.md) 和
> [ADR 0032](decisions/0032-event-first-candidate-restoration.md)。

> **首次真实实验（ADR 0033）**：02 已完成匹配的 sklearn Event 与 BDK Candidate 五折实验。
> BDK RMSE 1.544721，最佳 sklearn 1.541555，结论 `inconclusive`、未晋升；这证明 Event-first 执行链
> 可用，不证明 BDK 在该任务上更强。见
> [`decisions/0033-first-event-first-bdk-experiment.md`](decisions/0033-first-event-first-bdk-experiment.md)。

> **2026-08-30 BDK 原意校正（ADR 0034）**：FunctionPool 只是旧数值器官；BDK 北极星是
> “观察—联想—多假设—主动求证—记忆巩固”的世界模型底层。长期 Campaign 已加入业务优先、学习配额、
> Candidate 单次 Event 验证和治理密度门，但 production 仍为 `manual_stable`。见
> [`architecture/world_model_bdk.md`](architecture/world_model_bdk.md)。

> **2026-08-30 孵化区收敛（ADR 0035）**：已验证实现已迁入 Partner，运行时不再依赖
> `partner_test`。Transformer HypothesisProvider 完成三领域 Event-first 匹配实验；精度不变、候选工作集
> 缩小，但因只有一次合成实验保持 `inconclusive`、不进入生产。

> **2026-08-30 压力门（ADR 0036）**：补充 120 个稀疏/噪声/离群/OOD 样例和 110 条真实 Episode
> 只读 shadow；修复候选删真族、固定参数与离群证据问题。压力指标通过，但真实可学习轨迹目前只有 04，
> 03/05 reward 全零，所以 Candidate 继续 `inconclusive`，不得进入 RL 或 production。

> **2026-08-30 双层主动学习（ADR 0037）**：`bdk_transformer` 的对象层主动求证与
> `hermes_external_learning` 的 Agent 元层实验选择已统一进入 Partner。真实 Episode 已完成失败 taxonomy
> 顺应和匹配诊断，但尚未自动 repair 或证明 RL；见
> [`architecture/two_level_active_learning.md`](architecture/two_level_active_learning.md)。

> **2026-08-30 首个 bounded repair（ADR 0038）**：主动学习继续追因后确认多数所谓 unclosed generate_text
> 是 dependency-skip 缺少权威终态。现已补齐 task-log terminal 和 Episode `skipped` 语义；历史 replay
> 只消除 9 个有证据假阳性，保留 1 个未知中断。selector v2 已转向正分 repair，仍未声称业务/RL 改善。

> **2026-08-30 因果回放与 fresh canary（ADR 0039）**：最后 1 个“未知”被证明是 evaluator v1 漏读
> 无-remediation 的依赖 skip；plan DAG v2 纠正为 10/10，真实隔离 PlanExecutor 并发 canary 六门全过。
> 这闭合了终态可观测性问题，但仍不是业务任务或 RL 效果。

> **2026-08-30 显式承接意图（ADR 0040）**：10 个真实 outcome Episode 暴露 `inputs!=[]` 被误当项目
> continuation。现在由 `continue_from_project`/`previous_receipt_id` 表达承接；独立源码/holdout 任务可
> 建立 Receipt，明确漏交接仍 hard reject；只有真实承接获得 handoff reward。隔离 Event-first Candidate
> 九门全过，全仓基线见 `docs/testing/last_pytest.txt`。

---

## 零、当前进度入口

当前运行状态、已完成闭环、实机证据、已知边界和下一阶段优先级统一见
[`current_status.md`](current_status.md)。其他 Sprint 文档均按历史记录理解，不应用来判断当前服务状态。

**2026-08-28 进展**: 12 个 framework bug 修复（#38-#50 + #55）+ 14 个 framework ADR（0007-0020）+ cognition ADR 0021–0022。
03/05 的 75%/85% 是人工阶段估计：读取、生成、落盘/发送子链已有实证，但最近复核任务的最终治理
因 `unlinked_previous_receipt` 拒绝，不能表述为完整端到端成功。Bug 修复阶段为 351 passed；
加入 Gate B sidecar 与 Gate C shadow selector 后当前完整基线为 **366 passed**。
详见 `current_status.md` 的 2026-08-28 节。

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
│   ├── event_first_self_evolution.md ← 🔴 Event/Candidate/Experiment/Policy 与 BDK/RL 边界
│   ├── world_model_bdk.md             ← BDK 世界模型原意、Transformer/RL 分工与融合门
│   ├── two_level_active_learning.md    ← 对象层+Agent元层主动学习、真实诊断闭环与后续 repair 门
│   ├── user_observability_and_reports.md ← 三阶段消息、领域报告与验收硬门
│   ├── manual_stable_core.md       ← 当前生产路径：手动触发、逐步消息、单轮结束、双槽轮换
│   ├── cognition_shadow_integration.md ← BDK 认知账本的 shadow-only 融合合同与晋级门
│   ├── context_selection_candidate_gate_c.md ← 首个上下文选择 Candidate 的变量隔离与匹配任务合同
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
├── architecture/experience_policy.md ← 轨迹、可验证奖励、候选策略与晋升门
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
