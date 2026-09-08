# ADR 0032：恢复 Event-first 与可执行 Candidate 合同

**日期**：2026-08-29  
**状态**：Accepted；生产默认不变

## 问题

ADR 0023–0031 将 Hermes 笔记直接包装成 Candidate Skill，并用测试硬编码的奖励写入真实 RL 账本和
`control_policy.json`。这混淆了 Event、知识、Skill、Candidate 和 Policy：文件写入链路虽然可运行，
但没有真实任务效果，且02的 BDK handler 曾只存在于自己的模块，未进入主 executor handler 集合。

## 决策

1. Event 是进化状态变化的权威记录；Candidate JSON只是 artifact。
2. 新增 hash-linked `evolution_events.jsonl`，覆盖 Issue、Candidate、执行、Experiment 和 Policy 决策。
3. Candidate 新增 `artifact_type`、`execution_contract`、`execution_ready`；命名 Candidate 的 promotion 必须
   可执行且与 Experiment 绑定。
4. Candidate只允许调用白名单 Event，不允许持久化任意代码后直接执行。
5. Hermes笔记标为 `knowledge_draft`，不可执行、不可晋升；它只能作为未来 Candidate 的来源证据。
6. `targetdiff_bdk_function_pool` 与 `execute_candidate` 加入 executor handler 集合，仍只接受显式手动/shadow调用。
7. `test_bdk_real_promote.py` 改用 pytest临时 workspace，不再读写真实 Partner workspace。
8. 旧测试轨迹及 `bdk_closure_*` 纠正为 synthetic plumbing evidence，移出有效策略并禁止进入RL统计。

## BDK与RL边界

当前 BDK FunctionPool 是数值函数学习器，不是Agent底层认知运行时；不会作为Partner全局底座上线。
BO/MaxVar可以替代“连续实验选点”里的RL，但不能证明Partner在跨任务策略选择上不需要bandit/offline RL。
算法选择必须跟决策类型匹配。

## 证据

- `tests/test_event_first_candidates.py`
- `tests/test_bdk_real_promote.py`（已隔离）
- `docs/architecture/event_first_self_evolution.md`
- `/mnt/e/work/partner_workspace/share/mind/governance/evolution_record_corrections.jsonl`

## 生产影响

- `runtime.mode=manual_stable` 不变。
- 自动 Campaign、自动迭代、自愈和 cron 不开启。
- 删除的 `bdk_closure_1788004048` control映射从未有真实 production choices，因此纠正不会改变用户任务路径。
- 保留此前日志用于审计，不物理删除。
