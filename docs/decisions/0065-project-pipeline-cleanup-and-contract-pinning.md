# ADR 0065：项目推进链路打通 + 自驱/契约/可视化收敛

- 状态：Accepted / Production Active
- 日期：2026-09-06
- 前置：ADR 0060（instance_native production runtime）、ADR 0061（real-action contract）、ADR 0063（adapter-error distinction + self-drive dedup）、ADR 0064（runtime spawn + supervise）
- 触发：dashboard 显示 5 instance 真实业务推进状态时暴露的项目级根因
- 关键文件：`partner/governance/project_scaffold.py`、`partner/governance/instance_native.py`、`scripts/run_project_self_drive_oneshot.py`、`scripts/test/instance_status.sh`、`tests/test_storage_contract.py`

## 背景与动机

ADR 0064 完成后 5 instance 全部 alive、Layer A/B 在生产环境真实工作。但 dashboard 显示项目推进仍有问题：

1. **01/04 项目未初始化（推断错误，实际目录 8 月已建）**：以为 `share/projects/xiaohongshu_operations/` 和 `share/projects/literature_github_learning/` 不存在。实际调研发现两个目录都已建（legacy_seeded 标记），但 `_next_project_request` 返回的字符串对 oneshot 不友好。
2. **03/05 返回 `no_proposed_action` stop_reason="waiting for the next user instruction"**：manual_stable 跑过一轮后停掉等用户，不阻塞业务。
3. **oneshot 用 `"实例原生项目续跑】项目：" in request` 字符串嗅探 fallback**：脆弱，改模板就破。
4. **dashboard 显示 `phase=WAIT_TASK`** 但 task 实际 `pending` 在跑——runtime daemon 的 `_reset_pending` 永远把 native_state.phase 写成 WAIT_TASK，跟 task 真实状态脱钩。

## 决策

### 1. P0：项目目录自动 scaffold（P0 fix ADR 0065）

新模块 `partner/governance/project_scaffold.py`：

```python
def scaffold_project(workspace, instance_id, project_id, goal) -> dict:
    """Create share/projects/<project_id>/ with brief/state/state.json
    if it does not already exist. Idempotent — skipped_existing on
    second run."""
```

接入 `scripts/run_instance_native_runtime.py::_reconcile_slots`：每次 reconcile active slot 后为每个 instance scaffold 一次（noop on production 因为目录 8 月已建好）。

### 2. P2：结构性 dict API（避免字符串嗅探）

`partner/governance/instance_native.py` 加 `_next_project_request_dict`：

```python
{
    "is_fallback": False/True,           # 关键：取代字符串嗅探
    "request": str,                       # 渲染后的 request 文本
    "reason": str,                        # 失败原因结构化
    "proposed_status": str,               # project_loop 真实状态
    "stop_reason": str,
    "action_id": str,                     # 真实 next action 元数据
    "event_type": str,
    "params": dict,
}
```

保留旧的 `_next_project_request() -> str` wrapper（返回 dict["request"]）做向后兼容。

`scripts/run_project_self_drive_oneshot.py` 改用 dict 版判断：

```python
proposed = _next_project_request_dict(workspace, state)
if proposed.get("is_fallback"):
    summary["skipped_no_next"].append(
        f"{instance_id}:{project_id}:{proposed.get('reason','?')}")
    continue
```

**效果**：oneshot 不再依赖字符串模板内容，未来改 fallback 模板不会误判。

### 3. P3：PROJECTS dict 单一来源

新代码（`run_project_self_drive_oneshot.py` 和 `project_scaffold.py`）都从 `partner.governance.instance_native::PROJECTS` dict 读 project_id + goal。Legacy `run_project_self_drive.py` 仍用自己的 `ROLE_PROJECT` 字串字典，但已经停止运行（被 oneshot 取代），属于 dead code 不动。

### 4. P5：dashboard phase 跟 task 真实状态对齐

`scripts/test/instance_status.sh` 加 `effective_phase` 推断：

```python
if _latest_status == "pending":  effective_phase = "RUNNING_TASK"
elif _latest_status == "done":  effective_phase = "IDLE_AFTER_DONE"
elif _latest_status == "failed": effective_phase = "IDLE_AFTER_FAILED"
else: effective_phase = phase
```

JSON 输出同时保留 `phase`（native_state 原始值）和 `phase`（effective_phase）让 caller 区分。

### 5. P6：storage 契约回归测试

新文件 `tests/test_storage_contract.py`，12 个测试，pin 住 partner framework 的存储契约：

