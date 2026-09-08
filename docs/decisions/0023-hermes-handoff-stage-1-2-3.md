# ADR 0023：Hermes 接手 — Cognition Mirror 扩展

**日期**：2026-08-28
**状态**：Accepted；所有产出保持 shadow / default-off
**上下文**：`partner_test/CURRENT_AUDIT_AND_HANDOFF.md`（已撤）、用户 2026-08-28 修订指令

## 背景

Codex 完成当前实现/集成/资料覆盖率审计后交接给 Hermes。原 prompt 要求
四阶段顺序推进（外部学习 manifest → Episode→Cognition producer → Gate C 标记 → 验证）。
Hermes 在阶段 1 偏离了意图：用户原意是"让 Partner 主动学习 hermes_external_learning"，
而非"Hermes 替 Partner 整理 manifest"。阶段 1 产出（`hermes_external_learning/manifest/`
20 条 JSON + validator 脚本）已在本 ADR 修订时一并删除。

用户 2026-08-28 修订指令明确：

- 保留 cognition_mirror_extended.py（Partner 自身能力扩展）
- 保留 Gate C route_marker + execution_marker（防交叉污染）
- 删除 manifest/ 目录（幻觉式完成）
- ocamms_validator.py 提前抽离，将作为 bdk_transformer 的一部分

## 决策

### 阶段 1（已撤回）

- 原始"manifest 化"工作删除
- 改由 Partner 04 实例自行扫描 hermes_external_learning 并产出 candidate skill —
  见后续 INTEGRATION_PLAN.md

### 阶段 2（保留）：Episode→Cognition 扩展 producer

- 新增 `partner/governance/cognition_mirror_extended.py`，提供
  `mirror_episode_state_extended(workspace, state_path, *, extended=False)`
- `extended=False`：fallback 到 `mirror_episode_state`，与原 Gate B 完全一致
- `extended=True`：在原 observation + percept 之上，从 `tool_calls` 派生
  attention/allocated、action/requested、action/completed；从 `reward_vector.values.truth`
  派生 prediction/checked
- 不臆造：hypothesis/belief/memory_consolidated/candidate/experiment/policy 一概不生成
- `tests/test_cognition_mirror_extended.py`：11 个新测试 PASSED
- 默认 `extended=False`，production mirror flag 仍为 false

### 阶段 3（保留）：Gate C 显式 route_marker + 防交叉污染

- `cognition_context.py` 新增：
  - `BASELINE_ROUTE_MARKER = "governed_v1_no_cognition_signal"`
  - `CANDIDATE_ROUTE_MARKER = "cognition_v1_soft_boost_only"`
  - `execution_marker(query, instance_id, project_id, catalog_path)` — sha256 of inputs
- 每个 pair 增加 `route_marker`（baseline/candidate）、`execution_marker`（必须相等）
- `pair.checks` 增加 `execution_markers_match` 和 `route_markers_distinct`
- 任一不满足 → `mechanical_gate_passed = False` 并写明 `isolation_failure_reason`
- `tests/test_cognition_context_markers.py`：10 个新测试 PASSED

## 测试基线

```
BDK pytest -q:                       24 passed (不退步)
Partner cognition 套件:              36 passed (15 原有 + 21 新增)
Partner 全量 pytest -q:              387 passed (366 → 387, 0 回归)
```

## 边界（与之前 ADR 一致）

- 不启动 Partner 实例；不启动长跑；不自动晋升 Candidate
- 不修改 production 配置（包括 `partner_workspace/config/partner_config.json`）
- 不进入 Gate D 业务执行
- 不把"机械门 7/7"或"extended mirror 已写"等同于"任务质量提升"或"自进化已开启"
- `candidate_cognition_context_v1` revision 3 仍为 `shadow`、`production_effective=false`

## 后续

下一阶段不再由 Hermes 手工整理外部学习；改为 Partner 04 实例主动扫描
`hermes_external_learning/` 并通过 candidate skill registry 产出 skill 条目。
具体方案见 `partner_test/bdk_transformer/INTEGRATION_PLAN.md`。
