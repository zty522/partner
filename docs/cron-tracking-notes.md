
## Round 5 tracking 2026-08-23 07:05

### Bug #37 (NEW): TASK_FAILED handler does NOT trigger iteration chain

**Symptom**: 02 实例 task 30288f34 at 07:05:39 — `batch_plan_handler_failed` → TASK_FAILED event emitted → `_handle_task_failed` (L3735) delivers failure message to user → **iteration chain (atomic_strict_reflect → atomic_next_iteration) NOT triggered**
→ 0 events in evolution.jsonl last 30 min
→ 02 instance idle waits for next user message (silent dead-state)

**根因**: `partner/mind/executor.py:6805` emits TASK_FAILED but `_handle_task_failed` (L3735) only does `deliver(...)` — no v2 iter_event call. Skill 中 "TASK_FAILED → strict_reflect" 描述与代码不符。

**Evidence**:
- executor.py grep 'iteration' = 0 matches (no integration with v2 iteration_events.py)
- 02 task 30288f34 task_log.jsonl 13 行无 strict_reflect / next_iteration / iter_event_start
- evolution.jsonl 0 events in last 30 min (00:51-07:05)
- Bug #36 dead-state idle 和 Bug #37 直接相关：failed batch_plan TASK_FAILED 不触发 iter → instance 静默 idle

**Severity**: Medium. 整个 batch_plan failure 路径都断在这一环，让"自迭代"承诺失效。

**Fix direction**: 在 `_handle_task_failed` (L3735) 末尾追加 `_atomic_write_artifact` 类似的 hook:
```python
# after deliver(...) in _handle_task_failed (L3772)
try:
    from partner.v2.iteration_events import atomic_strict_reflect
    iter_ctx = SimpleNamespace(
        workspace=_workspace,
        task_instance=task,
        working_dir=task.working_dir,
        failed_at_step=failed_at_step,
        error=error[:500],
    )
    sr_result = await atomic_strict_reflect(iter_ctx, {"round": 1})
    if sr_result and sr_result.get("ok"):
        from partner.v2.iteration_events import atomic_next_iteration
        ni_result = await atomic_next_iteration(iter_ctx, {"round": 1, "max_iterations": 3})
except Exception as _e:
    logger.debug("[TASK_FAILED] iter chain trigger failed: %s", _e)
```

**追踪信号 (cron 必查)**:
- batch_plan_handler_failed → 0 iter events 在 task_log.jsonl → 报 Bug #37
- 单实例 30+ min 无新事件 + task_log 末尾 batch_plan_handler_failed → 强信号