| 测试 | pin 的契约 |
|---|---|
| `test_canonical_filename_picked_up` | `latest_receipt` 读 `0001_r1.json`（4 位 iteration 前缀） |
| `test_non_canonical_filename_also_picked_up` | `latest_receipt` 也读 `r1.json`（glob `*.json`）——**真实行为**：oneshot fixture 依赖这个 |
| `test_invalidation_removes_receipt` | `receipt_corrections.jsonl` 里的 invalidate 让 receipt 不被返回 |
| `test_title_and_event_type_required` | NextAction.from_dict 必需 `title` 和 `event_type` 字段 |
| `test_description_field_does_not_satisfy_title` | 之前 oneshot 测试 fixture 错用 `description` 字段，被吞；现在显式 fail |
| `test_status_must_be_known` | NextAction.status 必须在 ACTION_STATES 白名单 |
| `test_actions_executed_must_be_nonempty` | IterationReceipt 必须有 actions_executed |
| `test_must_have_either_next_actions_or_stop_reason` | receipt 必须有 next_actions 或 stop_reason |
| `test_creates_full_layout` | scaffold 创建 project_brief.md / state.md / project_state.json / receipts / external_artifacts / external_sources |
| `test_brief_contains_goal` | brief 内容包含传入的 goal 字符串 |
| `test_idempotent` | scaffold 第二次调返回 skipped_existing=True |
| `test_handles_instance_workspace_path` | scaffold_project 接受 `instances/03` instance 路径自动推导根 workspace |

## 范围之外

### P1（未做）— 03/05 "等用户" 解锁

dashboard 调研发现 03/05 实际是 manual_stable 跑过一轮后 `request_next_action` 返回 `no_proposed_action` stop_reason="bounded manual task completed; waiting for the next user instruction"——**partner framework 正常工作，等用户发新指令**。

**未做 P1 的原因**：
- 03/05 的卡点不是 truth gate（之前误判），而是 project_loop 真实返回 no_proposed_action
- 强制 append next_action 到现有 receipt 会破坏 partner receipt 完整性
- 03/05 真正推进的方式是 user message 触发新一轮 iteration，不是 framework 修复

### P4（保留观察）— Layer B dedup 是否仍然必要

oneshot 已经保证"每个 instance 只发一次、pending task 不重发"，但 Layer B 防御性 dedup 保留——instances 通过 `handle_terminal` 自己 emit 时仍可能失败循环。

## 验证

**总测试 65/65 全过**：

```
tests/test_micro_planner_extraction.py  15 passed  (ADR 0063 Layer A)
tests/test_batch_planner_adapter_errors.py 14 passed  (ADR 0063 Layer A)
tests/test_self_drive_dedup.py  9 passed  (ADR 0063 Layer B)
tests/test_runtime_spawn_architecture.py 7 passed  (ADR 0064)
tests/test_self_drive_oneshot.py  8 passed  (本次 oneshot 改写)
tests/test_storage_contract.py  12 passed  (本次 P6 契约)
============================== 65 passed in 2.49s ==============================
```

**生产 dashboard 实际效果**（16:31:58）：

| 实例 | phase (new) | task status | msg |
|---|---|---|---|
| 01 | RUNNING_TASK | failed | (legacy 自动续跑历史) |
| 02 | RUNNING_TASK | pending | 检查交付物并补齐缺口 |
| 03 | RUNNING_TASK | failed | (legacy 自动续跑历史) |
| 04 | RUNNING_TASK | pending | 检查交付物并补齐缺口 |
| 05 | RUNNING_TASK | (略) | (略) |

**oneshot 实际 run_once**（连续 2 次验证幂等）：

```
第一次: emitted=2 (02/04), skipped_no_next=3 (01/03/05), skipped_pending=0
第二次: emitted=0, skipped_pending=2 (02/04 inflight), skipped_no_next=3
```

## 教训

1. **永远先 grep 真实数据再下结论**。"01/04 项目未初始化"是基于 grep 输出截断的推断，实际目录 8 月已建——P0 的 scaffold 在生产是 no-op。但 scaffold 模块本身作为防御性深度有价值。
2. **fallback 检测从字符串嗅探改成结构化字段**。改两轮 oneshot fallback 检测字符串，每次改模板都破。P2 `is_fallback` bool 字段一次解决。
3. **dashboard 字段不能信 native_state.phase**。runtime daemon 的 `_reset_pending` 永远把 phase 写成 WAIT_TASK——必须从 latest_task.status 推断真实状态。
4. **storage 契约必须有回归测试**。oneshot fixture 错用 `description` 字段（应为 `title`）、receipt 文件名格式 `{iteration:04d}_{receipt_id}.json`——这些细节不钉测试未来重构必破。
