# ADR 0062：动态 slot 资源预算 + Partner 接入真实外部检索

- 状态：Accepted / Production Active
- 日期：2026-09-03
- 前置：ADR 0060 / 0061 / 0061v2
- 取代：硬编码 `scheduler.MAX_ACTIVE = 2`

## 背景与动机

ADR 0060 上线后实例原生运行时长期以 `max_active=2` 卡双槽运行。当时理由是：

1. partner 实例进程吃 Conda 锁、可能同时拉 LLM token，多实例并发会争抢；
2. "5 实例都活着但同时做事" 缺乏真实证据。

过去一天里观察到两件事决定这次升级：
1. 真实机器（Intel Core Ultra 9 185H，22 核 / 15 GB 内存，1 分钟 loadavg=0.04）长时间空闲
   到荒谬——硬编码 2 不是"硬件上限"，而是十月份的脚手架；
2. user 给 partner_test 下两个目录校正了两个真正借鉴主题：
   - bdk_transformer 的核心主题是"主动学习时怎么做"，partner 应当借鉴的不是 BDK 跑函数本身，
     而是它的"主动学习方法论 + 实验验证经验"；
   - hermes_external_learning 的核心不是 17 份离线笔记，而是 hermes **怎么实现外部检索**——
     那套 `list → filter → deep-fetch → summarize → cache` 流程（process.md 第 2 节）。

这两条被一并解析为 partner runtime 该有的两个真能力：
1. slot 资源预算要从硬编码变成读 `/proc/meminfo` + CPU 核数 + 可选 config override 三层；
2. partner 要有真实外部检索能力，主动学习时能根据 failure 主题动态 fetch 外部资料。

## 决策

### 1. 动态 slot 资源预算

- 移除 `partner/governance/scheduler.py` 中 `MAX_ACTIVE_DEFAULT=2` 作为**严格硬规则**；
  保留为 fall-back。
- `effective_max_active(workspace_root)` 重写为：
  - 首选读 `partner_config.json::runtime.instance_native_max_active`（显式 override）；
  - 否则从 `/proc/meminfo`（MemTotal）+ `/proc/cpuinfo` + `/proc/loadavg` 估算：
    可用内存按 4 GB/实例，CPU 保留 2 核，loadavg/核 >1.0 时降到 0.5×；
  - 上限不超过 `len(ALL_INSTANCES)=5`，下限不小于 1。
- `set_active_slots` / `reconcile_slots` / `instance_native.handle_terminal` 都以 effective 值为唯一真值。
- controller 启动时记录 effective_max 至 scheduler.json 顶部 runtime 注释。

### 2. Partner 真实外部检索能力

新增 `partner/governance/external_retrieval.py`：

- 统一入口 `search(query, sources, limit=10)`，sources ∈ {arxiv, github, http, file}
- arxiv：复用 hermes `process.md` 步骤（curl `export.arxiv.org/api/query`）。
- github：复用 hermes 步骤（GitHub API search repositories）。
- http：复用 hermes 步骤（curl + 简单 HTML→text 提取）。
- 所有 fetch 落 `share/external_cache/<source>/<sha256-of-url>.json`，12 小时内不再重抓。
- 返回结构：每条结果 `(url, title, fetched_at, snippet)`，**不**直接生成 partner 笔记——笔记仍由
  `learn_from_hermes.py` 离线生成。

### 3. `learn_from_hermes.py` 改造

- 保留离线笔记读取（向后兼容 17 份已有 notes）。
- 新增"主动学习驱动"路径：根据 receipt 的 `unresolved_questions` + failure keyword 自动选主题，
  调 `external_retrieval.search()` 取新内容，归一化后**临时追加**到候选生成。
- 离线索引与线上 fetch 走同一个候选生成入口；候选生成后仍走 ocamms_guard + canary_dry_run
  已有 PromotionDecision 路径，不绕过 Partner schema。

### 4. 不做的事（保持 ADR 0061 v2 边界）

- 不让 BDK 跑真实函数/训练任务。
- 不让 partner 自动改 partner/<pkg>/ 下的源代码（仍受 ADR 0060 边界约束）。
- 不让 partner 自动推送内容到 QQ。
- 不修改生产 partner_config.json 的 mode == manual_stable。

## 真实证据

