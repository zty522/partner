## 2026-09-08：单向 EGPL 迁移、强制 LLM 审议与多模态初验（ADR 0075 / Sprint 21）

- 当前代码、脚本、测试和 workspace 只使用 `share/mind/governance/experience_guided_policy/`；旧 `rl/`
  目录、`rl_decision_key`、`rl_algorithm`、`native_bandit` 旧值及运行时双写/回退均已移除。WebRL、JIT-RL
  等仍作为外部论文/项目专名保留，不是 Partner 当前机制名。
- 生产配置将 EGPL 审议设为 `every_project_action`：每个实质项目动作调用一次 MiniMax；停滞 Candidate
  另有 proposal 与独立 critic。LLM 必须输出事实、类比边界、约束、正反假设、反例和安全下一动作，
  但不能新增 Event、修改硬证据或自行晋升；专用 API purpose 为 `project_action_deliberation`。
- Sprint 21 已注册 `multimodal_browser_observe`、`multimodal_community_read`、
  `multimodal_login_resume`。导航前后均检查 HTTPS hostname 白名单；每页保存 DOM、截图 SHA-256、
  Qwen3-VL 描述和 MiniMax 对照判断；禁止自动输入凭据、点赞、评论、关注、上传和发布。
- 真实验收覆盖腾讯、阿里、小红书、微信公众号四个平台。腾讯首页→文章只读链成功，共 4 次模型调用；
  阿里第二页遇到 CAPTCHA，真实测试发现旧逻辑 false-success，现已增加 `challenge_detected` 硬门；
  小红书出现登录浮层且无安全帖子链接，正确 fail-closed；微信公众平台首页 observe 成功。
- 16 次本轮多模态模型调用实际消耗 66,074 tokens（Qwen3-VL 8 次、MiniMax-M3 8 次）。调用量以有意义的
  决策点为准，不为消耗而空调用。全仓生产 Conda 环境回归 `976 passed, 2 warnings, 0 failed`。
- 首版项目审议虽调用模型，但 1,800-token 截断使最近 10 条只有 1 条可解析，不能算有效参与。修复为
  5,200-token 主审议、嵌套 JSON 解码和按需 reduction 后，重启后的首两条新 selection 均为
  `llm_participation.completed=true, calls=1`；旧失败选择保持不可变，不倒填成功。

## 2026-09-08：经验驱动策略学习与可证伪 Event Candidate（ADR 0074）

- 当前 UCB 动作选择正式命名为 EGPL，而非笼统 RL；它学习终态动作价值，但不训练 LLM 权重。
- 新增 `project_hypothesis_propose` Event：三次非正项目 Reward 后由 LLM 选择一个受限实验方向，机器用 Event
  的真实指标生成 measurement contract、主假设和否证条件，之后作为 canary arm 加入动作池。
- 五项目的 variant 已连接到实际 Event 参数。Candidate 缺测量契约、未发出声明指标、重复或非正 Reward 均拒绝。
- 六段推理契约已进入提案/critic：现实证据→联想→约束→反方讨论→顺应→行动反思；三条进度账本不得混写。
- 首次生产探针真实运行 03/02 两个候选，但其 LLM 叙述超出 Event 能力且 Reward=-0.1；现已诚实拒绝并将该模式写成硬门。
- 完整回归 966 passed，2 warnings。下一阶段是五领域语义合法 matched 实验和跨日期业务改善证据。

## 2026-09-08：语义 Reward 与生产动作 Bandit 收紧（ADR 0073）

- 修复“新文件名/新随机数=业务进步”：终态先生成稳定语义签名，ID、路径、时间和裸测量值变化不再获得 novelty；显式 baseline/counterfactual 改善仍可计新证据。
- 01–05 的生产下一动作已从轮次取模迁移到 UCB contextual bandit；02 保留首个四阶段依赖启动，完整一轮后也进入 Reward 选择。每次决策写 arm 统计和选择原因，并回填 trajectory 的 `policy_decision/native_action_id`。
- LLM 参与两处：Bandit 前两项近似平局时做受限语义二选一；Partner 内部失败诊断时提出因果机制、反证和最小实验。两处均不能创造 Event 或批准 production。
- 一次重复只做 RL 负反馈并换 action；连续语义平台或真实失败才触发自进化，减少固定五步诊断空转。
- 遗留 `agent_active_learning_*` 仅作 Event ID 兼容，结果明确标记 `partner_self_evolution`；“主动学习”继续专指外部 GitHub/论文知识获取。
- 尚待跨日期数据证明业务持续改善，当前不宣称传统深度 RL 或长期 RL 成熟。
- 生产短跑已完成：03 出现一次真实 LLM 近似平局评审；01/02/03 的固定结论主要被压为 `-0.1`；04 不同源码证据短暂为 `0.85`、耗尽后回落；内部诊断写入 LLM 因果与反证。短跑同时修复 `学习干预=True` 刷 novelty 和 duplicate 计数串入另一 action 失败预算的问题。
- 最终完整回归更新为 `962 passed, 2 warnings in 113.34s`；服务重启后继续按完成信号运行。

## 2026-09-08：LLM 外部主动学习与自进化判断上线（ADR 0072）

- 术语固定：主动学习面向外部知识，自进化面向 Partner 自身，项目迭代面向业务；RL 用可验证 Reward 学动作价值，Candidate 是待验证干预，五者不再混写。
- 04 新增生产 Event `external_knowledge_scout`：MiniMax 分别做知识缺口/查询、真实候选选源、证据综合与反驳；代码负责 GitHub/论文抓取、去重、hash、PDF/源码读取范围和真值门。
- 真实生产已克隆 `affaan-m/ECC`、`bytedance/deer-flow`，下载 `Learning From Failure`、MuMath-Code 等论文；第一轮 QQ 只交付最终 PDF。叶片分割误选及 MuMath 相关性偏弱均作为负证据保留。
- 外部资料统一落到 `partner_workspace/external/{code,literature,insights}`；成功与失败均 append-only 索引，并区分 `abstract_only` 与 `pdf_excerpt`，不再把下载当阅读。
- 自进化 Candidate 新增 LLM 因果诊断和机器回归后的独立 LLM critic；LLM 不能批准生产。真实复验判断三个白名单 recipe 已饱和，返回 `no_new_candidate`，未伪造新补丁。
- 修复新外部 Event 被旧验收器误判 `missing_external_action`；QQ 默认只发 PDF，无 PDF 时仅发文字，机器 JSON/MD 留在本地审计。API 日志开始记录 provider token usage。
- 重启后的生产复验 Task `7bab1ca7-84c8-48d2-bac6-97c80a5dca26` 成功：选择 `alibaba/open-code-review` 与
  *Large Language Model Agent: A Survey*，实际读取 4 个仓库文件和 PDF 前 8 页，三次关键 LLM 调用，最终
  Receipt=`receipt_f89b3862b4fd`；QQ 仅发送 19,718 字节 PDF，随后自动进入下一项目动作，未触发假失败学习。
- 当前仍只能称“受限主动学习和行为型自进化可用”；外部知识到代码采用、跨日期 Reward 改善和通用自进化尚未达到长期成熟门。

## 2026-09-08：Sprint 19 三条可观察闭环与第三个代码 Candidate（ADR 0071）

- “迭代推进、主动学习、自进化”已拆为三条独立验收链，新增
  `project_iteration_audit`、`active_learning_effect_audit`、`self_evolution_effect_audit` 与
  `sprint19_acceptance`；一条通过不能掩盖另一条失败。
- 原生项目动作选择会记录 `project/action_selected`；学习后改选记录
  `active_learning/project_action_selected`，包含正常选项、实际选项、全部备选和原因。消息/JSON 会明确显示
  `动作选择=<id>；学习干预=True/False`，不再只能从后台猜测学习是否生效。
- 修复重复自进化计数：已有策略复查改记 `no_new_candidate / production_effective=false`，不再把
  `already_effective` 冒充新生产 Candidate。
- 修复其下游 Reward/Receipt 串线：05 生产复验 `86e1e1d6-ce35-4bb7-95aa-5a36f4f17102` 被正确记为
  `candidate_no_change_observation_recorded`，不推进 ProjectState、`business_progress=false`；首次新观察
  Reward=0.60，相同观察再次出现为 -0.10，避免 RL 学会反复确认既有策略。
- 五实例测试脚本的 05 指令不再硬编码“每轮造 Candidate”，恢复四类 Event 的项目轮转；只有真实机制与
  证据足够时才进入隔离对照和可逆实施。
- 第三个行为型 Candidate `code_candidate_code_surface_novelty_20260908014833` 已真实实施：05 重复结果 Episode
  → 新的代码面轮换策略 → baseline fail / candidate pass → 15 项聚焦回归 → promote/activate 完整事件链。
- PDF 已按 01–05 分成编辑简报、研究记录、实验手记、源码札记、工程审查五种标题/配色/页眉，不再统一使用
  居中蓝色“证据驱动执行报告”模板；机器 JSON 仍保持统一合同。
- 五实例脚本实跑最终 5/5 均得到 `reward=0.85 / business_progress=true / duplicate=false`。03 首轮命中热进程
  缓存的旧 Event 白名单而诚实失败；五步学习完成后，经 rolling-upgrade 兼容修复 redrive，实际把下一动作从
  integrator smoke 改为 temperature sweep，4/4 模拟过门、最坏相对能量漂移 `1.9449e-05`，成功 Receipt 为
  `receipt_9b7735ed7653`。生产 `sprint19_acceptance` 三环总审计已 passed；这仍是短期受限闭环，不称长期 RL 成熟。
- 最终完整回归 `949 passed, 2 warnings in 136.95s`。

## 2026-09-08：资源自适应五实例与首个行为型代码 Candidate（ADR 0070 / Sprint 19）

- 固定双槽已退出当前生产合同。scheduler 每个 watchdog sweep 读取 CPU、`MemAvailable` 与 5 分钟 load，
  在配置上限内动态选择 1–5 个实例；资源下降时不抢杀在途任务，只停止补位并自然排空。00:23 实测为
  22 逻辑 CPU、约 10.5 GiB 可用内存、load 1.90，选择 5 槽；服务实际运行 01–05 五个独立进程。
- `scripts/run_seed_all.py` 现在覆盖 01–05，使用唯一 run-id，并可用 `--instances` 做子集验收；显式 inbox
  优先于重启恢复/自动续跑，避免测试消息被旧项目任务抢占。
- 同轮五实例真实结果：02 分子生成得到 attempted=100、valid=85、unique=85、mean QED=0.537781，Reward=0.85；
  03 数值实验 Reward=0.85；01 与 04 虽执行成功，但结论重复，均 Reward=-0.1，未伪装成业务进步。
- 05 已从真实 `outcome.duplicate_semantic_result` 轨迹生成并实施首个会改变行为的生产代码 Candidate
  `code_candidate_md_novelty_20260907234635`：baseline probe fail、Candidate probe pass、聚焦回归 13 passed，
  生产参数策略按 native turn 改变。其 `candidate/proposed → experiment/started/completed → policy/promoted →
  policy/activated` Event 链、Experiment、PromotionDecision 和 rollback preimage 均已落盘。
- 第二个机制同类、领域不同的 Candidate `code_candidate_research_novelty_20260908005532` 也已完成
  baseline fail / Candidate pass，聚焦回归 15 passed 后激活 `turn_source_rotation_v1`。04 随后真实读取
  Codex rollout-trace README（8345 bytes，SHA-256 `5d8c177a38dec8e8...`），保存逐字摘录并获得文件 ACK。
  对应两个 04 实跑 Task（DeepSeek→Codex）均为 `reward=0.85 / business_progress=true /
  duplicate_outcome=false`；后续轮转耗尽新来源后重新出现 `-0.1`，说明防刷门仍生效。
  前两次构造器缺陷（错误 baseline project、同秒 pyc 缓存和 diff 换行）均被 hard gate 拒绝并保留为
  rejected 负证据，修复后才实施。
- 旧 `apply_pipeline` 的 comment-only 自动 diff 已关闭；没有真实 unified diff 的提案一律 `skipped_no_diff`。
- 05 的 Candidate 产物已复制进当前 Task 工作目录后再验收；真实运行中 JSON 上传成功且最终验收通过，
  不再出现“代码生效但 project/no_real_artifact”假失败。
- RL 的准确边界：Reward/后验目前负责问题与策略选择，主动学习负责找高价值失败，Candidate 控制器负责
  生成/验证/实施。一次成功尚未证明跨机制通用生成、跨日期持续改善或长期 RL 成熟；01/04 的负样本正是
  当前下一轮优化输入，不能删除。
- 最终完整回归：`942 passed, 2 warnings in 327.70s`；新增 05 duplicate Episode 必须走真实行为型代码
  Candidate、动态资源收缩自然排空、五实例投递等回归。
- 01:29 的 7192 条全历史 evolution ledger 可验证至当时 head；审计同时显式报告退休 writer 留下的 3248 条无幂等键记录、
  11 个并发 hash fork 和 192 个 sequence collision。其单条 hash 均通过，异常仅在 2026-09-07 前兼容；
  当前带 event_id/idempotency_key 的事件仍按严格连续链验证，不修改旧记录。

## 2026-09-07：五实例真实轮转已通，Reward 防重复与机制级学习门落地（ADR 0069）

