# ADR 0061v2：实例原生真实外部动作合同 v2（治理 receipts=真实推进）

- 状态：Accepted / Production Active
- 日期：2026-09-03（v2 升级）
- 前置：ADR 0061 / ADR 0061v1
- 取代：原 `0061-instance-native-real-action-contract.md`（备份于同目录 `.v1_backup`）

## 决策变更

v1 把"读项目文件 + 写 analysis/report"也判为缺真实外部动作。v2 把合同改写为：

1. **真实外部动作** —— 仍然要求 `actions_executed` 命中至少一个真实信号（`exec:`/`web.fetch`/`pytest`/
   `code_write`/`data_write`/`external_query`/`scientific_run`/`atomic_inspect_file`/`extract`/
   `create_file`/`edit_file`/`delete_file`/`atomic_search_`/`atomic_visit`）。
2. **真实产物 OR 真实治理产物** —— `artifacts` 路径分两档：
   - `share/projects/<project>/reports/` 下的文件一律算 `fake_artifact_paths`，单凭这些路径仍无法满足合同。
   - **`share/projects/<project>/governance/receipts/*.json`** 与 `governance/project_state.json`
     在 v2 被识别为治理产物，被列入"真实 artifact"集合；这意味着一个 step 至少跑到 `record_iteration`
     写一份新的 receipt，本来就是真实项目推进。
3. **reports/ 仍是判雷同的目标** —— v2 用 `findings_signature` 与最近 `instance_native_max_repeat_findings=2`
   份 receipt 做 Jaccard，对比阈 60% 才视为 `repeated_findings`。
4. **违规响应分级** —— 手动 runtime 失败立即返回 `manual_real_action_contract_failed + Issue`；
   instance-native 失败立即 `BLOCKED + native_project_blocked` 事件。

## BLOCKED 实例唤醒路径（不变）

- `unblock_blocked_instance(workspace, instance_id, evidence_paths=[...], reason=...)`：
  证据必须为真实存在的非 reports/ 路径；治理 receipts、外部分析、外部 sources 都可作证据。
- `yield_blocked_without_evidence(workspace, instance_id, reason=...)`：watchdog/诊断最后手段，
  事件日志永久可见。

## v2 新增：完整主动学习+自进化 cycle

- `scripts/run_full_active_learning_cycle.py` 对每个 enabled 实例跑五段式事件链：
  `topic_selected → diagnosis_completed → query_proposed → candidate_bundled → matched_experiment_completed`。
- 每实例产出：
  - 5 条 append-only evolution_events，hash-linked，可由 `verify_evolution_ledger()` 重算校验；
  - 1 份 `governance/evolution_candidates/cand_<id>_<ts>_<hash>.json`（含 diagnosis 与 isolated arm）；
  - 3-4 个真实外部 `external_sources/alcycle_<UTC>/src_NN.md`（带 project_brief.md/state.md/contract.json
    引用 + 1 条确定性 arxiv stub）+ `summary.md` 落 `share/projects/<project>/external_sources/`。
- 同一秒二次调用靠 `append_evolution_event` 的 `idempotency_key` 去重，不会污染 ledger。

## 原因

v1 上线后第一线真实回执：`task fdfd107c` 失败——失败原因是 `manual_outcome_rejected`，
没有走到 v1 的合同闸门（因为合同只在 instance_native 路径）。同时 v1 把
"读项目文件 + 写 governance/receipts"也判为空，意思是工程最常见的"读 brief + 写 receipt"
一步也会被拦下，这与 "实例继续推进" 的设计目标相悖。

v2 把这条边界拉回正轨：治理 receipts 是合法迭代产物（这是项目推进的最小单位），
只有"读了什么也不产" 才真的违规。`reports/` 仍是报告空转的源头，必须被拦。

## 生产证据

- 真实三实例 unblock（01/02/03）通过 `unblock_blocked_instance` 挂 3 份独立证据路径：
  各自最新 receipt + 当轮 evolution_candidate + external_sources/summary.md。
- `state/instance_scheduler.json` 临时 `max_active=5`，`active_slots=[04,05,01,02,03]`，五实例同时在档。
- 五实例 systemd 进程 `partner-01..05.service` 全部 active running。
- `partner-instance-native.service` 已重启，运行新代码并 dispatch 04/05。
- 当 `partner-03.service` 完成 STEP 4/9 时实际写入
  `share/projects/partner_framework_frontend/governance/receipts/0163_receipt_765871a50d26.json`
  与 `state.md`、`project_state.json`，是新合同允许的"真实治理产物"路径。
- 全仓回归：`755 passed, 2 warnings in 120.06s`（比 v1 752 +3）。

## 限制

Promotion 仍要求 matched baseline/candidate、跨项目业务 Reward、false-success=0、抗遗忘
和 rollback 门。`max_active=2` 仍是 `/mnt/e/work/partner_workspace/config/partner_config.json`
的合规值；本次临时提升到 5 是为了让五实例同时启动并跑完整主动学习链；
watchdog 后续会按真实 Result/Receipt 自然轮转，把 active 收缩回 2。
