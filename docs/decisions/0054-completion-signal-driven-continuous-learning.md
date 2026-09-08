# ADR 0054：完成信号驱动的双槽持续学习

**状态**：Accepted；实验 Campaign 正在运行，完整生产晋升仍 blocked  
**日期**：2026-09-01  
**模型边界**：MiniMax-M3 only；不调用 DeepSeek  

## 背景

ADR 0053 已把实例内任务改为 FIFO，但真实日期采样 supervisor 仍以固定周期复审。用户要求提高模型和机器
利用率：一个任务真正终态后立刻运行下一个，而不是等待 5 分钟；同时 04/05 最多双槽、Event-first、
baseline/candidate 匹配、Reward 真值门和文档纪律均不能降低。

## 决策

1. `TaskInstance` 在权威 `done/failed` 时双写终态：持久账本
   `state/campaigns/task_terminal_events.jsonl`，以及按 workspace 隔离的 `/tmp` named FIFO 唤醒。
2. Campaign controller 阻塞等待终态 FIFO；收到信号立刻 reconcile 和 dispatch。30 秒 timeout 只用于进程
   崩溃、信号丢失或遗留任务恢复，不再是任务调度周期。
3. `batch_plan` 的“产物生成完成”只是中间 checkpoint，不发终态信号；Claim、交付、Receipt 治理后的
   `manual_stop_project_finalization` 才是权威终态，避免同一任务 `done → failed` 双信号。
4. `execute_candidate` 是白名单 Event 的包装器，和其有界本地 handler 一样内联执行；匹配 baseline 的
   `select_context(use_llm=false)` 也内联执行，避免历史超时线程占满通用 worker pool。
5. 成功的 `manual_stop_project_finalization` 可被 Campaign recovery 直接认作完成，不再强制等待并不存在的
   `iteration_llm_check`，否则成功的本地观察会占槽到 Lease 过期。
6. 04/05 必须在可访问外部 API 的正常运行环境启动。受限沙箱内 DNS 失败是 environment 负样本，不能
   伪装成 MiniMax 输出质量问题。
7. QQ 暂时不可用时，本次显式授权的研究实验可使用 durable local observation 推进；这种样本不计
   `delivery_confirmed`，不能满足 sustained-business 的真实交付门。
8. 纯文本生成固定使用单轮、无工具的 `report` purpose，并在初始 prompt 要求直接输出成品。运行配置的
   report timeout 从 60 秒先提高到 180 秒；实跑又出现一条 181 秒超时，最终提高到 300 秒。MiniMax 长报告
   不能因过早 timeout 再启 Hermes fallback、形成重复模型调用；外层 Claim 门仍负责拒绝不合格正文。
9. Candidate 的机器证据账本接受 `verified_sources` 字典与 `verified_source_evidence` 列表两种内部形状。
   模型正文可自由组织，但从任意层级、任意前后缀的首个 Claim Ledger 标题开始全部丢弃，最终只附加
   运行时按真实路径和逐字引文生成的完整账本；baseline 不获得此 Candidate-only 干预。

## 实验负载

- Campaign：`campaign_f8ace4ef3e44`，04/05 两槽，7 天硬截止，56 个初始 WorkItem。
- 每项目首窗 14 个 matched pair，严格 `baseline → candidate → 下一主题`；主题覆盖上下文、失败学习、
  证据、课程、Reward、Candidate 和 promotion 边界。
- 每个新真实日期窗口再补 3 pair/项目，直到 3 个真实日期；不伪造时间。
- 一个预声明 stress pair 使用 Codex `compact.rs` + JitRL；失败按负样本保留，不降低硬门。

## 实机证据

- 05 baseline Task `4a73a6a6-5ecb-4ddb-bdd4-e8201b623886` 在最终失败后，controller 收到
  `terminal_signal_received`，约 5 秒内派发下一 Candidate；不再等待 5 分钟。
- Candidate 包装器修复后本地上下文步骤由卡死变为约 1.2 秒完成；baseline 本地选择由卡死变为约
  0.1 秒完成。
- report purpose 首个 05 实跑只用一次 MiniMax 调用，23.7 秒返回 11,433 字符；此前 action 路径常用
  72–338 秒并触发第二次纠偏调用。
- 正常网络环境下 MiniMax-M3 真实返回；失败 Claim 被真值门拒绝并记 Reward `-0.4`，没有假成功。
- 通用 LLM readiness 现由 2 个独立 accepted、3 个 rejected MiniMax 实验共同支持：6 个 matched pair、
  3 类任务；拒绝记录没有被筛掉。
- 实跑先暴露两类确定性桥接缺陷：新证据列表未被旧保护器识别，以及 `# Claim Ledger` / 中文序号标题
  未被清理，导致机器的 2 条真 Claim 与模型的 9 条不完整 Claim 并存。失败均以 Reward `-0.4` 保留；
  修复后对同一真实产物重放为 `2 passed, 0 failed`。
- 最终重载后的 05 Candidate `e0e347d1-afaa-466e-9b31-cf25ad1b9c9e` 在线真值门通过并得到
  Reward `+0.8`，对应 WorkItem completed，controller 立即派发下一 baseline。文件上传成功但阶段消息无
  ACK，`delivery_confirmed=false`，说明本决策没有用本地完成冒充真实渠道完成。
- 最终完整回归：`688 passed, 2 warnings in 129.97s`。

## 边界

当前 readiness 仍为 `blocked / activated=false`。已有 sustained business 只有 3 个真实交付轮、1 个项目、
1 个日期；长期 RL 未满足每臂 20、两项目、三真实日期、Candidate Wilson 下界和零 false-success。
本 ADR 证明“高利用率、可恢复、可验真的持续采样器”已运行，不证明长期 RL 已成熟，也不授权绕过
Event-first 或直接修改 production control policy。
