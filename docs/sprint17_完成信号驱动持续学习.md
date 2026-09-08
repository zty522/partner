# Sprint 17：完成信号驱动持续学习与长期 RL 取证

**日期**：2026-09-01  
**状态**：基础设施里程碑完成；首个真实日期采样已结束，完整长期门未通过  
**生产基线**：`manual_stable` 不变；本 Sprint 仅运行用户显式授权的 04/05 实验 Campaign

## 目标与五项交付

| # | 交付 | 状态 | 验收事实 |
|---|---|---|---|
| 1 | 完成即续跑信号 | ✅ | 持久终态账本 + FIFO；真实 Task 终态后约 5 秒接续 |
| 2 | 高密度 MiniMax matched 负载 | ✅ | 56 WorkItem，04/05 各 14 pair，MiniMax-M3 only |
| 3 | 有意义 Event-first 项目任务 | ✅ | 文献/GitHub 与 Agent 自进化两个真实项目；Claim 真值门、Episode、Reward 均保留 |
| 4 | 自动 readiness 与条件激活 | ✅ 基础设施 / ⏳证据 | 每个终态复审；只有全门通过才 Event-first promotion/activation |
| 5 | 实机、回归与文档纪律 | ✅ | 688 passed；ADR、架构、状态、目录、代码与测试基线已同步 |

## 已解决的实机问题

1. `execute_candidate` 外层包装没有进入有界本地 Event 白名单，卡在通用线程池；现已内联。
2. baseline `select_context(use_llm=false)` 仍被发往线程池；现为 0.1 秒级本地步骤且不浪费分类模型调用。
3. 受限启动环境禁止 MiniMax DNS；04/05 已改为正常网络环境启动，QQ 与 MiniMax 均恢复连接。
4. 成功的实验 observation 没有 `iteration_llm_check`，04 槽位长期不释放；Campaign recovery 现在认可
   `manual_stop_project_finalization`。
5. Harness 中间 `done` 和最终真值 `failed` 产生双终态信号；已限定只有最终治理发唤醒。
6. QQ 故障曾让研究任务空转；未开始项可显式降级为 local observation，但不获得 delivery credit。
7. 文本报告原来走 `action` 工具模式且 60 秒过早超时，常产生第二次纠偏/fallback；现走单轮无工具
   `report`。先调至 180 秒后，04 又实证一条请求在 181 秒才超时，故最终上限为 300 秒，避免同一报告
   重复调用；这只是单次调用上限，完成信号调度仍是立即接续。首个 05 样本 23.7 秒单调用完成。
8. 连续 Candidate 的证据结构是 `verified_source_evidence` 列表，旧确定性 Ledger 保护器只识别
   `verified_sources` 字典；并且只清理二级、标题完全相同的 `## Claim Ledger`。现同时支持两种证据形状，
   并从任意 Markdown 标题层级、带中文序号或后缀的首个 Claim Ledger 起整体替换为机器核验账本。
   真实失败中的“2 passed + 9 incomplete”按负样本保留；同一真实报告离线复验为 `2 passed, 0 failed`。

## 当前机器事实

- Campaign：`campaign_f8ace4ef3e44`，截止 2026-09-08 17:28 +08:00。
- 初始 56 WorkItem，运行期间因报告/恢复和后续取证形成 88 个持久项；19:35 最新快照为
  `51 completed / 37 blocked / 0 active`，Campaign 当前 `blocked`。blocked 包含真实环境失败、baseline
  缺证据、修复前 Candidate Claim 失败和框架重载中断，全部保留为负样本。
- 最终重载后的 05 Candidate Task `e0e347d1-afaa-466e-9b31-cf25ad1b9c9e` 真值门通过、Reward `+0.8`、
  WorkItem `work_2a2788eaef65` completed，随后立即进入下一 baseline。文件真实上传，但步骤/总结消息无
  渠道 ACK，因此 `delivery_confirmed=false`，没有获得 sustained-business credit。
- MiniMax 通用门：2 个 accepted + 3 个 rejected 实验；accepted 共 6 pair、3 task families，
  baseline 0/3、Candidate 3/3。
- 连续实验已有 baseline 22、Candidate 25；均值分别 `-0.4 / 0.56`，Candidate 成功下界约 `0.609`、
  false-success=5。production readiness 仍 `blocked / activated=false`；sustained business 3 轮、单项目、
  单日期，长期门未过。
- 本 Sprint 不再宣称“持续取证中”：本日期队列已经耗尽。下一阶段的主动选题、自动修复、跨日期学习和
  受限晋升合同见 `sprint18_主动课程自动修复与受限晋升.md`（规格就绪，实施未开始）。
- 完整回归：`688 passed, 2 warnings in 129.97s`。

## 完成定义

本 Sprint 的“完成”指持续取证器已可靠运行，不指未来 7 天证据提前完成。后续真实日期和样本只能由运行
产生；controller 必须持续 fail closed。任何人不得为了把 Sprint 标绿而改时间戳、补假 delivery、删除
rejected/negative 样本，或把 local observation 写成用户已收到。
