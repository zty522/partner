# ADR 0015：framework 限制最后一轮修复（Bug #45 完全 working + Bug #48）

**状态**: accepted
**日期**: 2026-08-27
**触发**: 第二轮 framework 修复（Phase 1）

## 背景

ADR 0014 修了 Bug #44/#45/#47，但 (a) Bug #45 跨实例说明不完整 + (b) Bug #48 task status=None
仍未修。Bug #46 经诊断发现不是真 bug（execute_code 本身就不该有 path 校验——
plan preflight 误读 parameters['code'] 为 path 已通过 Bug #43 修复消失）。

## Bug #45：跨实例 review 完全 working + 文档化

**问题**：ADR 0014 修了 allowed_read_roots 加 `workspace/state` 和 `shared_root/instances`——
但 `_manual_environment_contract` 的 contract 文案没明确说"跨实例读可用"，LLM 不知
道能直接 atomic_inspect_file 其他 instance 的 state/tasks/* 路径，会改用
atomic_list_project_files workaround（被 partner framework 拒绝）。

**修复**（`partner/planner/batch_planner.py`）：

```python
# Hermes 2026-08-27 fix (Bug #45 documentation): clarify cross-instance
# read capability.  allowed_read_roots already includes both the
# per-instance state/ root and the shared instances/ root, so a
# reviewer instance can read another instance's task working_dir,
# dialog_history, or inbox.  Document it here so the planner LLM
# knows cross-instance atomic_inspect_file calls are valid without
# falling back to list_directory workarounds.
+ "- 跨实例审阅（如 05 评估 04 的 holdout 产物）可直接用 atomic_inspect_file 读取其他实例的 state/tasks/*、dialog_history、recommendations 路径——已在 allowed_read_roots 白名单内。\n"
```

## Bug #46：不是真 bug

**诊断**：`partner/planner/batch_planner.py:718` 的 preflight 只对
`atomic_inspect_file / read_file / list_directory` 做路径校验——execute_code 本身
没 path 校验（脚本默认在 workdir 跑）。05 v1 E 任务的 step4 报 "outside allowed roots"
是 `atomic_list_project_files`（不是 execute_code），该 issue 已被 ADR 0007
的 preflight 修复 + Bug #43 修后消失。

**不修**：Bug #46 不存在实际 framework bug，execute_code 的设计是合理的（脚本跑在
workdir + 用户代码自负责任）。如果需要更严格路径校验，是 policy 设计问题（让
LLM 不能调任意 import），不是 framework bug。

## Bug #48：TaskInstance status=None

**问题**：03 + 05 真任务的 task_instance.json 顶层 `status` 字段一直是 None——`mark()`
只设 `self.completion_status` 不设 `self.status`，且 TaskInstance 是 `@dataclass`，
`status` 没在字段里——`asdict(self)` 不导出。

**修复**（`partner/harness_core/task_instance.py`）：

```python
class TaskInstance:
    ...
    completion_status: str = "pending"
    # Hermes 2026-08-27 fix (Bug #48): mirror completion_status with the
    # canonical top-level status field.  asdict() persists both, so
    # downstream monitors can rely on task_instance.json["status"] to
    # know whether the task has finished (was previously always None).
    status: str = "pending"
    ...
    def mark(self, status: str, data: JsonDict | None = None) -> None:
        ...
        # Hermes 2026-08-27 fix (Bug #48): mirror the status into the top-level
        # self.status field.
        self.status = status
        self.completion_status = status
        self.save()
```

**测试**：`tests/test_governance.py::test_task_instance_mark_writes_top_level_status`

## 全量回归

`346 passed in 11.58s`（333 + 4 Bug #38 + 1 Bug #39 + 1 Bug #40 + 3 Bug #43 + 2 Bug #44/45 + 1 Bug #47 + 1 Bug #48 = 346，0 回归）

## 后果

- 05 跨实例审阅 task 现在能直接 atomic_inspect_file `instances/04/state/tasks/*/holdout_*.md`
- task_instance.json 顶层 status 字段真持久化——下游 monitor + 05 RecommendationRecord 能可靠判断 task 是否完成