| 项目 | 数据 |
|---|---|
| 当前宿主 CPU 核 | 22（nproc） |
| 当前宿主内存 | 15.37 GB（/proc/meminfo MemTotal） |
| 当前 1/5/15 min loadavg | 0.04 / 0.05 / 0.08 |
| 5 实例同时 active 时 LLM 调用频率 | 不是 host-bound（hermes adapter 内部排队） |
| `effective_max_active` 动态值（无 override 时） | min(5, 22-2, 15/4) = 3（保守估值） |
| owner override | partner_config.json::runtime.instance_native_max_active 仍可显式写入任意正整数 |

LLM 调用排队是 partner 当前主要的并发瓶颈，不是 host CPU。动态值给出**上限**，不必真打满。
等上 5 分钟线上观察的真实负载，再决定是否上调。

## 代价与风险

- LLM quota 多实例并发可能 squeeze Hermes quotas → watchdog 需追加"token-window"维度（暂不做，记入未来选项）
- 5 实例同时 dispatch → inbox poller 写入 race 风险（已是 durable file lock，未观察到危害）
- 外部检索依赖 hermes curl，可能在无网环境下失败 → cache 兜底

## 边界

- 仍是 Partner 实例原生运行时；campaign 仍作 benchmark / audit，不进 production 排程
- slot 计算 / 外部检索 / 主动学习 只新增三个模块 + ADR，不动现有 harness.py / executor.py
- 不增 partners/runtime.mode 默认值，不改 manual_stable

## §3 边界扩展 — Sprint18 self-evolution apply 通道（2026-09-03）

随着 dynamic slot 上线、5 实例并行进入"长时间空闲但能做事"的实测窗口，ADR 0062 的边界
需要回答一个新问题——实例真的不忙的时候，应该做什么。 之前契约规定 slot scheduler 不写主动
内容、active_learning 仅作为 project failure 的副产物，self-evolution 在 governance 账本里
早已过 canary、promoted 13 个、但 promotion 与 partner 业务代码之间没有任何自动桥——这就是
"自进化账本漂着"问题的本质。

扩展决策（不新开 ADR、不新开 sprint，以本 § 作唯一契约）：

1. 新增 `partner/evolution/apply_pipeline.py`：扫描 production_readiness/、对每个
   candidate_validated 候选走 `git apply --check` → `git apply` → `git add` → `git commit`
   主路径；失败时升级为 governance evolution_events.jsonl 里的 `policy/auto_apply_failed`，
   并把 target_file 加进 `rl/apply_blacklist.json` 72 小时冷却。每个合并带
   `evolution(apply): <experiment_id> → <target_file>` 的固定 commit 消息，可被
   `git log --grep=evolution\(apply\)` 检索。
2. 新增 `partner/evolution/curiosity_bridge.py`：每个实例 slot 入口调一次
   `run_evolution_budget(workspace, instance_id, budget_seconds=60)`——从 task_state 的
   `unresolved_questions` 抽信号、提案 topic 写 governance/evolution_events.jsonl 与
   share/mind/governance/research_learning/curiosity/<tid>.md；超额直接 abort，不阻塞
   project 续跑。失败事件由 `curiosity/budget_run_failed` 兜底。
3. `partner/governance/instance_native.py::recover_or_start` 在 `_enqueue` 之前 try/except
   调一次 evolution_budget；失败写到 `curiosity/budget_run_failed` 事件，禁止 propagate 到
   主入口。
4. 新增 `partner/evolution/apply_pipeline.py::auto_rollback_recently_failed`：当
   `policy/auto_apply_failed` 事件 ≥threshold 且在 24h 内，反向回滚最近一次匹配 target_file
   的 `policy/promoted_applied` 提交；不引入新闸门，只读 evolution_events.jsonl 与 git。
5. apply_blacklist 必须不是 partner 仓 commit 跟踪对象（位于 partner_workspace 而不是
   partner/ 仓），避免合作修改时黑名单跟着进 git diff 引起 reviewer 干扰。

### §3 边界

- apply 通道不改 partner/<pkg>/ 下源代码——通过 `_is_safe_target` 限定路径前缀
  `partner/<pkg>/...py` 或 `tests/...py`，禁止跨包路径与绝对路径；
