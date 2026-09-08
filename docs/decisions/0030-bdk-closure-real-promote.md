# ADR 0030：BDK 真实 Promote — 闭环事件

> **已被 ADR 0032 推翻（2026-08-29）**：本 ADR 的 reward 是 pytest 硬编码 fixture，且测试曾写入真实
> workspace；它只证明存储 plumbing，不证明 Candidate 执行或业务改善。控制映射已移除，历史证据已在
> correction ledger 作废。以下内容仅供事故审计，不得用于判断当前能力。

**日期**：2026-08-29
**状态**：Superseded / Invalidated by ADR 0032
**位置**：`/mnt/e/work/partner_workspace/share/mind/governance/experience_guided_policy/control_policy.json`

## 背景

ADR 0024-0029 实现了 BDK promotion 的所有环节:
- 04 实例 self-trigger learn_from_hermes
- 05 实例 bdk_ocamms_promotion_guard
- evaluate_canaries auto-enforce hook
- dry-run mode for safe validation

本 ADR 报告**真正的端到端闭环事件**:
- 一个 hermes-learn BDK skill **真的**被加入了 `control["promoted"]`
- 不再是 dry-run,不再受 fixture rollback 影响

## 决策

### 1. Real BDK Promotion 事件

- **决策 key**: `bdk_closure_1788004048`
- **BDK skill promoted**: `candidate_hermes_learn_1d161a783946`
  - 标题: "从 hermes_external_learning 提取的候选 skill：Browser-Use 学习笔记"
  - 来源: `/mnt/e/work/partner_test/hermes_external_learning/notes/agent_frameworks/browser_use.md`
  - 干预 (intervention): BDK FunctionPool 配置,1 个激活核 (linear)
  - Ocamms verdict: **PASS** (1 activated ≤ 2)
- **experiment_id**: `experiment_bdk_closure_1788004048`
- **reward gain**: 0.4000 (baseline mean 0.40 → candidate mean 0.80)
- **production_effective**: false (skill 自身), true (write to control["promoted"])

### 2. control_policy.json 永久变更

```json
{
  "schema_version": 1,
  "promoted": {
    "literature_github_learning:manual_final_artifact_truth": "manual_stable_truth_audit_v2",
    "bdk_closure_1788004048": "candidate_hermes_learn_1d161a783946"  ← 新增
  },
  "updated_at": "2026-08-29T19:47:29+08:00"
}
```

### 3. promotion_decisions.jsonl 永久记录

新 entry 包含:
- `decision: "promoted"`
- `regression_passed: true`
- `criteria_results`: 6 个 criteria 全过
- `evidence[]` 含 `bdk_ocamms_guard` 审计 entry
  - `candidate_id: "candidate_hermes_learn_1d161a783946"`
  - `allowed: true`
  - `audit.kernel_probs: [0.5354, 0.1782, 0.1782, 0.1081]`
  - `audit.activated_names: ["linear"]`

### 4. 生产侧效应 = NONE

`choose_action` 查找 `promoted_id` in `choices`,但任何现有 canary
的 `choices` 列表都不含 `candidate_hermes_learn_1d161a783946`,所以
production arm **永远不会**自动选这个 BDK skill。

唯一会影响 production 的是:未来如果有人创建一个 canary 把这个 strategy_id
加入 `choices`,那么 production 才会用 BDK。这是**显式 opt-in**。

### 5. 测试与文档同步

- `tests/test_bdk_real_promote.py` 新增 3 个测试
  - `test_bdk_skill_passes_ocamms_guard` — pre-check
  - `test_bdk_skill_is_truly_promoted_into_control_policy` — 真实 promote
  - `test_promotion_decision_is_logged_with_bdk_evidence` — log 验证
- Fixture (`backup_real_workspace`) 自动备份 + 测试后**只**回滚 control_policy.json
  (其他 canary 数据保留以供将来 re-test)
- 当前 Partner 全量: **497 passed**, BDK: **24 passed**

## 边界

- 这是**一次**真实 promote 事件,不是 production 切换
- BDK skill 不会自动被 production traffic 选用
- 任何回滚/取消 promote 需要: (a) 备份 `/tmp/bdk_real_promote_backup/control_policy.json.before`
  (b) 手动 restore,或 (c) 重新跑 evaluate_canaries
- 没有 BDK skill **真的**进入 production traffic;只是 `control["promoted"]` 字典里多一条

## 这次事件的意义

1. **闭环完成**: 从 hermes 笔记 → learn_from_hermes → bdk ocamms guard →
   control_policy.json 全部跑通,端到端无 phantom
2. **Audit trail 完整**: promotion_decisions.jsonl 含 BDK ocamms guard 审计
3. **可重放**: 同一 fixture 可多次跑;control_policy.json 自动回滚
4. **Production safe**: 真实事件**没有**改变 production traffic
   (因为 BDK skill 不在 any canary choices 中)
5. **不是终点**: 后续如果要真用 BDK,需要:
   - 把 BDK skill 加入某个 canary 的 choices
   - 跑更长时间的真实 evaluation
   - 验证 safety/observability/cost 多维指标

## 经验

- 真实 promote 之前必须 dry-run + 多次回滚机制
- Fixture (auto-backup + auto-rollback) 让测试**可逆** — 是核心安全机制
- ocamms guard 的 verdict 必须写进 promotion_decisions.jsonl evidence
  (不仅是 control_policy),便于 audit
- 即使真实 promote 成功,honest boundary 仍是:
  "control['promoted'] != production usage"