- 生产长跑已经生成五实例真实 Receipt；01/03/04/05 的新 Event 摘要进入 trajectory 后为 `reward=0.85`，02 已完成真实分子合成基准 Receipt，旧 generic finding 阻塞已凭代码与测试证据解锁。
- 修复原生项目验收串线：handoff 中的 `continuation.md` 不再被当作当前输出；分子数值报告不再因“方法/效果”字样被误套研究引用门；机器项目产物不再被 Candidate Claim Ledger 门误伤。
- Harness 已正确解包 typed Event envelope，Receipt/trajectory 保存具体策略与业务指标，不再统一写“已完成本地微计划执行”。
- 主动学习 hard gate 已收紧：`failure_classes=[]` 的历史 native matched 实验不再算通过。真实 v3 被拒绝；v4 从 archived trace 恢复 `verification.acceptance_contract/implicit_handoff_artifact` 后通过，且 `production_effective=false`。
- Reward 现做跨 trajectory 的语义新颖性检查：同项目、同 Event、相同结论重复出现记 `duplicate_outcome=true`、Reward `-0.1`、禁止 policy eligibility，不能重复生成文件刷正样本。
- “执行成功但结果重复”已作为 `outcome.duplicate_semantic_result` 进入 Episode 与一次受限学习中断，学习后回项目；不再把无信息增益的成功静默当作下一轮业务推进。
- 02 已生产实跑完整的重复结果学习链并返回项目。Candidate 的 rejected/inconclusive 现在被视为有效实验终态而非 Event 故障；重复机制若缺少源动作，`candidate_changes_action` fail-closed，不允许假通过。
- runtime 单 sweep 只使用一次权威 slot 快照，消除轮转边界约 30 秒三子进程重叠；生产服务继续运行，最多双槽。
- 最终测试基线：`931 passed, 2 warnings, 0 failed`，详见 `docs/testing/last_pytest.txt`。当前可称“受限、可审计的持续项目/学习闭环已运行”，仍不可称“跨日期长期 RL 已成熟”。

## 2026-09-07：原生项目 Event 路由与学习应用闭环已实现，待生产轮转验收（ADR 0068）

- 已确认旧运行的真实停滞：service 外壳存活，但 01–05 全部因重复报告/错误规划进入 `BLOCKED`，scheduler 活动槽为 0。
- 原生 project 请求改为实例专属的确定性 Event 路由；普通手动消息不开放这些长期 Event，继续保持 `manual_stable`。
- 01 执行内容证据/风险队列，02 执行四阶段真实分子基准，03 执行可复现 velocity-Verlet 数值实验，04 执行 Harness 源码采用分析，05 执行 Hermes/Partner 源码合同与聚焦回归。
- 主动学习 matched 实验不再只跑固定 Claim/output-reference 夹具：native Episode 还必须匹配到同一实例、规范 project_id 与可执行 Candidate Event；学习观察仍 `production_effective=false`。
- 双槽硬上限为 2；重新授予槽位会复位上一量子的失败预算，一个真实项目步或一次有界学习重试后让槽。
- 最终全仓回归：`920 passed, 2 warnings`，0 failed，110.85 秒。03 已在真实服务中生成首条数值实验 Receipt；其余实例轮转仍在继续。此结果证明工程合同无回归，不等于长期 RL 已成熟。

## 2026-09-07：五实例 QQ 恢复 5/5；项目推进真值仍有 P0 缺口（ADR 0067）

- 根因已确认：五个 QQ Bot 的配置和 WebSocket 都正常，失败来自 Hermes 新增的跨实例硬编码 OpenID。QQ Official OpenID 按 Bot App 隔离，03 的用户 OpenID 对另外四个 App 无效，故 01/02/04/05 返回 HTTP 400“用户/群不存在”。
- 已改为每个实例只从自己的私聊入站历史恢复 App-scoped OpenID；禁止兄弟实例复制，关闭 03 自动 relay 冒充。历史 relay outbox 保留为负证据，不计交付。
- 生产服务重启后 01–05 均 `Bot ready`。一次真实 oneshot 发出 5 个项目任务，五份 delivery ledger 均新增 `sent + acknowledged=true`，QQ 渠道验收 5/5。
- 项目效果不能宣称 5/5：01/02/05 仍有通用报告/固定 findings 空转；03 出现失败提示却记 done、正 Reward、错误 project_id；04 planner timeout 未带 failure taxonomy、也未进入 learning observation。
- 完整回归为 `900 passed, 5 failed, 2 warnings`：QQ 新增/定向测试通过；失败位于 active-learning preflight canary、apply rollback 时间窗、两条 manual preflight 断言漂移及沙箱 user-systemd 测试。当前不得宣称全仓零回归。
- 下一优先级：先修 03 false-success 和 project_id 归属，再让 04 timeout 进入 Episode/主动学习，最后收紧 01/02/05 的真实动作、差异化 findings 与 NextAction 门。QQ 不再是当前阻塞项。

## 2026-09-04 ADR 0062 §6：decision_handoff 接入 + 真 overnight run 实证

- 上一轮 §4 写出了 decision_loop，但**只有接收方**——harness 那头没有 DecisionEvent 发送端，
  default_decision=noop 让所有 slot entry 都写出 noop、ledger 看上去饱满但行为没改进。
- 新增 `partner/mind/decision_handoff.py`:
  - `make_classification / make_decision / make_full_payload / validate_payload`
  - `make_preconditions_self_evolve / make_preconditions_external`
  - `signals_count(task_state)` 公开 helper（任务 3 在 instance_native 用到）
  - `recommend_next_action(...)` 启发式路由（按 failure_class → branch 表 + candidate/query/signals 推断）
  - `dispatch_to_decision_loop(workspace, instance_id, ...)` 单一入口，自动推断 preconditions / 验证 / 安全降级
  - `summarise_run(result)` 一行可读摘要
- `partner/governance/instance_native.py::recover_or_start` 把硬编码 default_decision=noop
  替换为 `dispatch_to_decision_loop(... task_state={..., phase=state.phase, reason=state.reason})`，
  让 recommend_next_action 真正按 phase 路由（BLOCKED → noop / WAIT_TASK + signals → active_learning / 信号不足 → noop）。
- `partner/governance/instance_native.py::handle_terminal` 在 native_learning_triggered 之后插入
  `dispatch_to_decision_loop(... classification={failure_class: bug_in_partner_source}, ...)`，
  让 task 失败产 DecisionEvent + 自进化/主动学习事件链——LLM 看 ledger 就能答"为什么这条"。
- `partner/governance/production_readiness.py::assess_production_readiness` 在
  `write_attestation=True` 后追加 `dispatch_to_decision_loop(instance_id=governance, ...)`，
  让 promotion→apply 形成 governance 自循环（候选一进 production_readiness/ 就触发决策循环）。
- 新增 `partner/evolution/overnight_canary.py`：`run_once(workspace, dry_run=...)` /
  `run_loop(...)` / 写 `overnight/canary_run` 事件，失败过多升 `overnight/many_failures`。
- 新增 `scripts/run_overnight_canary.py`（SIGINT-clean shutdown，120s 间隔，dry_run=0），
  实机已在跑（pid 242691）。
- 新增测试 `tests/test_decision_handoff.py`（24 例）+ `tests/test_overnight_canary.py`（7 例）。
- 全仓回归 **829 passed**（前 798 +31 新增），0 failed。
- **实证数据（已写入 share/mind/governance/evolution_events.jsonl）**：
  - overnight canary 4 次迭代：examined=1 / applied=0 / skipped=1（candidate_research_downstream_a420574f08ad
    因 decision=blocked 走 skipped_inconclusive_decision）/ failed=0 / elapsed=0.01s
  - 实例 controller `recovery` 5 实例全部 ok=true / status=pending_dispatch，新代码已生效。
- ADR 0062 §6 是这条契约来源——不新开 ADR、不新开 sprint。

## 2026-09-04 ADR 0062 §4：Sprint18 决策循环状态机 + 28-event 契约 + loop_watchdog

- 之前 active_learning / self_evolution / 外部检索三条路径没有清晰判定标准——同一个任务有时
  写出 6 条事件但看不出走哪条、有时什么都不写。"空跑"和"卡住"在 ledger 上不可分辨。
- 新增 `docs/architecture/sprint18_decision_loop.md`——状态机文档：单源真相，
  IDLE → OBSERVING → DIAGNOSING → DECIDING → 自进化/主动学习/外部检索/noop → COMPLETE → IDLE，
  每个转移都写一条带 schema_version=2 的 ledger event，**没有任何隐分支**。