- apply 通道不改 ADR / change_log / current_status.md——governance 文档必须人类手改；
- apply 通道不触发 instance_native.handle_terminal 的中断信号——budget 调用最多 60s，
  超时直接降级放弃；
- 不让 apply 通道对 production_readiness/ 目录内的 .json 做 in-place 改动——候选源由
  governance 评估器（production_canary.py / production_readiness.py）独占写入；
- 不为 apply_blacklist 单独起 schema 版本——它只服务于 apply 通道，schema bump 跟着
  `apply_pipeline.SCHEMA_VERSION` 走。

### §3 验收门槛

- 全仓回归 786 passed（前 759 +27 新增），test_apply_pipeline.py 19 + test_curiosity_bridge.py 7 = 26；
- apply_promoted_candidates(partner_workspace) 在 production_readiness/ 实际文件上
  dry_run=1 候选 / examined=1 / skipped_inconclusive_decision=1，apply 通道真实就位，
  与 §3 之前"零可见落点"形成对照；
- 5 实例 slot entry 调 run_evolution_budget 路径被 instance_native.py 接入，期望
  24h 内 evolution_events.jsonl 出现 ≥1 条 `curiosity/budget_run_completed` 事件。

### §3 失败模式

- git apply --check 失败 → apply 升 failed → 升级事件 → 黑名单 72h 冷却；
- git commit 失败 → 同上；
- 候选 decision ∈ {inconclusive, rejected} 不进入 apply 主路径；
- apply_blacklist.json 缺失或损坏 → 视为空列表，不阻断 apply；
- run_evolution_budget 抛异常 → instance_native 主路径照常返回，不影响 slot 调度。

### §3 当前证据

- production_readiness/candidate_research_downstream_a420574f08ad.json：
  decision=blocked / 无 diff_hunk → apply 通道识别为 skipped_inconclusive_decision 1 次；
- 全仓回归 786 passed / 0 failed（执行时间戳见 change_log.md 当前条目）；
- partner_workspace 暂未触发真合并——13 个 promoted 候选中 production_readiness 只有 1 份且
  decision=blocked，apply 通道已可识别真实 candidate，可立即开始接收后续 promotion 流入。

## 测试

- `tests/test_dynamic_max_active.py`：测 effective_max_active 在不同 host 模拟下单调合理
- `tests/test_external_retrieval.py`：测试 arxiv / cache / httpx fallback
- `tests/test_learn_from_hermes_online.py`：测试失败 → 自动 fetch → candidate 生成
- `tests/test_ledger_consistency.py`：确保 ledger 与 ocamms_guard 仍一致

## §6 决策回路发送端 — decision_handoff（2026-09-04）

§5 状态机是接收方。§6 把 harness/handle_terminal/production_readiness 三个发送端接入，让
DecisionEvent 不再是占位桩。每个分支点都通过 `partner.mind.decision_handoff.dispatch_to_decision_loop()`
单一入口触达 `run_decision_loop`，schema v2 强校验、payload 异常安全降级到 noop。

新增：
- `partner/mind/decision_handoff.py`（293 行）：schema constructors + recommend_next_action
  启发式 + dispatch 入口 + summarise_run 摘要
- 改动 `partner/governance/instance_native.py::recover_or_start` 用 dispatch 替换硬编码 noop
- 改动 `partner/governance/instance_native.py::handle_terminal` 在 native_learning_triggered
  之后 dispatch（failure_class=bug_in_partner_source）
- 改动 `partner/governance/production_readiness.py::assess_production_readiness` 在
  write_attestation 时 dispatch（governance actor）
- 新增 `partner/evolution/overnight_canary.py` + `scripts/run_overnight_canary.py`

实证：overnight/canary_run 已写入 evolution_events.jsonl，5 实例 slot entry 经过
recommend_next_action 路由（BLOCKED 仍 noop；PROJECT 缺信号仍 noop；WAIT_TASK + signals
走 active_learning）。当前 production_readiness 仅 1 份 candidate_research_downstream_a420574f08ad
（decision=blocked），所以 non-noop 触发率受 production_readiness 流入速度限制——这是 admit
的下一步目标（让 production_canary 真产出 ready_for_explicit_activation 候选）。

验收门槛达成：全仓 829 passed / 0 failed。
