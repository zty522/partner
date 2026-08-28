# ADR 0009: manual_stable handoff contract — shape (a)/(b) 分类 + opt-in

**状态**: accepted
**日期**: 2026-08-27
**触发**: Bug #40 — 03 任务 1/3 端到端跑通后, governance 报 unlinked_previous_receipt

## 背景

2026-08-27 推进 03 任务 1/3 (只读诊断) 端到端跑通——`push_files` 真发 QQ、4 步全过、`delivery_confirmed=True`、`completion_ok=True`。但 `record_manual_task_outcome` 返回:

```json
{"ok": false, "status": "unlinked_previous_receipt", "latest_receipt_id": "receipt_93fffba62011"}
```

→ partner_status `completion_status="failed"` → task_instance `mark("failed")`。

## 根因

`partner/governance/manual_runtime.py` 第 380 行的 contract (Codex 8/25 改):

```python
previous = latest_receipt(workspace, project_id)
if previous and previous.artifacts and not _handoff_present(previous.artifacts, inputs):
    issue = record_issue(workspace, {...severity: "high"...})
    return {"ok": False, "status": "unlinked_previous_receipt", ...}
```

该 contract 把两种语义不同的失败场景合并成一种 hard reject:

- **Shape (a)**: 任务 `inputs=[]`, 因为用户从 desktop_inbox 发起独立任务, 没有可承接
  的前序 receipt. 这是合法 self-contained 任务, 但 contract 把它当"未接前序"拒绝
- **Shape (b)**: 任务 `inputs=[some_other_path]`, 故意绕开 previous 的 artifacts 路径
  ——这是真正的"前序断裂"信号

03 任务 1/3 是 shape (a)——通过 desktop_inbox 收到 Hermes 的任务, 原 project
`partner_framework_frontend` 上一条 receipt 是 8-25 的 `receipt_93fffba62011` (别人的任务, artifacts 完全无关), 导致 `_handoff_present` 永远返回 False, contract 永远拒绝。

## 修复

### Step 1: `record_manual_task_outcome` 区分 shape (a) vs (b)

```python
# partner/governance/manual_runtime.py
previous = latest_receipt(workspace, project_id)
ignore_handoff_check = bool(params.get("ignore_handoff_check", False))
if previous and previous.artifacts and not _handoff_present(previous.artifacts, inputs):
    shape_b_reject = bool(inputs)
    if shape_b_reject:
        # 真正的"前序断裂"——保持原 hard reject 行为
        issue = record_issue(workspace, {...severity: "high"...})
        return {"ok": False, "status": "unlinked_previous_receipt", ...}
    if not ignore_handoff_check:
        # shape (a): inbox-triggered 独立任务——只记 info issue 不阻塞
        record_issue(workspace, {...severity: "info"...})
```

### Step 2: 把 `ignore_handoff_check` 透传到 `record_iteration`

`record_manual_task_outcome` 内部调 `record_iteration` (`partner/governance/project_loop.py`), 后者**独立**有一个 handoff check 抛 `ValueError`——如果不透传 opt-in, shape (a) 任务仍会被拒绝并返回 `invalid_iteration_receipt`:

```python
# partner/governance/project_loop.py
if previous and not _artifact_handoff(previous, inputs):
    if params.get("ignore_handoff_check"):
        pass
    else:
        raise ValueError("new iteration must reference at least one previous artifact")
```

### Step 3: 上游自动 opt-in

`partner/mind/executor.py:9635` (手动任务完成点) 探测任务来源, 自动给 inbox 触发的任务传 `ignore_handoff_check=True`:

```python
# partner/mind/executor.py
_is_inbox_triggered = bool(
    payload.get("inbox_message_id") or payload.get("trigger_source") == "inbox"
)
governance_result = record_manual_task_outcome(_workspace, {
    ...
    "ignore_handoff_check": _is_inbox_triggered,
})
```

## 回归测试

`tests/test_manual_governance.py::test_manual_followup_requires_actual_previous_artifact_input` 更新为四段断言:
- task "one" `inputs=[]` + opt-in → accepted (shape a)
- task "two" `inputs=[]` + opt-in → accepted (shape a, 接 task one 失败)
- task "three" `inputs=[unrelated.md]` 不带 opt-in → rejected (shape b) → `status="unlinked_previous_receipt"`
- task "four" `inputs=[second.md]` 不带 opt-in → accepted (真正承接 task two 的 artifact) → iteration 递增到 3

## 后果

1. shape (a) inbox 任务不再被 governance 阻塞, IssueRecord 仍以 `severity=info` 记录给 05 独立审查 + shadow-replay evaluator
2. shape (b) 真前序断裂保持原 hard reject 行为
3. iter counter 对 shape (a) 独立任务: 每个独立 task 占用一个 iteration slot, 但 artifacts 列表不传染
4. 手动任务完成路径 `partner/mind/executor.py:9635` 自动给 inbox 触发的任务传 opt-in
5. 全量回归: 337 → 339 passed (+ 1 Bug #38 round 2 + 1 Bug #39 = 339, 0 回归)
6. 未变更边界:
   - 未改 `_handoff_present` 自身的逻辑
   - 未改 `record_iteration` 的 iteration 序号递增逻辑
   - 未改 shadow-replay 评估器
   - 未改 `record_issue` 严重程度映射

## 不重做

- 未给 `record_iteration` 加"shape (a) 自动通过"的隐式行为——必须有显式 opt-in
- 未把 `ignore_handoff_check` 默认值改成 True——保持 backwards-compat
- 未把"info issue"和"high issue"的 severity 合并