- 新增 event 词汇表（见文档 §8），28 条事件，覆盖所有路径：
  observe/*、diagnose/*、decide/{decision,refused,dead_letter}_recorded、
  evolve/{diff_validated,git_apply_check,git_apply,git_add,git_commit,git_commit_failed}_recorded、
  apply/outcome_recorded、learn/{signals_extracted,topic_proposed,note_written,
  evolution_event_appended}_recorded、candidate/proposal_recorded、
  retrieve/{query_normalized,cache_hit/cache_miss,fetch_started,fetch_completed,
  fact_card_written}_recorded、evidence/intake_recorded、
  noop/reason_recorded、loop/{completion,stuck,branch_panic}_recorded。
- 新增 `partner/evolution/ledger.py`：hash 链 append-only helper，所有 loop 路径都过这里，
  reader 通过 `events_for_slot(workspace, subject_id)` 看每次 slot 全程。
- 新增 `partner/evolution/decision_loop.py`：单入口 `run_decision_loop()`，驱动状态机；
  DecisionEvent 必须含 `next_action ∈ {self_evolve, active_learning, external_retrieval, noop}`，
  preconditions 不满足就 refuse+降级到 noop，未知 next_action 走 dead_letter。
- 新增 `partner/evolution/loop_watchdog.py`：`detect_stuck()` 扫未写 completion 的 subject
  并写 `loop/stuck_recorded`；`summarise_subject()` 给 LLM 提供 per-slot 摘要
  （events 总数、是否 observe/diagnose/decide/complete、decided_action）。
- `instance_native.recover_or_start` 在 `_enqueue` 之前先调 `detect_stuck` 再调
  `run_decision_loop`，失败兜底 `curiosity/budget_run_failed`，主路径不被影响。
- 验收门槛达成：
  - 全仓回归 **798 passed**（前 786 + test_decision_loop.py 12 例新增），0 failed；
  - 状态机每条 transition 都对应一个 event，run 完后 `summarise_subject` 对每个 subject
    同时给出 has_observe / has_diagnose / has_decide / has_completion 四个布尔——LLM
    只要看 ledger 就能回答"做了吗/卡在哪/走的哪条"。

## 2026-09-03 ADR 0062 §3：Sprint18 self-evolution apply 通道 + 五实例 evolution budget

- 之前 promotion_decisions.jsonl 里 13 个 promoted 候选里 target 指向 partner/*.py = 0；
  candidate_policy.json 还是 `status=candidate` / `automatic_production_promotion=False`，
  promoted 与"production 落地"之间没有自动桥。
- 新增 `partner/evolution/apply_pipeline.py`（465 行）：
  - `apply_one(candidate, repo_root, *, dry_run=False)` 走 `git apply --check` →
    `git apply` → `git add` → `git commit`，commit message 固定
    `evolution(apply): <experiment_id> → <target_file>`；
  - 失败 → 升级 `policy/auto_apply_failed` 事件 + 72h `apply_blacklist.json` 冷却；
  - 路径用 `_is_safe_target` 限定 `partner/<pkg>/...py` / `tests/...py`，禁止越界；
  - 每条 apply 结果都进 `share/mind/governance/evolution_events.jsonl`。
- 新增 `partner/evolution/curiosity_bridge.py`（216 行）：
  - `propose()` 从 task_state.unresolved_questions 抽信号→topic，写 md note + evolution event；
  - `run_evolution_budget(workspace, instance_id, budget_seconds=60)`
    单次入口 idempotent，含 apply_pipeline dry_run 计数；
  - 默认 60s budget，失败兜底为 `curiosity/budget_run_failed` 事件。
- `partner/governance/instance_native.py::recover_or_start` 在原 `_enqueue` 之前 try/except
  调 `run_evolution_budget`，主路径不被牵连。
- `apply_pipeline.auto_rollback_recently_failed(workspace, max_age_hours=24,
  failure_threshold=1)`：当 governance 在窗口期内出现 auto_apply_failed ≥ threshold，
  反向回滚最近匹配 target_file 的 `promoted_applied` 提交。
- 真实证据（partner_workspace/share/mind/governance/experience_guided_policy/production_readiness/）：
  `apply_promoted_candidates(partner_workspace, dry_run=True)` 报告
  `examined=1 / applied=0 / skipped=1 / failed=0`，其中 1 个候选
  `candidate_research_downstream_a420574f08ad` 因 `decision=blocked` 被
  `skipped_inconclusive_decision` 拒绝——apply 通道真正识别 1 个真实候选，与 §3 之前
  "零可见落点"形成对照。
- 全仓回归：**786 passed, 2 warnings in 143.58s**（前 759 +27 新增：
  test_apply_pipeline.py 19 例 + test_curiosity_bridge.py 7 例 = 26）。
- ADR 0062 §3 是这条契约的来源——不新开 ADR、不新开 sprint。

## 2026-09-03 ADR 0062：动态 slot 预算 + Partner 真外部检索（用户校准后）

- **用户给的真正借鉴主题**：
  - bdk_transformer 真正该被借鉴的不是 BDK 跑函数本身，而是它的"主动学习方法论
    + 实验验证经验"（Param Isolation / FC v2 / MaxVar / ocamms_constraint）
  - hermes_external_learning 真正该被借鉴的是它实现外部检索的 **6 步流程**（list →
    filter → deep fetch → summarize → connections → skills），而不是它的离线笔记内容
- **dynamic max_active**：
  - `partner/governance/scheduler.py` 移除硬编码 2；改三层预算
    （override → /proc 估算 → FLOOR=2 兜底）
  - `instance_native.load_native_runtime_config["max_active"]` 改为引用同一个函数
  - 实机动态值：从硬 2 → 5（22 核 + 15 GB + load 0.07 的机器自然能跑满）
  - 生产 config 删 `instance_native_max_active` override 让 runtime 走 /proc
- **partner 真外部检索**：
  - 新增 `partner/governance/external_retrieval.py` — fetch(URL) + search(topic) +
    harvest_for_topic(topic)；12 小时 cache；`partner.knowledge.searcher` 已经覆盖
    arxiv / semantic scholar / crossref / pubmed 4 个后端，本模块复用为唯一入口
  - 新增 `docs/architecture/hermes-six-step-retrieval.md` 把 hermes process.md 6 步
    对位到 partner 工具链
- 生产 5 instance `partner-01..05.service` 全部 active running；controller 重启后
  `active=[04,05,01,02,03]` `paused=[]` `max_active=5`；OCR 全 5 实例用真实证据 unblock
- 全仓回归：`759 passed, 2 warnings`

## 2026-09-03 升级 v2.1：max_active=5 可配置，五实例并行跑项目+完整主动学习链

：max_active=5 可配置，五实例并行跑项目+完整主动学习链

- 之前 `partner/governance/scheduler.MAX_ACTIVE` 是硬编码 2，`set_active_slots` 在
  选 5 时直接 raise。现在抽成 `effective_max_active(workspace_root)` 函数，从
  `partner_workspace/config/partner_config.json::runtime.instance_native_max_active`
  读真实值；`MAX_ACTIVE_DEFAULT=2` 作 fall-back。`set_active_slots` 校验 + 落
  `data["max_active"]` 都用 effective 值。
- 顺带修 `instance_native.load_native_runtime_config` 的 `max_active`：`min(2, max(1, ...))`
  改成 `min(len(PROJECTS), max(1, ...))`，让 runtime arbiter 真的能扩到 5。
- 生产 config 现在写 `instance_native_max_active=5`，`scheduler.json` 持续
  `max_active=5` + `active_slots=[04,05,01,02,03]`，五 systemd service 全部 active
  running，`partner-instance-native.service` 用新代码 reconcile。
- 实时证据：03 实例 STEP 7/8 走 `execute_code` 真实计算并落到
  `share/projects/partner_framework_frontend/governance/receipts/0163_receipt_765871a50d26.json` 与
  `external_sources/`（HARNESS_PARALLEL ✅）。04/05 上轮 learning chain 已 DONE
  落 `learning experience recorded`，01/02 Bot ready 等下一个真实 task message。
- 完整主动学习+自进化 5 段式 evolution 事件（topic_selected → diagnosis_completed →
  query_proposed → candidate_bundled → matched_experiment_completed）每实例各
  append 一份进 `share/mind/governance/evolution_events.jsonl`，ledger 校验
  `verify_evolution_ledger()` 全 hash 通过。
- 全仓回归：`755 passed, 2 warnings in 122.16s`。prod 5 实例同时运行。

## 2026-09-03 升级：ADR 0061 v2（治理 receipts=真实推进）+五实例并行运行+完整主动学习链


- v1 把"读项目文件 + 写 governance/receipts"也判为缺真实外部动作。v2 改写合同：
  1) `share/projects/<project>/governance/receipts/*.json` 与 `governance/project_state.json`
     视为"治理产物=真实推进"，列入真实 artifact 集合；
  2) `share/projects/<project>/reports/` 仍是 `fake_artifact_paths`（报告空转源头不放过）；
  3) 真实外部动作信号词不变（exec:/web.fetch/pytest/code_write/atomic_inspect_file 等）。
- 旧 5 个 BLOCKED/被停实例 unblock 路径：调用 `unblock_blocked_instance(workspace, iid,
  evidence_paths=[latest_receipt, evolution_candidate, external_sources/summary.md], reason=...)`，
  每个实例挂 3 份真实独立证据，全部 ok。
- 完整主动学习+自进化链 `scripts/run_full_active_learning_cycle.py`：每个 enabled 实例跑
  topic_selected → diagnosis_completed → query_proposed → candidate_bundled →
  matched_experiment_completed 五个 append-only evolution 事件，落 `governance/evolution_candidates/
  cand_<id>_<ts>_<hash>.json` 一份 + `external_sources/alcycle_<UTC>/` 4 份引用 + summary.md。
  ledger 用 `verify_evolution_ledger()` 重算 hash 可校验。
- 五实例全部运行：scheduler 临时 `max_active=5` `active_slots=[04,05,01,02,03]`，五 systemd service
  active running。`partner-instance-native.service` 重启 dispatch 04/05，把其余实例的 slot 也
  让出，watchdog 后续自然轮转回 2。
- v1 备份在 `docs/decisions/0061-instance-native-real-action-contract.md.v1_backup`，v2 在原路径。
- 全仓回归：`755 passed, 2 warnings in 120.06s`（比 v1 752 +3）。

## 2026-09-03 紧急修复 v2：手动任务也走 ADR 0061，重启五实例观察新合同（ADR 0061）


- ADR 0061 v1 只在 `instance_native.handle_terminal` 拦截项目步，2026-09-03 上午上线后生产
  第一轮 01 实例 task `fdfd107c` 再次失败，但失败原因（"manual_outcome_rejected"）还是
  来自 `manual_runtime.record_manual_task_outcome` 的旧版 truth gate，没走 ADR 0061 合同。
- v2 把 ADR 0061 抽成 `partner/governance/real_action_contract.py` 这一个共享 helper，
  让 `instance_native.handle_terminal` 和 `manual_runtime.record_manual_task_outcome`
  （包括 manual_stable 兼容边界）调用同一份规则，避免漂移。manual 失败时返回
  `manual_real_action_contract_failed`，instance 失败时进入 BLOCKED + 写 `native_project_blocked`。
- 新增 `scripts/rebound_instances_to_real_action_contract.py`：把 BLOCKED 实例用
  `yield_blocked_without_evidence`（watchdog 最后手段）一次性 yield、写入
  `rebind_events.jsonl` 审计行，方便运维回查"哪一台是为了合同生效被显式诊断性唤醒"。
- 落实后启动顺序：
  1. 五实例 `state/instance_control.json` 和 `state/instance_scheduler.json` 都已清 paused；
  2. reboot `partner-instance-native.service` 让代码变更生效；
  3. `01` 实例进程真实接到 `01实例原生项目续跑`（新合同硬约束文案），planner/probe/prompt_builder
     完成后进 batch_plan（task `0754ca31-1728-43fe-b3a2-6795dd134cc8`）；
  4. 该 task 完成时 `record_manual_task_outcome` 走 ADR 0061，发现没有真实外部动作
     /真实 artifact/差异化 findings，返回 `manual_real_action_contract_failed`；
  5. `handle_terminal` 把 01 标记为 BLOCKED 并写 `native_project_blocked` 事件。
  这就是 P0 报告空转必须被工程化拦下的证据；同一合同现在对 manual + instance 双侧生效。
- `RuntimeConfig` 仍由 `manual_stable + instance_native_autonomy` 驱动；新字段
  `instance_native_max_repeat_findings=2`、`instance_native_require_external_artifact=true`
  写到 `partner_workspace/config/partner_config.json`，与 ADR 0061 默认值一致。
- 全仓回归：`752 passed, 2 warnings in 97.75s`（比 v1 749 +3：新增
  `tests/test_rebound_adr0061.py` 三例）。旧 Campaign systemd 服务单元文件保留为
  benchmark/matched experiment 能力，未启动、未排程，符合 ADR 0059 的"只看不动"定位。

## 2026-09-03 紧急修复：实例原生真实外部动作合同（ADR 0061）


- 实跑暴露 ADR 0060 的 report-only 漏洞：01/03/05 在 6 小时内共写 217 份 Receipt，其中 71/68/69
  的 `findings` 几乎是同一句"已完成本地微计划执行"，`next_actions` 全周期只新增 2 条；它们在 read+generate_text
  之间反复报告而非推进。
- ADR 0061 在 `RuntimeConfig` 上新增 `instance_native_max_repeat_findings` 和
  `instance_native_require_external_artifact`，在 `instance_native.handle_terminal` 的项目成功路径上
  校验：actions_executed 必须包含真实外部动作（exec:/web.fetch/pytest/code_write/data_write/external_query/
  scientific_run），artifacts 必须落到 `share/projects/<project>/external_artifacts/` 等非 reports
  路径，findings 必须与最近 N 份 receipt 的发现集合明显不同（窗口默认 2，Jaccard < 60%）。
- 违反任一项时实例立即 `BLOCKED` 并写 `native_project_blocked` 事件，避免无限报告循环；提供两条唤醒路径：
  `unblock_blocked_instance(evidence_paths=...)` 必须挂真实外部证据，
  `yield_blocked_without_evidence(reason=...)` 仅供 watchdog/诊断使用并写入事件日志。
- 旧 02/04 因连续失败被 BLOCKED 后长期未自醒的根因得到处理：watchdog 可以用真实证据唤醒，也可以用诊断 yield 让
  槽位轮转。完整的 02/04 解锁仍需手工指定外部证据（运行 `unblock_blocked_instance`），这点明确写在 ADR。
- 新测试覆盖：报告空转被 BLOCKED、唤醒必须挂真实证据、真实外部动作下的 happy path 推进；fixture 默认
  给 happy path 落一份真实 artifact，避免旧测试脆弱。
- 全仓回归：`749 passed, 2 warnings in 103.83s`，比 ADR 0060 的 746 增加 3 项。

## 2026-09-03 生产验收：五项目实例原生循环上线（ADR 0060）


- 旧 Sprint18 Campaign 服务保持停止/禁用；Campaign 只保留 benchmark、matched 对照和审计用途，不再负责
  长期项目心智、周期进度或固定课程。
- 新 `partner-instance-native.service` 已启用。五个实例均进入同一生产合同，最多双槽；实跑已完成
  `04→05→01→02→03` 调度覆盖，当前槽为 02/03。没有定时认知周期，30 秒 watchdog 只做漏信号恢复。
- 每实例状态机为 `PROJECT → (失败/冲突/不确定性/知识缺口) → Episode → observe → select → diagnose →
  repair proposal → verify → PROJECT`。普通手动消息仍保持 `manual_stable` 单次完成后等待，不被强制续跑。
- 04 真实 canary 已完成：失败 Task 归约为真实 Episode，确定性四 Event 学习使用 0 次 planner LLM，产出审查
  JSON、通过真值/交付门，并在权威终态后立即恢复原项目。历史误入 LLM、引用门误杀和失败均保留为负证据。
- 05 已连续完成两个真实项目动作并两次按终态让槽；01/02/03 均已真实经历
  `project failure → Episode → Event-first learning completed → project resumed`，不是仅创建了状态文件。
- 实跑修复了长期机制问题：学习任务被业务 citations 门误杀、重启后槽账本与 systemd 服务漂移、重启孤儿
  TaskInstance 永久 pending、学习前未强制 Episode 归约、planner `filename` 产物合同未补 writer；另修复控制器
  重启会重选首槽并打断在途任务，以及单实例“学习成功但业务重试继续失败”时无限占槽。现在每次失败最多一次
  学习中断；应用后仍失败就保留证据并让槽，稍后轮转再访。
- 当前生产配置为 `manual_stable + instance_native_autonomy`、enabled `04/05/01/02/03`、`max_active=2`、
  completion quantum=1。外部发布、登录、生产晋升和直接改生产源码仍受既有审批边界约束。
- 全仓回归：`746 passed, 2 warnings in 259.83s`。这证明实例原生控制闭环、五实例调度覆盖和真实学习恢复成立；不等于
  “长期 RL 已成熟”或五个业务均已持续改善，后者仍需多轮真实 Receipt/Reward/回归证据。

## 2026-09-02 架构校正：Campaign 不再充当实例心智（ADR 0059）

- 核对 02 消息账本确认，09:03、00:19 等“Partner Campaign 阶段进度”来自固定每小时的
  `Campaign 定时进度摘要`；它们是控制面报告，不是 02 项目取得新结果。
- 已停止并禁用 `partner-sprint18@campaign_74833fad4af8.service`，因此 02 不会继续发送这种周期消息；Campaign
  历史、Experiment、Reward 和失败证据均保留。
- Campaign 被降回其正确定位：批量 benchmark、matched 对照、限额审计。新增
  `report_interval_seconds=0` 静默合同，Sprint18 runner 默认静默；watchdog 只能恢复，不能制造任务或消息。
- 长期方向改为实例原生事件循环：实例连续推进自己的 Receipt NextAction；只有真实失败、冲突、不确定性、
  知识缺口或用户反馈才插入一次主动学习/自进化，验证后回到原项目。全局层只负责最多双槽资源仲裁。
- 该段是迁移前状态；实例原生运行时现已由 ADR 0060 上线，普通用户消息的 `manual_stable` 兼容边界保持不变。
  当时全仓回归为 `727 passed, 2 warnings in 104.14s`。

## 2026-09-02 前序状态：第二真实日期曾自动续跑（ADR 0058；常驻服务后由 ADR 0059 停用）

- Sprint 18 常驻服务曾经历 `/mnt/e` 短暂 I/O 故障和 WSL 重启后自动恢复；该事实证明恢复机制可用，但该
  服务后来因周期 Campaign 形式不符合实例原生认知而由 ADR 0059 停用。
- 新增真实日期 wake/window：04/05 两项目各 2 组，共 4 个严格 baseline/candidate pair。baseline `0/4`、
  Reward 均值 `-0.4`；Candidate `4/4`、Reward 均值 `+0.8`，每个 Candidate 产物均验证两个真实来源、
  Claim `2 passed / 0 failed`。模型调用累计 `17`。
- Campaign 现有 `42` 个 WorkItem：`30 completed / 11 blocked / 1 cancelled / 0 active`。取消项是修复前
  matched 外层错误提出、但从未执行的业务 continuation；matched 现在不会写 Project Receipt 或推进项目状态。
- matched 轨迹已进入学习资格，但保持 production-policy ineligible。Observation 最新为 `689 total / 547 learning /
  94 promotion / 439 positive / 238 negative / 12 neutral`。
- 修复长期成熟度统计对 append-only trajectory correction 的重复计数。按 `trajectory_id` 只取最新修订后，
  连续实验为 baseline `26`、Candidate `29`，平均 Reward `-0.4 / 0.5931`；Candidate Wilson 95% 下界
  `0.6545`，仍有 `5` 个历史 false-success，且只有两个真实日期。
- 所以当前结论是“长期自动循环已在线、第二日期表现改善”，不是“长期 RL 已成熟”。生产继续
  `manual_stable`，`control_policy.json` 未修改，未 promotion。独立业务持续性门也仍只有 3 个真实交付轮次、
  单项目、单日期；本地 matched 产物不会冒充 QQ/渠道已送达。全仓回归：`726 passed, 2 warnings in 108.22s`。

## 2026-09-01 22:28 最新状态：Sprint 18 持久控制器在线，空闲不烧额度（ADR 0057）

- 专用 systemd 服务 `partner-sprint18@campaign_74833fad4af8.service` 已启用并持续 active，PID `218595`、
  `NRestarts=0`。它每 30 秒做恢复 watchdog，Task 终态仍即时唤醒；没有高信息新证据时不占实例槽、不调用 LLM。
- Campaign 当前 `23 completed / 7 blocked / 0 active`，共 `30` 个 WorkItem；新增的一项是零 LLM 的阶段报告。
  Campaign 投影状态为 `blocked/WAITING_EVIDENCE`，但持久控制器在线，二者含义不同。
- `LearningObservation` 最新为 `678` 条：`539 learning eligible / 94 promotion eligible / 435 positive /
  234 negative / 9 neutral`。75 条旧 observation 依照轨迹中权威的
  `learning_observation_eligible=false` 追加纠正，不再虚增学习样本。
- 阶段报告即使渠道未 ACK，也只属于 control-plane report issue：不消耗业务 failure/retry budget、不创建业务
  Issue、不进入 RL，且不修改 ProjectState。本次历史误投影已追加 WorkItem/trajectory/Observation correction。
- 学习 policy 仍为 `learning_policy_2c75e59722b21322`；未修改 `control_policy.json`，未晋升生产。
- TargetDiff 先执行三 seed uncertainty reliability diagnosis：raw RF variance 与真实绝对误差平均 Spearman
  `-0.0625`、top-error recall `0.2381`，三个可靠性门全失败，结论为
  `uncertainty_weak_or_miscalibrated`。系统因此没有继续盲调 acquisition 权重。
- 新 `recipe_uncertainty_estimator_comparison_v1` matched 比较 raw tree variance 与只用已标注样本训练的
  cross-fitted residual uncertainty。Candidate 赢 `2/3` seed，平均 Spearman `-0.1306 → -0.0181`，
  top-error recall `0.2381 → 0.3333`，仅接受进入 `acquisition shadow`；仍无业务/生产效力。
- 04 Harness context Candidate v3 已绑定最新有效业务 Receipt `receipt_4fd68f29fc41`，在 9,000 字预算内
  选入 DeepSeek + Hermes 两份固定 digest 源证据与 3 条同项目正负轨迹；结果为
  `learning_progress=true / business_progress=false / production_effective=false`。
- 实跑中修复四个框架归因问题：共享治理证据未计入 Campaign artifact、Campaign 报告被误当业务负 Reward、
  Sprint18 本地 shadow 被强制要求 QQ delivery、学习 WorkItem 被外层 Campaign 错写成项目 Receipt 并触发
  continuation。历史采用 append-only trajectory/Receipt/WorkItem correction，原始失败未删除。
- 因修复前的错误 continuation，02/04 额外执行了校准、误差切片、外部索引、Harness mapping/adapter contract；
  这些任务均真实产出且送达，作为业务 Receipt 保留，但不冒充由学习策略正确触发的证据。
- 全仓回归：`720 passed, 2 warnings in 151.96s`。长期硬门仍缺三真实日期、20/arm、Wilson 下界、
  false-success=0、anchor 抗遗忘、真实 canary/rollback；因此不能称“长期 RL 成熟”或“已生产自进化”。

## 2026-09-01 前序状态：Sprint 18 首个真实负反馈闭环完成，长期硬门仍未完成（ADR 0055）

> 21:00 实时校正：Campaign `campaign_74833fad4af8` 的首批 02/04/05 工作已到终态；下一项
> `work_caa905a2ecae` 已持久排队。控制器按“终态立即续项、watchdog 仅恢复漏信号”实现，但本次从 Codex
> 会话重启长期宿主进程被执行额度拒绝，因此此刻不能写成后台仍在运行。队列、Campaign 与全部实验状态均保留，
> 恢复运行后从原 Campaign 接续。当前始终是隔离验收，不是 production promotion。

- `LearningObservation` 最新投影为 659 条，593 条可学习、89 条可支持晋升；424 正样本、235 负样本。
  02/04/05 三条成功实验曾因 evidence namespace 漏接被错误负奖，现以 append-only revision=2 纠正为
  `learning_progress=true / reward=+0.55 / policy_eligible=false`，旧事实未覆盖。
- 主动选题使用信息增益、不确定性、业务/迁移价值、成本/风险/重复惩罚和 critic；
  第一个真实高分机制是 `planning.output_reference_contract/typed_reference_unresolved`。
- 已实现机制级 Diagnosis/RepairRecipe、受限 CandidateBundle、正负 Beta 后验和最小探索概率；
  全部 Event-first 落账，学习 policy 不直接修改 `control_policy.json`。
- TargetDiff 已从“函数选择/RMSE 比较”转为真实样本主动学习。v4 在 seed 20260901 上胜出，但新 seed
  20260902/20260903 均未胜出；三 seed 只赢 1/3，虽平均 RMSE `2.380269` 略优 MaxVar `2.380523`，仍按
  预注册稳健门正式 `rejected_not_robust`。下一假设为不确定性校准与密度/误差切片条件化，不能晋升。
- 04 已完成四 Harness 首个未读证据选择与 matched shadow（delta `+0.332`）；05 已完成 typed output
  reference Candidate 的四硬门匹配实验；二者均 `production_effective=false`。下一条 04 未调查组合已排队。
- 三 seed rejection 已通过 Event→LearningObservation 进入课程，自动选出不确定性校准 R0 Candidate；该
  Candidate 用 labelled-only OOB error 做 RMSD slice 校准后又在三 seed 0/3 胜出，已再次 rejected。后验
  更新后回到 `WAITING_EVIDENCE`，没有机械调第三组权重。
- 全仓回归：`707 passed, 2 warnings in 161.93s`。
- 未完成：三真实日期、20/arm、Wilson 下界、false-success=0、anchor 抗遗忘、手动消息验收和
  完整 canary/rollback。因此长期 RL 仍未成熟，不得提前晋升。

## 2026-09-01 前序状态：Sprint 17 首日期运行已结束，长期闭环 blocked（ADR 0054）

> 19:35 校正：Sprint 17 的首日期队列已经运行完毕，Campaign 当前为 `blocked`、active slots 为空，
> 持久 WorkItem 为 `51 completed / 37 blocked`。这不是仍在后台主动选新题。连续实验已有 baseline 22、
> Candidate 25，均值 `-0.4 / 0.56`；但 Candidate Wilson 下界约 `0.609`、false-success=5，且只有一个
> 真实日期窗，所以 longitudinal RL、sustained business 和 production activation 仍未通过。下一闭环已
> 记录为 Sprint 18；v2 规格已经就绪，代码实施尚未开始。

> Sprint 18 v2 规格现已完成：包含双资格 Observation、主动课程 selector/critic、分级 RepairRecipe、隔离
> Candidate builder、可复算 policy update、三个真实项目、三日期抗遗忘和 bounded production canary。
> 当前状态仍是 `Specification Ready / Implementation Not Started`；不能把规格完成写成主动学习已经完成。
> 规范入口：`docs/sprint18_主动课程自动修复与受限晋升.md`。

- 生产默认仍是 `manual_stable`；当前另有用户显式授权的实验 Campaign `campaign_f8ace4ef3e44`，仅 04/05、
  最多双槽、7 天硬截止、MiniMax-M3 only。它不是普通消息的默认自治。
- 初始 56 个 WorkItem：两个真实项目各 14 组 baseline/candidate，严格 pair 顺序；当前失败/拒绝样本均保留。
- Task 权威终态双写 durable ledger 与 named FIFO。05 Task
  `4a73a6a6-5ecb-4ddb-bdd4-e8201b623886` 最终失败后约 5 秒进入下一 Candidate，不再等待 5 分钟。
- 实机追因并修复四个框架问题：`execute_candidate` 包装器线程池卡死；baseline 本地 selector 不必要入线程；
  受限启动环境无法访问 MiniMax；成功 manual observation 没有 generic LLM check 导致 04 槽位不释放。
  中间 `batch_plan done` 也不再发假终态信号。
- QQ/渠道故障时，本次实验允许 durable local observation 继续形成 Episode/Reward；但
  `delivery_confirmed=false`，不计 sustained-business，也不能宣称用户已收到。
- MiniMax 通用门现为 true：2 个独立 accepted + 3 个 rejected 实验共同入审计，accepted 共 6 matched pair、
  3 task families。DeepSeek 未调用。
- 持续 Candidate 实跑又定位并修复了确定性 Ledger 桥接缺口：`verified_source_evidence` 列表现可进入
  Candidate-only 机器账本；`# Claim Ledger`、`## 七、Claim Ledger`、带后缀标题均会被完整替换，避免
  MiniMax 不完整账本与机器账本并存。真实失败保留，修复后对同一产物复验为 `2 passed, 0 failed`。
- 最终重载后的 05 Candidate Task `e0e347d1-afaa-466e-9b31-cf25ad1b9c9e` 已真实得到
  `truth_audit.passed=true / reward=+0.8 / WorkItem completed`，完成后立即进入下一 baseline；其文件上传成功，
  但步骤/总结消息没有渠道 ACK，故仍诚实记录 `delivery_confirmed=false`，不计 sustained-business。
- `report` timeout 最终为 300 秒：180 秒实跑仍有一条 MiniMax 长报告在 181 秒超时并触发 fallback；提高
  单次调用上限用于避免重复模型成本，不改变“终态信号到达即派下一项”的调度语义。
- 完整 readiness 仍 `blocked / activated=false`：真实送达业务只有 3 轮、单项目、单日期；长期门未达到
  每臂 20、两项目、三真实日期、Candidate Wilson 下界与零 false-success。Candidate 失败 Claim 继续按
  `reward=-0.4` 留作负样本，不降真值门。
- 完整回归：`688 passed, 2 warnings in 129.97s`。
- 代码与运行详情见 ADR 0054；Sprint 推进见 `sprint17_完成信号驱动持续学习.md`。

## 2026-09-01 前序状态：04/05 即时串行执行与跨项目修复已实机通过（ADR 0053）

- 实验 USER_MESSAGE 不再合并 BATCH_PLAN；每个实例内部严格 FIFO，任务经 Harness/Claim/交付/Reward
  到达真实终态后立即投下一条，不等待 30 分钟 scheduler 周期。04/05 仍最多双槽并行。
- 遗留假 pending 已按 `event_queue/experimental_batch_plan_merge` 诚实关闭并 redrive，没有改写成成功。
- v4 失败把 05 Candidate 的具体缺口缩小到 11 条 Claim 中 1 条跨来源路径拼接；自动保留 Reward `-0.4`
  与 repair proposal。Candidate 合同随后收紧为“跨来源比较只写正文、每条 Ledger 只绑定一个来源”。
- v5 两项目 matched：04 与 05 baseline 均 `-0.4`；修复 Candidate 均 `+0.8`，truth score 分别 `0.85`、
  `0.8571428571`。这是一次真实的失败→诊断→Candidate→对照→改善闭环，但还不是长期成熟证明。
- 清理宿主残留双消费者后，最终 04/05 各只有一个进程；05 干净重试不再出现 `Updates.pdf` 假合同。
- 7 天长期 sampler 已从 6 小时 sleep 改为每 5 分钟幂等复审；已有任务仍是完成即接续，新日期到来后
  最多等待 5 分钟创建真实日期窗口。同一天不会重复投递，也不会伪造时间戳。
- 完整回归：`679 passed, 2 warnings in 150.29s`。完整 readiness 仍受 20/arm、三真实日期窗与 Wilson
  下界阻断；不得伪造日期或提前宣称 full promotion。

## 2026-09-01 历史状态：受限生产 Canary 已接入，双项目长期 RL 正在真实采样（ADR 0052）

- 完整 `promoted` 硬门没有降低；新增独立、可逆的 `control_policy.canaries` 层。当前
  `canary_65f4a91260f915a6` 限 04/05 研究/文献/GitHub/源码类双源任务、最多 12 个 accepted tasks、7 天。
- 一次 Claim 真值失败、一次 false-success、连续两次失败或样本预算结束会自动回退；Candidate JSON 仍为
  `production_effective=false`，避免把 canary 冒充 full promotion。
- 真实控制路径的激活→命中→回退演练已通过；readiness 的 rollback gate 现通过，但完整 readiness 仍因
  20/arm、两项目、三真实日期窗与 Wilson 下界不足而 `blocked`。
- 新增长期采样器：每天最多一次，04 `literature_github_learning` 与 05 `agent_self_evolution` 各两个
  baseline/candidate pair，最多双槽；采样动作本身只投递正常 USER_MESSAGE，不写 Reward、不改时间戳。
- 每 6 小时自动复审完整 readiness；只有四类门全过才按 Event-first 生成 promotion/activation 并关闭 canary。
  用户已授权该条件式激活，但当前首次复审仍为 `blocked / activated=false`。
- 04/05 已连接真实 QQ 与 MiniMax；2026-09-01 首个窗口已登记 5 baseline + 5 candidate Task。登记不等于
  通过，终态仍由 Harness/Claim/交付/Reward 硬门判定。
- 首个 04 v4 Candidate 已通过双源和 10-Claim 真值门；05 两个旧进程样本因 `Updates.pdf` 输入尾部误判为
  输出而记为 `reward=-0.4, false_success=true`。历史负例保留；检查器已改为同时读取 durable USER_MESSAGE，
  修复通过回归并由重启后的实例加载。
- 修复 DrvFS orphan lock：仅在精确 owner PID 已不存在时归档 stale inode；live/不确定 owner 仍 fail closed。
- 完整回归：`676 passed in 156.41s`。这证明 canary 与采样基础设施已生产运行，不表示长期 RL 今天已经成熟。

## 2026-09-01 最新状态：MiniMax 单模型与真实 04 业务 Candidate 已验证，长期 RL/生产仍阻断（ADR 0051）

- 用户明确授权不可逆脱敏后的项目派生上下文发送给 MiniMax-M3；本阶段没有调用 DeepSeek v4-flash。
  `direct_api` 不再隐式回退 DeepSeek，MiniMax 配置缺失时 fail closed。
- 两次独立 MiniMax-M3 下游实验共 6 个 matched pair、3 类任务，baseline 0/3、Candidate 3/3，脱敏
  audit 全通过；通用门只在 `single_authorized_general_purpose_model` 范围成立，不代表跨模型泛化。
- 04 Candidate 已接通 typed 双源（Hermes 源码 + JitRL PDF）、SHA-256/逐字引文、Claim 语义门、统一交付、
  Episode 与 Reward。修复了 8,000 字符结果截断、残缺 Claim 误放行、带空格 PDF 输入误判为输出等真实 bug。
- 历史 Canary-11 的残缺 C007 不改写、不作为权威正样本；Canary-12 完成双源与 7 Claim 验真，Reward 0.85。
- 严格 `experiment_research_business_v3 / jitrl_hermes_pair_15`：同 query、MiniMax、预算、冻结双源与真值门下，
  baseline 验证 0/2、Reward -0.4；Candidate 验证 2/2、10 Claim 全过、Reward 0.8，delta +1.2。
- pair-15 文件 push callback 成功，但 local inbox 任务没有 QQ 文本收件人，步骤/总结缺渠道 ACK，因此只记
  durable local observation，`delivery_confirmed=false`，不计作完整真实送达业务轮。
- 最新 readiness 仍 `blocked`：MiniMax 单模型门通过；持续业务仅 3/6 轮、1 个项目、1 个日期；长期 RL
  没有 20/arm、两项目、三时间窗、Wilson 下界和回滚演练。`control_policy.json` 未改、未 promotion、
  未 activation、未启动 Campaign。
- 完整回归：`666 passed in 153.82s`。

## 2026-09-01 历史状态：学习型 Candidate 生产总门已建立，当前明确阻断（ADR 0050）

- 新增不可绕过的 production readiness attestation：通用 LLM、持续业务产物、长期 RL 和 Event ledger 四类
  证据必须同时通过；学习型 Candidate 即使先产生 `policy/promoted` 也不能直接激活。
- 当前 `candidate_research_downstream_a420574f08ad` 审计结果为 `blocked`：外部模型 0 个通过、已送达业务
  轮次 0/6、Candidate 长期 matched 样本 0；生产策略没有修改。
- 全局已有 546 条历史轨迹，但最大可比组只有 10/13，且只覆盖同一天、同一项目并含 false-success，不能
  冒充长期成熟或转嫁给新 Candidate。
- MiniMax 脱敏调用因没有对该具体外部服务的数据外发授权而被安全门拒绝；本机没有可用离线模型。实验
  runner 已支持分别选择已配置的 MiniMax-M3 与 DeepSeek v4-flash，但尚未调用。
- 完整回归 `653 passed in 333.73s`。下一步必须先获得具体外发授权，随后按真实时间窗采集业务与
  RL 样本；在证据形成前不激活生产。

## 2026-08-31 最新状态：证据 Candidate 已通过三类本地下游 Agent 对照（ADR 0049）

- 新增独立下游评价层：冻结 Receipt 承接、JitRL 机制和 Hermes handoff 三类真实任务，以 truth/source/safety
  硬门评价 baseline/candidate；关键事实错误或越权建议直接 reward=0。
- 两次失败实验分别暴露“证据被末尾截断但引用仍声称选中”和“中文任务无法匹配英文机制、Top-2 来源重复”；
  均保留原结果，修复后另开 Experiment。
- 最终本地实验 `experiment_a420574f08ad` 使用独立的 `typed-evidence-consumer-v1`，Baseline 0/3、Candidate
  3/3，reward 均值 `0 → 1`；项目状态、Receipt 与生产策略均未变化。
- 外部 MiniMax 实验没有完成：沙箱网络失败，提升权限又因真实/派生项目数据没有明确外发授权被安全门拒绝；
  没有绕过。脱敏能力已实现并测试，但脱敏不等于用户授权。
- 结论仍为 `inconclusive`：已证明本地机器可消费性，尚未证明通用 LLM、真实业务产物或长期 RL 改善。
  完整回归 `649 passed in 118.33s`。

## 2026-08-31 最新状态：多源资料已形成代码 Candidate，并完成五项目真实上下文对照（ADR 0048）

- Hermes/Codex 的预算化交接与 JitRL 的 state/action/reward 动态记忆证据，已与 Partner 本地 Receipt、
  trajectory、context selector 合同共同编译为最小 Event Candidate；缺 code+paper、三类 Claim 或三个
  本地实现证据时 fail closed。
- Candidate 只能经 `execute_candidate → research_adoption_context_shadow` 运行，当前
  `production_effective=false`，未修改 `control_policy.json`，未启动 Campaign。
- 首次五项目实验 `experiment_0d6cd09d84d8` 暴露并保留两个失败：baseline bundle 实际超预算、01/05
  Receipt ID 被截断。没有用报告掩盖失败。
- 修复后新实验 `experiment_7cb945f44c9e` 在 01–05 五个真实项目快照上 7/7 硬门通过；同一冻结输入和
  9000 字预算下，证据召回 `0.0667 → 1.0000`，无跨项目轨迹泄漏。
- `context_selector` 预算现包含 provenance wrapper；Candidate 先保护 Receipt identity/next action，再分配
  canonical docs、轨迹和研究摘录预算。
- Policy 仍为 `inconclusive`：已证明上下文准备改善，尚未证明下游 LLM 业务产出或长期 Reward 改善。
  完整回归 `641 passed in 99.67s`。

## 2026-08-31 最新状态：04 已完成真实源码/论文主动学习与跨轮证据反馈（ADR 0047）

- 新增确定性 `observe → select → investigate → matched` 研究链：真实文件先做 SHA256/Git revision 核验，
  再按信息增益、任务价值、新颖性、成本与风险选问题/来源；Candidate 最多进入 shadow。
- 修复跨线程 asyncio 唤醒被受限环境静默拒绝导致的 inbox/步骤卡死；入口改用线程安全队列，纯本地研究
  Event 与 STOP_PROJECT Episode reduction 均走有界内联执行。04 的最终完成消息和 Episode 终态恢复。
- 实机 Task `fcfe6494-74c6-46b7-b754-ff4963c45743` 真实读取 Codex、Hermes 和 JitRL；选择 Hermes
  `context_compressor.py`，记录 handoff summary/持久记忆证据。匹配覆盖率 `0.372 → 0.889`，仅
  `accept_for_shadow`，`production_effective=false`、`promotion=false`。
- 续轮 Task `058ade34-6a6c-4596-aa9f-a4a162081059` 承接 investigations 并转选 JitRL，不重复首轮来源；
  matched experiment 幂等复核，不覆盖历史。
- JitRL 首次被字面提取器误判为证据不足；旧负记录保留。`semantic_alias_v2` 纠正后命中 6/6 概念，
  质量 `0.991`、支持后验 `0.917`，纠正证据为 `research_evidence_4f43170693f8ca68`。
- 纠正已通过正式 04 Event-first Task `6061bc5f-2752-4d92-9ee9-6f9f5edb1625` 再验证，Receipt
  `receipt_7e24675adee3`；不是仅靠函数直调得出的结论。
- 研究轨迹现在区分 `learning_progress` 与 `business_progress`：真实学习 reward `0.55`，但
  `business_progress=false`、`policy_eligible=false`。这证明研究选择闭环初步可用，不证明代码已自改、
  业务持续改善、生产 promotion 或无界自治。
- 最终全仓回归 `636 passed in 112.64s`；本轮未启动 Campaign，04 仅以本地消息通道完成有界验收。

## 2026-08-31 最新状态：04 负向观察与只读主动学习已通过真实执行层回归（ADR 0046）

- 多源报告现在先过 Claim/来源真值门再允许外部文件交付；被拒绝的候选文件保留为 partial evidence，不能先发
  给用户再把任务改判失败。
- verified-source 后处理会为每个真实来源写可解析、可回查的 grounded Claim；`source_identity` 字段与解析器
  合同已对齐，错误或悬空的 source/evidence 行不会继续进入成品。
- “预期文件不存在且不得生成替代物”的手动测试固定为一次 inspect；确认不存在是成功的负向观察，分类为
  `input_state / observation/expected_missing_input`，不再触发红叉、Remediation、Issue 或负 Reward。
- 用户明确授权的只读主动学习任务固定走 `observe → select → diagnose → repair proposal` 四 Event，审查结果
  显式记录 `production_effective=false`、不改 control policy、不 promotion。
- 失败消息保留真实对象、错误、责任方和 mechanism；文件成功终止原因已统一为中文用户语义。
- 只读四 Event 链曾在首个 observe 后卡住通用 worker thread；四个纯本地治理 Event 现改为确定性顺序执行，
  临时 workspace 真实 handler 4/4 完成并生成审查 JSON。
- 自动真实实例验收（无需用户发测试消息）已通过：负向观察 Task
  `e8f61ac9-ef0c-45cd-b773-784987023701` 为 done、Receipt `receipt_75d400128d24`、QQ 消息全部 ACK、
  Reward=0、monitor-only；主动学习 Task `b2af8f38-3bdf-4095-be6d-1f5b9c7d9fc9` 四阶段 4/4，Receipt
  `receipt_28e35c149972`，审查 JSON 已真实发送并 ACK。
- 首次自动 canary 暴露的错误负 Reward 已用 append-only revision 2 校正，旧行和 Task 日志未改写。
- 最终全仓 `623 passed in 224.46s`；相关回归 181 passed。04 已用最终代码重启并在线。当前没有启动
  Campaign，也不表示无界自治或通用 RL 已完成。

## 2026-08-30 最新状态：Claim 语义验真、Reward 校正和失败主动学习已闭合（ADR 0045）

- 04 原任务真实失败点是 PDF 消费裸相对路径，未绑定上游 Markdown typed output；四个源文件已成功读取，
  不是用户路径错误或读取超时。
- 运行时已实现 typed output/任务目录解析，并对 output-reference 确定性错误首轮短路，避免同参数原样重试。
- 04 证据报告现必须写显式 Claim Ledger；治理层检查来源归属、引文成员关系、模板引文、五个比较轴和
  claim–evidence 相关性，不再将标题/banner 或跨源引文当作语义真值。
- 失败 Reward 已修正：`accepted_completed=0`、完整 artifact=0、partial artifact=0.05；历史轨迹追加 revision 2，
  Episode truth=0、scalar=0、policy_eligible=false，原记录和 raw trace 保留。
- 手动失败热路现自动执行 observe→select→diagnose→repair proposal，但不直接修源码或晋升生产。原 04 失败已真实产生
  `repair_proposal_d45f590ce525fd6c`。
- 正式 Event 匹配实验 `experiment_manual_learning_event_20260831_v1` 4/4 硬门通过，反馈已进 ActiveLearningMemory；
  仍为 `production_effective=false`。全仓回归 `615 passed in 235.99s`。
- 重复超时长消息的历史发送函数因绕过日志无法唯一反推；旧进程、非标准播报为最高可信归因。当前已同时有
  消息持久去重和执行确定性短路两道保护。

## 2026-08-30 最新状态：04 遗留超时通知已收敛，消息持久审计通过实机 canary（ADR 0044）

- 已确认重复超时长消息不是持久任务队列、Campaign 或 Hermes cron 正常重跑；旧 04 进程加载修改前代码且
  QQ 周期恢复，用户可见消息未进入标准历史，构成运行态遗留通知与可观测性缺口。
- 旧进程已停止并由新代码重启；任务、聊天、Issue/Episode 等历史证据未删除。
- 文本消息统一经审计入口发送；`user_message_delivery.jsonl` 记录每次 attempt/ACK/failure/dedup，ACK 指纹
  持久化跨重启。普通消息按 Event 限域，超时洪水按内容 6 小时抑制。
- 实机 04 matched canary 前三轮分别暴露并修复“纯文本任务误注入 MD”“未发送真实读取结果”“跨任务误
  去重”；第四轮完整发送收到、计划、步骤开始/完成、真实第一行和最终停止，全部获 QQ ACK。
- 同步修复输入 README 被误称为新产物、纯文本最终通知仍写“文件/成品”的表述。全仓回归
  `609 passed in 115.23s`。
- 本轮没有启动 Campaign、自进化或 RL；ADR 0043 的语义 claim ledger、失败学习与 matched repair 仍是
  后续工作，不能因消息链通过而宣称完成。

## 2026-08-30 当前状态：Campaign 已停止，04 手动生产负样本暴露四层缺口（ADR 0043）

- 用户已显式停止 ADR 0042 的长期 Campaign；当前没有运行中的 Campaign/Partner 实例进程，不得继续把
  `campaign_abf9e34bf6af` 写成 running。生产默认仍为 `manual_stable`。
- 04 手动 Task `d2cb657c-f79d-4146-93d2-cdec40dc084e` 确实使用已激活的
  `candidate_preflight_contract_v2`，并成功读取四份 Harness 文件、生成 12,914 字节 Markdown。
- 下游 PDF Event 未解析刚生成 Markdown 的相对 `source_path`，三次原样重试后仍返回
  `no content (provide content or source_path)`；缺 PDF 后产物硬门正确将整体判为 failed。
- 最终“等待用户提供正确文件路径”是错误归因：用户输入路径正确，根因是内部 output-reference contract。
- 当前 preflight 能检查命名来源和逐字引文，但抽到的引文多为标题/banner，且报告出现跨来源混淆和无充分
  证据推断；literal membership 尚未升级为 claim-level semantic entailment。
- Issue `issue_038f7feaeffb`、Episode `episode_4d3f78f10ae387af` 和负轨迹
  `traj_manual_4d3f78f10ae387af` 已落盘，但标签/action/artifact/reward 过于粗糙，未自动产生诊断、repair
  Candidate、fresh canary 或原任务恢复。因此这次只证明“失败被记录”，不证明主动学习、自进化或 RL
  已从失败闭环学习。
- 当前准确能力边界：Event/Episode/硬门和 04 限域 matched 策略学习已部分可用；通用语义验真、手动失败
  自动诊断、通用 bounded repair、恢复原任务及持续 RL 改善仍未完成。

修复优先级和不可回退约束见 ADR 0043。本文档更新只记录事实，没有改代码、跑测试或启动任务。

## 2026-08-30 最新状态：长期 Campaign 已打通终态、波次学习和负样本 RL（ADR 0042）

- 正式运行 `campaign_abf9e34bf6af`：4 小时、五 lane 轮转、最多双槽，deadline
  `2026-08-31 00:15:41+08:00`。
- 当时实跑 30 个 WorkItem：29 completed、1 blocked；01–04 均有首轮真实业务完成，05 已完成三次
  `offline_policy_learning_self_evolution`。该段是运行时历史快照；Campaign 后续已由用户显式停止，当前不是 running。
- 修复两类框架根因：Campaign 明确授权的确定性 Event 在 `manual_stable` 中可直达；直达成功的
  `completion_ok/delivery_confirmed` 会完整进入 `STOP_PROJECT`，不再被二次改写成失败。
- 增加学习波次屏障，业务自更新指纹不能无限抢先准入导致 05 饥饿；05 消费一轮终态后立即继续业务。
- RL 现在把可归因的 failed/blocked 业务动作作为负样本纳入 reward 分布，但仍不得 canary/晋升。
  `01_content_readiness_gate` 当前 3 样本、1 负样本、mean reward `0.2967`、success rate `0.6667`，
  因低于 0.67 保持 `eligible_for_canary=false`。
- 01 的恢复 canary 已在不降低 PDF 质量门的前提下通过并真实送达；最终全量回归
  `560 passed in 112.32s`。

旧 `campaign_b98311a476ce` 与 `campaign_cacd94a1a320` 作为负面证据保留。当前能证明长期轮转与离线
策略更新已真实运行；不能据此声称五项目均持续改善或 LLM 权重已训练。详见 ADR 0042。

## 2026-08-30 阶段状态：首个真实业务持续改善门通过并限域晋升（ADR 0041）

- `planning.semantic_preflight` v6 诊断在 13 个真实 Episode 上拆出 input/evidence/event/artifact 四类机制；
  失败的旧 taxonomy 实验保留，正确链收敛到 `input_path_contract`。
- 修复 Candidate verified-source 未接线、无扩展名 `LICENSE` 漏识别、业务 Planner 误选控制面 Event、
  matched observation 被 QQ 网络污染等基础问题；普通业务的真实投递硬门没有降低。
- `experiment_6ef2b9be620b` 三组真实 Harness 仓库 matched pairs：Candidate 完成/真值/可观测性 3/3，
  Baseline 1/3；mean Episode reward `0.9 vs 0.3`，逐对 gain `[0.9,0.9,0.0]`，持续改善硬门通过。
- 晋升前全量回归 `555 passed`；补入激活/长期指纹测试后的阶段回归为 `556 passed in 111.85s`。
  Candidate 经 `policy/promoted → policy/activated` 两阶段 Event-first
  激活到 04：`production_effective=true`；其适用域仅为 04 的真实文件证据报告。
- 首次 4 小时 Campaign `campaign_b98311a476ce` 后续因确定性 Event 路由问题被
  `superseded_after_fix`；不要再把它当作当前运行 Campaign。替代运行与修复结果见 ADR 0042。

当前可以说“04 这一策略在三组真实 matched 业务中持续不回退且两组改善，并已受控进入生产”；仍不能说
“五项目都已持续改善”或“通用 RL/模型权重自进化已完成”。详见 ADR 0041。

## 2026-08-30 最新状态：主动学习完成第二个 bounded repair——显式承接意图（ADR 0040）

- `outcome.no_business_progress` 已按真实 governance reason 拆分，03/05 的 10 个
  `governance.unlinked_previous_receipt` Episode 被诊断为 systematic failure（10/10 evidence readable）。
- 根因是旧合同把“inputs 非空”错误等同于“承接上一 Receipt”；实际 10 个任务的
  `continue_from_project` 均为空，inputs 是源码、holdout 等独立任务材料。
- `stop_project` 现在保留 continuation/inbox provenance；manual governance 只对明确 continuation 强制
  previous artifact。独立任务即使有输入也可建立 Receipt，真正漏交接仍 hard reject。
- Event-first `experiment_6b9a3aa5fbc0` / Candidate `candidate_handoff_intent_6b9a3aa5fbc0` 在隔离 workspace
  验证三条路径：standalone accepted、explicit missing rejected、explicit linked accepted；拒绝尝试不推进
  iteration；standalone 不获 handoff reward、正确承接才获得。九项 criteria 全过，ledger 153 events 验链，
  Policy 保持 inconclusive。
- 完整回归：`544 passed in 125.83s`。未启动实例/Campaign，production 仍为 `manual_stable`。
- 修复后全局 selector 已重新计算，而非重复追已闭合 subtype：新提议
  `active_query_debe10bb5634fce9` 转向 `planning.semantic_preflight → diagnose_failure`，score
  `0.0230145`；`execution_authorized=false`，只提议未执行。最新 ledger 154 events 验链。

旧 trajectory 无法证明 intent 时不改历史文件，policy 在读取时保守扣除其模糊 handoff bonus。详见 ADR
0040。下一优先级是先诊断 `planning.semantic_preflight` 的真实机制，再决定 repair 或 resample，不直接改生产。

## 2026-08-30 最新状态：最后一个“未知中断”已由因果回放与 fresh canary 闭合（ADR 0039）

- ADR 0038 的 9/10 是 evaluator v1 漏判：最后一个 Episode 的失败输入被 generate_text 参数直接引用，
  但独立 sibling 产出报告后没有 remediation，导致 v1 看不到 skip。它不是 generate_text 模型调用挂死。
- evaluator v2 使用 plan DAG、depends_on、参数 `$step` 引用和上游 terminal 递归回放：10/10 都有
  dependency-skip 证据，0 个未知中断；旧 9/10 结果保留为反例。
- 正式 fresh Event-first `experiment_25880910b688` 真实运行 PlanExecutor 并行 DAG：失败 source、成功
  sibling/artifact、skipped compose/deliver；被 skip handler 0 次调用、每步唯一 terminal、模型调用 0。
- 六项 fresh criteria 全过，ledger 100 events 验链；Policy 仍 inconclusive，因为这是 runtime/证据语义
  canary，不是业务任务或 production canary。
- 完整回归：`537 passed in 202.27s`。

详见 ADR 0039。

## 2026-08-30 最新状态：首个 bounded repair 找到并修复真实终态缺口（ADR 0038）

- 对 ADR 0037 的 `generate_text systematic` 继续追因：根因主要不是模型挂死，而是 dependency-skipped
  步骤只发 progress callback、未写权威 task-log terminal，导致 Episode 假报 unclosed。
- PlanExecutor 现在持久化 `terminal_status=skipped`；Episode reducer 将其视为终态且不制造 tool failure。
- 正式历史 replay `experiment_2e4ff0e1db7c`：9 个 Episode、10 个 baseline unclosed 中，9 个有明确 skip
  证据被安全终态化，1 个无证据中断保持不变；历史文件 byte-identical，resample 诚实记为 unknown。
- selector v2 使用最新 diagnosis 更新 prior、降低重复诊断 novelty，并把版本纳入 decision ID；真实决策
  `active_query_e02512e8878d6aea` 以正分 `0.00649` 选择 bounded repair，而不是再次诊断。
- 完整回归：`535 passed in 155.72s`。本修复改善运行/学习证据真实性，不代表业务 outcome、模型质量或 RL
  已提升；production 仍为 `manual_stable`，未启动实例或 Campaign。

详见 ADR 0038 与 `docs/architecture/two_level_active_learning.md`。

## 2026-08-30 最新状态：双层主动学习已进入真实 Agent shadow（ADR 0037）

- 明确吸收 `partner_test` 两条主动学习主线：`bdk_transformer` 负责对象层“下一观察/实验”，
  `hermes_external_learning` 负责 Agent 元层“下一诊断/repair/resample”；正式 runtime 仍不依赖孵化区。
- 新增通用期望信息增益、成本/风险/任务价值 acquisition 与 Beta action memory；数值世界模型下一点已从
  纯 MaxVar 改为 Gaussian information gain × coverage。
- 真实 03/05 Episode 主动诊断发现 `lifecycle.unclosed_tool` 混合 6 个 step 机制；版本化 Candidate taxonomy
  保持历史 Episode 字节不变。首轮实验暴露全局/局部选择混淆并保留失败，随后以显式 focus 修复。
- 正式 shadow：`experiment_3f0fd4cc62bd` 将下一查询聚焦到 `.../generate_text`；
  `experiment_e8f5c512f4b0` 读取 9/9 匹配证据，诊断 `systematic_failure`、confidence `0.95` 并回写选择记忆。
- 当前完成“选问题→取证→修订假设空间→再选择→可行动诊断”，尚未实现通用 bounded repair、业务 outcome
  对照或 RL action policy。所有 Policy 均 `inconclusive`，production 仍为 `manual_stable`。
- 完整回归：`532 passed in 152.18s`。

详见 `docs/architecture/two_level_active_learning.md` 与 ADR 0037。

## 2026-08-30 最新状态：压力门与真实 Episode shadow 已打通（ADR 0036）

- 新增 120-case、多种子压力门：稀疏 5 点、高噪声、gross outlier、训练值之外的 Fourier/decay/hinge 参数。
- 首轮压力测试真实暴露 Candidate proposal recall 仅 75.8%（29/120 排除真实族）和离群 RMSE≈0.224；
  已通过“稀疏/高 surprise 全族退让”、扩展结构网格及 `insufficient_evidence` 修复，未绕过失败。
- 两臂共享的 `robust_bic_mdl_v2` 将压力集 mean RMSE 降至 Library `0.051701`、Candidate `0.047880`；
  outlier mean RMSE 两臂均为 `0.012678`。Candidate proposal recall `1.0`，平均工作集 `28→20.95`。
- 真实读取 110 条 Episode，只发现 04 有足够 reward 变化可做描述性轨迹；03、05 reward 全零，当前不能支持
  学习效果声明。`hard_gate_passed` 仅表示 truth+safety，新增 outcome gate 语义，禁止 RL 当成完成奖励。
- 新 Experiment `experiment_d0ccd7752641` / Candidate `candidate_world_model_pressure_d0ccd7752641`；
  27-event evolution ledger 验链。实验指标 `candidate_wins`，治理仍 `inconclusive`、production 无变化。
- 完整回归 `523 passed in 104.13s`；并修复长期 reducer 同秒时被随机 WorkItem ID 改变阶段的确定性缺陷。
- 完整生产仍为 `manual_stable`，当前没有启动实例或 Campaign。

详见 ADR 0036 与 `docs/architecture/world_model_bdk.md`。

## 2026-08-30 最新状态：孵化区已收敛迁移，Transformer Candidate 完成匹配实验（ADR 0035）

- Partner Python runtime 已不再包含 `/mnt/e/work/partner_test`、`import bdk` 或 `from bdk` 依赖。
- 正式能力进入 `partner/cognition/world_model/`；旧 FunctionPool/约束器进入 Partner-owned `partner/learn`。
- 三领域冻结 shadow：Library 与 Transformer 均为 family accuracy `1.0`、mean holdout RMSE
  `0.0036258983`；Transformer 将 mean hypotheses 从 `23.0` 降到 `8.6667`。
- Event-first Experiment `experiment_1d8b9b782175`、Candidate
  `candidate_world_model_transformer_1d8b9b782175` 已真实执行；双 Event 合同 ready，evolution ledger 14 events 验链。
- benchmark 目标判定 `candidate_wins`，但治理 Policy 为 `inconclusive`：只有一次合成 benchmark，
  `production_effective=false`，没有修改五实例生产上下文。
- 采用的外部学习结论已进入 Partner 自有知识目录；`partner_test` 降级为历史孵化档案，不再继续双线开发。

详见 ADR 0035 与 `docs/architecture/world_model_bdk.md`。

## 2026-08-30 历史阶段：BDK 世界模型原意校正 + 长时域相位循环（ADR 0034）

- BDK 北极星已从“四核 FunctionPool 选择”纠正为：现实观察→联想检索→多函数/机制假设→拟合与概率更新→
  主动求证→记忆巩固/反证。Transformer 将来是检索/假设 provider，不接管事实、评价、安全和晋升。
- `partner_test/bdk_transformer/src/bdk/world_model/` 已有透明最小原型和 hash-linked 事件账本。真实演示从
  5 点 top=`cubic` 修正为 17 点 top=`fourier_f1`（posterior≈0.869），重复证据继续巩固记忆；6 个定向测试通过。
- Partner Portfolio 新增 `long_horizon` reducer。项目 NextAction 优先；新业务证据达到配额后才学习；只有同
  Campaign 且 execution/evaluation Event 合同齐备的 Candidate 可自动安排一次实验，绝不自动晋升。
- 定向验证：long-horizon + Campaign **55 passed**，含“业务→学习→Candidate 实验→不重复”的端到端测试。
- 当前仍为 `manual_stable`；本次没有启动实例、没有开启长跑、没有修改 production policy。

详见 `docs/architecture/world_model_bdk.md`、`docs/architecture/project_portfolio.md` 与 ADR 0034。

## 2026-08-29 当前纠正：恢复 Event-first（ADR 0032，优先于 ADR 0030–0031）

### 首次真实匹配实验（ADR 0033）

- 已在真实 TargetDiff affinity 数据上运行 `experiment_60f044790615`：相同 63,123 ligand、相同
  `sha256(group)%5` 五折、相同 `[vina,rmsd]` 特征。
- sklearn HGB mean RMSE `1.541555`，BDK `1.544721`，差值 `+0.003166`（BDK 差约 0.21%）；BDK 优于
  Linear `1.600721`，但没有胜过最佳 baseline。
- Candidate 确实通过 `execute_candidate → targetdiff_bdk_function_pool` Event 执行；5 折零 group leakage，
  Ocamms 通过，7 个 Event 哈希链验证通过。
- BDK 平均 gate：Fourier 87.86%、Linear 6.30%、Quadratic 5.63%、ExpDecay 0.22%。这是内部模型权重，
  不是因果结论或贝叶斯后验。
- 预注册改善门 `-0.02 RMSE` 未通过，独立重复数也不足，因此决策 `inconclusive`、未晋升、生产无变化。

详见 ADR 0033。

- **唯一执行语义**：Partner 仍坚持 Event-first。Candidate 只能声明一个经过审计的
  `execution_contract(kind=event)`，由 `execute_candidate` 事件调用白名单 Event handler；Candidate 文本、
  笔记或 Python 内容不能直接执行。执行请求和完成结果写入 hash-linked Event ledger，并按
  `execution_id` 幂等，重放不会重复副作用。
- **Candidate 已可真正执行**：`targetdiff_bdk_function_pool` 和 `execute_candidate` 已接入直接 Event
  registry。只有 `execution_ready=true`、实例在 allowlist、handler 存在，且 production 模式下
  `production_effective=true` 才能执行。知识笔记明确登记为 `knowledge_draft`，不可执行、不可晋升。
- **伪晋升已纠正**：旧 `tests/test_bdk_real_promote.py` 用硬编码 baseline/candidate reward 写入真实
  workspace，不能证明业务改善。测试已隔离到 `tmp_path`；真实 `control_policy.json` 中的
  `bdk_closure_1788004048` 已移除，相关历史记录通过 correction ledger 作废但保留审计痕迹。
- **BDK 边界**：当前 BDK FunctionPool 是小型数值函数选择器（Linear/Quadratic/Fourier/ExpDecay），
  可作为 02 等数值任务的可选模型 Event；它不是大模型 Agent 的底层认知架构，也没有替代 Partner 的
  Event、状态机、工具、记忆、验证和项目循环。
- **RL 边界**：外部学习只说明 BO/MaxVar 适合连续实验的下一采样点，不证明 Partner 不需要 RL。
  重复的 context/tool/strategy 选择可用保守 contextual bandit/offline RL；一次性代码和协议变更继续走
  Issue→Hypothesis→Candidate→test/canary→PromotionDecision。合成 fixture 轨迹现被学习和 canary 排除。

权威设计见 `docs/architecture/event_first_self_evolution.md`，决策见 ADR 0032。


## 2026-08-29 历史总结：BDK 集成声明（ADR 0031；已被 ADR 0032 纠正）

**历史原声明（不再作为当前事实）**：把 `partner_test/bdk_transformer` 的 4 个模块接进若干实验路径，
并让一个 BDK skill 走到 `control_policy.json`。其中“真实晋升”来自硬编码测试奖励，现已作废；测试数也只
代表当时仓库快照，不代表当前完整基线。

### 接入状态
- 04 实例：`hermes_external_learning` 18 篇笔记 → 18 candidate skill（ocamms 全过）
- 05 实例：BDK ocamms guard hook（`PARTNER_BDK_OCAMMS_ENFORCE=1` env var 启用）
- 02 实例：BDK FunctionPool atomic event（`targetdiff_bdk_function_pool`，与 sklearn 平行）
- inbox trigger：04 启动时跑后台 poller（`PARTNER_LEARN_TRIGGER_ENABLE=1`）

### 真实数字
- BDK vs sklearn HGB on TargetDiff：BDK 显著差 1.5%（3-seed CI [+0.0019, +0.0193] 排除 0），但实用等价
- BDK FunctionPool 在 04 literature 数据：RMSE 0.0451 log10（证明 BDK 跨域）
- BDK kernel mask 策略：all-4-mask 仍最优（gate 自己学比 prior 强）
- 一个 hermes-learn BDK skill **真的**被 promote 进 `control["promoted"]`（`bdk_closure_1788004048`），audit trail 在 `promotion_decisions.jsonl` 含 BDK ocamms verdict

### 没动
- `runtime.mode` 仍 `manual_stable`
- BDK skill **没**进 production traffic（因为不在任何 canary choices）
- sklearn Stage 9-13 没被替换
- 8 个 ADR（0023-0030）全保留 + 0031 summary

详细见 `docs/decisions/0031-bdk-integration-summary.md`


## 2026-08-29 历史无效记录：BDK fixture Promote（ADR 0030；被 ADR 0032 作废）

> 本节保留原始事故记录。下列“真的”“永久”均不再成立，不得作为当前能力或生产事实引用。

- **一个 hermes-learn BDK skill 真的被 promote 了**:
  - decision_key: `bdk_closure_1788004048`
  - BDK skill: `candidate_hermes_learn_1d161a783946`（Browser-Use 学习笔记）
  - ocamms verdict: PASS (1 activated kernel: linear)
  - reward gain: 0.40 (baseline 0.40 → candidate 0.80)
  - 永久写入 `control_policy.json` 和 `promotion_decisions.jsonl`

- **生产侧效应 = NONE** (BDK skill 不在任何 canary choices 中,
  choose_action 查 promoted_id in choices 返回 None)

- **fixture 自动回滚** (`tests/test_bdk_real_promote.py`):
  - 备份 `/tmp/bdk_real_promote_backup/` 在 module 启动时
  - 测试结束只回滚 control_policy.json (canary 数据保留)
  - 3 个新测试 PASSED

- 当前全量回归：**497 passed in 103.06s**(从 494 → 497, +3 新增)
- BDK 沙箱：**24 passed**(不退步)
- 决策文档：`docs/decisions/0030-bdk-closure-real-promote.md`

### 闭环完整路径

```
hermes_external_learning/notes/*.md
  → partner.learn.learn_from_hermes.py (registers 18 skills)
  → bdk_ocamms_promotion_guard (1/18 passes, all)
  → PARTNER_BDK_OCAMMS_ENFORCE=1 hook
  → evaluate_canaries(dry_run=False)
  → control_policy.json ← 真实写入
  → promotion_decisions.jsonl ← 含 BDK ocamms 审计
```

### 历史“不做”列表（不再适用）

- 不让 BDK skill 自动走 production traffic(需要显式 canary 配置)
- ~~不回滚这个 promote 事件~~（已纠正：映射移除，证据作废但保留原始审计日志）
- 不基于单次 reward gain 0.4 推广(这只是 6 轨迹的小样本)

## 2026-08-29 最新状态：BDK 多 seed 真相 + 跨域验证（ADR 0029）

- **多 seed bootstrap 修正 ADR 0027 结论**:
  - 单 seed (ADR 0027): CI 包含 0, 结论"无差异"
  - **3 seeds (本轮)**: CI = **[+0.0019, +0.0193]** 排除 0
  - **BDK 显著比 sklearn HGB 差 0.01-0.02 RMSE (~1.5%)**
  - 结论修正: sklearn 优势**真实**但**小**; 仍可作为 parallel option

- **Mask 策略对比** (4 masks × 3 seeds × 5 folds):
  - all-4-mask (sweep best): **1.5538** RMSE
  - recommended (linear+quad): 1.5849 (+2%)
  - linear-only: 1.6139 (+4%)
  - quadratic-only: 1.7038 (+10%)
  - **all-4 仍最优**; kernel optimizer prior 不是 hard rule

- **04 文献 demo** (`learn_literature_bdk_demo.py`):
  - 6 sources, BDK RMSE = 0.0451 log10 (all-4) vs 0.1809 (recommended)
  - 证明 BDK 在非分子数据**同样工作**;04 不消费此输出(写 /tmp/)

- **02 pipeline 集成** (`data_driven_mask=True` opt-in):
  - 真实数据: all-4 RMSE 1.68, data-driven 1.81 (反而更差)
  - 默认仍是 all-4 (向后兼容)
  - data-driven 是 prior helper,不是 production 强制

- 当前全量回归:**504 passed**(从 486 → 504, +18 新增, 0 回归)
- BDK 沙箱:**24 passed**(不退步)
- 决策文档:`docs/decisions/0029-bdk-multi-seed-truth.md`

### 不做

- 不基于多 seed CI 结果拒绝 BDK(差距 1.5% 在分子任务上实用等价)
- 不强制 data-driven mask(用户可继续用 all-4 default)
- 不把 04 demo 接入 04 production (它是 capability proof,不是 plan)

## 2026-08-29 最新状态：BDK Bootstrap CI + Kernel 设计 + Canary Dry-Run（ADR 0027）

- **Bootstrap CI 验证**（`targetdiff_bdk_vs_sklearn_bootstrap.py`）：
  - 1041 groups, 1000 bootstrap
  - sklearn HGB: **1.5046** RMSE
  - BDK best: **1.4994** RMSE
  - Δ = **-0.0051**（BDK 略好）
  - 95% CI = **[-0.0120, +0.0020]**, p (BDK worse) = 0.086
  - **CI 包含 0 → 无统计差异**

- **Kernel Mask Optimizer**（`bdk_kernel_mask_optimizer.py`）：
  - 真实 TargetDiff per-kernel R^2: linear=0.18, quadratic=0.19, fourier=0.07, expdecay=0.03
  - 推荐 mask: `[True, True, False, False]` (linear + quadratic)
  - 集成点: `BDKFunctionPoolFitter.from_data(X, y)` 自动应用

- **Canary Dry-Run Mode**（`evaluate_canaries(dry_run=True)`）：
  - 不写 `control_policy.json`（production safe）
  - 仍写 `promotion_decisions.jsonl`（audit trail）
  - 真实 dry-run 端到端：18 hermes skills 全通过，1 个测试 BDK skill (3 activated) 被 hook 阻止
  - control_policy.json **未修改**验证

- 当前全量回归：**486 passed in 119.74s**（464 → 486，+22 新增，0 回归）
- BDK 沙箱：**24 passed**（不退步）
- 决策文档：`docs/decisions/0027-bdk-bootstrap-kernel-canary.md`

### 不做

- 不基于 bootstrap CI 结果做 BDK promotion（CI 显示无差异）
- 不强制使用 kernel mask optimizer（仍是可选 helper）
- dry-run mode 默认关闭（需 caller 显式 `dry_run=True`）

## 2026-08-29 最新状态：BDK 真实触发 + 超参搜索（ADR 0026）

- **真实 inbox 端到端触发**：
  - 写 marker 到 `instances/04/state/desktop_inbox.jsonl`
  - `start_learn_trigger_poller` 几秒内消费，运行 `learn_from_hermes.main()`
  - 18 skills 进 registry，exit_code=0, ocamms_all_passed=True
  - **04 自触发学习流程在真实环境跑通**

- **BDK 超参搜索**（quick grid 16 configs）：
  - BDK best: **1.5432** RMSE（lr=0.05, wd=0.001, epochs=300）
  - sklearn HGB baseline: 1.5416 RMSE
  - Delta: **+0.0016**（BDK 比 HGB 略差 0.1% — within noise）
  - **结论**: BDK FunctionPool 默认参数下与 sklearn HGB 在 TargetDiff
    亲和度任务上**实质打平**，集成价值已验证

- 当前全量回归：**464 passed in 69.35s**（460 → 464，+4 新增，0 回归）
- BDK 沙箱：**24 passed**（不退步）
- 决策文档：`docs/decisions/0026-bdk-real-trigger-and-sweep.md`

### 不做

- 不基于 sweep 结果做 BDK promotion（差距无统计显著性）
- 不替换 sklearn baseline（BDK 现在是 parallel option，不是 replacement）
- 不跑 full grid sweep（240 configs × 5-fold ~10 分钟，性价比低）

## 2026-08-29 最新状态：05/02/04 BDK 接入延伸（ADR 0025）

- **05 hook 真正启用**：`PARTNER_BDK_OCAMMS_ENFORCE=1` env var 启用
  - `evaluate_canaries` 检测 candidate_id → 如指向 BDK skill 自动加 `enforce_bdk_ocamms=True`
  - 默认行为不变（env var 不设时）

- **02 sklearn vs BDK 对比实验**：
  - 63123 aggregated ligands 5-fold
  - sklearn Linear: 1.6007 RMSE
  - sklearn HGB: **1.5416** (best)
  - BDK FunctionPool: **1.5502**
  - Delta: +0.0087（BDK 比 HGB 略差 0.6%，噪声范围内）
  - BDK ocamms: PASS（主要 fourier 87%）
  - 报告: `partner/learn/targetdiff_bdk_vs_sklearn_comparison.py`
  - **不构成 promotion 决定** — 是 descriptive comparison

- **04 inbox 触发路径**：`PARTNER_LEARN_TRIGGER_ENABLE=1` AND instance=04 时
  - `partner.start_mind()` 后启动后台 poller
  - 监听 `instances/04/state/desktop_inbox.jsonl`
  - 检测到 `{source: learn_trigger, text: /learn_from_hermes}` marker → 调
    `learn_from_hermes.main()`
  - 默认行为不变（env var 不设时）

- 当前全量回归：**460 passed in 60.41s**（450 → 460，+10 新增，0 回归）
- 决策文档：`docs/decisions/0025-bdk-extension-wiring.md`

### 不做

- 三个 hook 全 opt-in；env var 不设时与之前完全一致
- 不基于对比结果做 BDK promotion — 差距在噪声范围内
- 不把 BDK 超参数搜索挂上 production

## 2026-08-28 最新状态：05/02 BDK 集成接入（ADR 0024）

- **05 promotion hook**：`decide_experiment` 新增 opt-in BDK ocamms guard
  - 仅当 `params["enforce_bdk_ocamms"]=True` AND `decision="promoted"` AND
    `candidate_id` 给出时触发
  - blocked → `{"ok": False, "status": "bdk_ocamms_blocked", verdict}`，不 promote
  - passed → verdict 追加到 `decision.evidence` (kind="bdk_ocamms_guard")
  - 完全向后兼容：不传 `enforce_bdk_ocamms` 时与之前完全一致

- **02 BDK stage**：新增 `targetdiff_bdk_function_pool` atomic event handler
  - 平行于 Stage 9–13 的 sklearn baseline，**不替换**
  - 同样 5-fold group-disjoint split (`sha256(group)%5`)
  - 用 `bdk_function_pool_fitter` 训练，产出 `mean_bdk_rmse` /
    `mean_kernel_probs` / `ocamms_all_passed`
  - BDK 不可用 → 返回 `bdk_unavailable`，不影响原 pipeline

- **真实数据验证**：02 BDK stage 跑 TargetDiff 真实数据 76803 行
  - mean_bdk_rmse = 1.5692（5-fold 平均）
  - mean_kernel_probs = [0.031, 0.063, **0.904**, 0.002]（fourier 主核）
  - ocamms_all_passed = True

- 当前全量回归：**446 passed in 42.14s**（431 → 446，新增 15 测试，0 回归）
- 决策文档：`docs/decisions/0024-bdk-05-02-integration.md`

### 不做

- 不自动启用 `enforce_bdk_ocamms`——任何现有 caller 都不受影响
- 不让 02 production pipeline 默认调 `targetdiff_bdk_function_pool`——
  caller 必须显式调新 event
- 不声称 BDK 在分子任务上优于 sklearn（两组结果在独立 .json 文件里，
  caller 需要同时跑两边再比较）

## 2026-08-28 最新状态：Hermes 接手 Codex — Cognition 扩展 + BDK 集成设计（ADR 0023）

- **已撤回**：会话初版在 `hermes_external_learning/manifest/` 写了 20 条手工 JSON +
  校验脚本。这是 Hermes 替 Partner 完成的整理工作，违背用户原意（"让 Partner
  主动学习"），已删除：
  - `hermes_external_learning/manifest/` 整个目录
  - `hermes_external_learning/scripts/validate_manifest.py`
  - 配套虚假记录（learning_log.md / progress.md / PARTNER_INTEGRATION.md / ADR 0023 旧版）

- **保留**：阶段 2 cognition_mirror_extended（Partner 自身能力扩展，不依赖外部学习）
- **保留**：阶段 3 Gate C 显式 route_marker + execution_marker（防交叉污染）

- **新真工作**：BDK 集成设计方案
  - `partner_test/bdk_transformer/INTEGRATION_PLAN.md`：BDK 核心能力 ↔ Partner 实例映射、import 方式、两阶段目标
  - Partner 04 实例的 `learn_from_hermes.py`：扫描 hermes_external_learning → 产出 candidate skill
  - 端到端测试：registry/candidate_skills/ 至少 3 条新条目，source 字段指向 hermes_external_learning/ 具体文件

- 当前全量回归：**387 passed in 18.59s**（从 366 → 387，新增 21 个测试，0 回归）
- BDK 沙箱基线：**24 passed in 0.86s**（不退步）
- 决策文档：`docs/decisions/0023-hermes-handoff-stage-1-2-3.md`（已重写为诚实记录）
- `candidate_cognition_context_v1` revision 3 仍为 `shadow`、`production_effective=false`；未注册、未晋升、未进入 production

### 不做（继续冻结）

- 不把"21 个新测试通过"等同于"任务质量提升"或"自进化已开启"
- 不启动 Partner 实例；不启动长跑；不修改 production 配置；不进入 Gate D 业务执行
- 不让 Hermes 替 Partner 做"学习结果"整理

### 下一轮需用户授权

BDK 集成走 `import` 路线后，需要用户授权才能让 Partner 在 04 实例实际运行
`learn_from_hermes.py` 并部署到 partner_workspace/registry/。

## 2026-08-28 追加：认知账本 L0 与 Partner shadow bridge
## 2026-08-28 追加：认知账本 L0 与 Partner shadow bridge
## 2026-08-28 追加：认知账本 L0 与 Partner shadow bridge

- `partner_test/bdk_transformer` 已实现 typed CognitionEvent、单 Episode 哈希链 JSONL、确定性 reducer、
  篡改检测和“复制已验证前缀”的故障恢复；20 个现有 Partner Episode 已只读投影为 40 个事件，
  20/20 重放一致。
- Partner 新增 `governance/cognition_adapter.py` 与 `cognition_shadow_bundle.schema.json`。适配器只做
  显式离线校验和 shadow 归档，可生成 Candidate Skill 草案但不注册、不晋升、不执行。
- `manual_stable`、Campaign/Research Loop 开关、实例消息、QQ/browser 和生产策略均未改变。
- 这证明数据边界与回放成立，不证明认知策略已提升任务，也不代表自进化已重新开启。
- 初始 import `cognition_shadow_49cae789b89154ac` 因 project_id 被不必要地改写为连字符形式，已在
  `corrections.jsonl` 追加 invalidate；未删除历史。权威 shadow archive 为
  `cognition_shadow_809c3bcdd0b0412b`，其 project/source Episode/task/instance 均精确匹配。
- `candidate_cognition_context_v1` 只在内存中生成草案，registry 中不存在该文件。
- Gate B 已实现为 Partner 自有旁路：任务 Episode reducer 完成后可选择性生成两事件认知镜像；配置
  `runtime.cognition_shadow_mirror` 默认 false，失败不改变任务终态，且不依赖 `partner_test` 运行时。
- 真实显式验证使用实例 04 的 completed Episode `episode_29582c86e705d2df`，生成 mirror
  `cog_episode_29582c86e705d2df_f60650145f5c6ec1` 与 import
  `cognition_shadow_4c8be7fad06c69e6`；哈希链/ledger digest/head hash 全部复核通过。
- 用独立 BDK reducer 对同一 Episode 再生成账本，结果与 Partner-native sidecar **逐字节相同**，
  replay state/digest 也完全相同；这验证了交换合同，而不是让 Partner 运行时依赖 BDK。
- Gate B 真实验证后生产镜像开关继续为 false；当时没有注册 Candidate。随后 Gate C 才按独立实验合同
  创建下述 shadow revision，二者不能混为生产启用。
- Gate C 首版 `experiment_780b5cc8351d` 因挤掉 Receipt 3/7、query 相关上下文 4/7 已拒绝；修正版
  `experiment_acfec34e9d7b` 使用 bounded soft boost + 1200 字符 Receipt reserve，7/7 机械门通过，
  3/7 真正使用 cognition route。`candidate_cognition_context_v1` revision 3 现为 `shadow`，
  `production_effective=false`；尚无独立业务任务质量证据。
- 当前完整 Partner 回归：**366 passed in 16.64s**。
- 权威边界见 `docs/architecture/cognition_shadow_integration.md`。
- 后置 sidecar 决策见 ADR 0021；首个候选的隔离规则见
  `docs/architecture/context_selection_candidate_gate_c.md`，修正版决策见 ADR 0022。


## 2026-08-28 最新状态：12 个 framework bug 修复 + 03/05 执行子链提升

**会话代号**: Hermes 接管 Codex（2026-08-28 凌晨 ~ 15:30）
**commit**: d536870 pushed to origin/main

### 12 个 framework bug 修复（ADR 0007-0020）

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
| #55 | 0020 | push_files 自动从上游 step result 落盘（read → generate_text → push_files 执行/交付子链 verified）|

### 03 + 05 能力演进（按执行子链计，不等于完整治理任务成功）

> **2026-08-28 证据口径校正**：下面的百分比是阶段性人工估计，不是独立基准分数。
> 最近复核的 03 任务 `b61eca0e-1cbe-4ec7-ae86-5006c483b5a9` 和 05 任务
> `4e65ff07-bb90-416d-85d4-7c5fa88215ef` 均完成了读取、生成、落盘/发送等底层步骤，
> 但最终 `manual_iteration_governance` 因 `unlinked_previous_receipt` 拒绝。故这里只能证明执行与
> 交付子链可用，不能称为“受治理任务端到端成功”。独立任务若有意不承接上一 Receipt，必须显式使用
> ADR 0009 定义的 `ignore_handoff_check`；项目连续任务仍必须链接上一 Receipt。

**03 (partner_framework_frontend)**：
- 自主度 50% → **75%**
- 第九轮的执行/交付子链 verified（finding_report.md 真发到 QQ）；完整治理终态仍需链接 Receipt 后复验
- 5/5 步：atomic_inspect_file + execute_code + generate_text + create_file + push_files
- 3 对 verbatim source_path + evidence_quote 双行引用（来自 step1 真读 harness.py）
- 诚实边界：grep 计数未提供时标 `proposed`（未执行）——不编造

**05 (agent_self_evolution)**：
- 自主度 40% → **85%**
- 第九轮的执行/交付子链 verified（cross_instance_review_v9.md 真发到 QQ）；完整治理终态仍需链接 Receipt 后复验
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
- `/mnt/e/work/partner_test` 的 BDK/认知架构仍是独立研究沙箱；其中 `partner_deploy.py` 是模拟，
  candidate JSON 尚不兼容主 Partner schema，未进入 production 或 `manual_stable`。

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
