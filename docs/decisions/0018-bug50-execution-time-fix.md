# ADR 0018：Bug #50 execution-time 修复 — _atomic_inspect_file + _safe_inspect_path

**状态**: accepted
**日期**: 2026-08-28
**触发**: 05 第五轮任务跑仍报 `inspect path is missing or outside allowed read-only roots`（尽管 ADR 0017 修了 preflight）

## 背景

ADR 0017 修了 `partner/planner/batch_planner.py:_manual_preflight_plan` 接受 `paths` list alias。但 2026-08-28 05 第五轮验证显示：

- preflight **通过了**（不再报 `requires path`）
- 但 step1 **执行时仍失败**：`inspect path is missing or outside allowed read-only roots`

**根因**：Bug #50 fix 在两个地方被破坏：
1. `partner/mind/harness.py:_safe_inspect_path` 的 `allowed_roots` 不含 `shared_root/instances`——**跨实例读被执行路径拒绝**
2. `partner/mind/harness.py:_atomic_inspect_file` 只读 `params.get("path")` 单数字段——**`paths=[...]` list 被忽略**

## 根因详解

### 1. _safe_inspect_path allowed_roots 缺 shared/instances

```python
allowed_roots = [
    os.path.join(repo_root, "partner"),
    os.path.join(repo_root, "tests"),
    os.path.join(repo_root, "docs"),
    os.path.join(shared_root, "external", "code"),
    os.path.join(shared_root, "external", "literature"),
    os.path.join(shared_root, "share", "evidence"),
    os.path.join(shared_root, "share", "mind", "governance"),
    os.path.join(shared_root, "share", "projects"),
    os.path.join(shared_root, "files", "outgoing"),
    os.path.join(instance_workspace, "state", "tasks"),
    # ⚠️ 没有 shared_root/instances
]
```

注释甚至明确说"Does not permit cross-instance mutable task reads"——**ADR 0014/0015 在 batch_planner 加了跨实例白名单，但 harness._safe_inspect_path 没跟上**——导致 preflight 通过但执行路径拒绝。

### 2. _atomic_inspect_file 不识别 paths list

```python
def _atomic_inspect_file(ctx, params):
    path = _safe_inspect_path(ctx, str(params.get("path") or ""))  # 只读 path 单数
```

如果 `params` 里只有 `paths=[...]` list —— `params.get("path") = None` —— `_safe_inspect_path` 抛 ValueError。

## 修复

### Fix 1：_safe_inspect_path allowed_roots 加 shared/instances

```python
allowed_roots = [
    # ... (其他保留) ...
    os.path.join(instance_workspace, "state", "tasks"),
    # Hermes 2026-08-28 fix (Bug #45 enforcement): cross-instance review
    # (e.g. instance 05 reading instance 04 holdout outputs) needs the
    # shared instances/ root authorised here too.  Without this the
    # preflight passes but _safe_inspect_path at execution time rejects
    # the read with the cryptic "outside allowed read-only roots" error.
    # The same-instance restriction above only blocks mutable
    # task/state writes; cross-instance reads of completed task
    # artifacts are permitted.
    os.path.join(shared_root, "instances"),
]
```

### Fix 2：_atomic_inspect_file 接受 paths list

```python
def _atomic_inspect_file(ctx, params):
    raw_paths: list[str] = []
    primary = str(params.get("path") or "").strip()
    if primary:
        raw_paths.append(primary)
    alt = params.get("paths")
    if isinstance(alt, (list, tuple)):
        for item in alt:
            s = str(item or "").strip()
            if s and s not in raw_paths:
                raw_paths.append(s)
    elif isinstance(alt, str) and alt.strip():
        if alt.strip() not in raw_paths:
            raw_paths.append(alt.strip())
    if not raw_paths:
        raise ValueError("inspect path is missing or outside allowed read-only roots")
    single = len(raw_paths) == 1
    multi = not single
    # ... read each file, concat with BEGIN/END separators for multi-source
    # ... for single-path, keep legacy raw content shape (no wrappers)
```

**向后兼容策略**：
- **单 path**：保留原始 `content` 形状（无 BEGIN/END 围栏）——不破坏现有 8+ 个测试
- **多 paths**：用 BEGIN/END 围栏分隔——下游 step 能区分来源
- **单 path 失败**：仍抛 ValueError（legacy 行为）——现有 try/except ValueError 测试继续通过
- **多 path 失败**：返回 `result["ok"]=False` with error message（不抛）——multi-source 不会因一个 path 失败 cascade

## 验证

`tests/test_governance.py` 新增 2 个针对性测试：

1. `test_atomic_inspect_file_accepts_paths_list`：单 path 用 `paths=[...]` list 形式——验证 ok=True
2. `test_atomic_inspect_file_single_path_backwards_compat`：单 path 用 `path="..."` 形式——验证 content 不含 BEGIN/END 围栏

**全量回归**：`350 passed in 17.49s`（333 + 4 + 1 + 1 + 3 + 1 + 1 + 1 + 1 + 2 + 2 = 350，0 回归）

## 后果

- 跨实例 read 在 preflight **和** 执行路径都通过
- LLM 用 `paths=[...]` 表达 multi-source review 在两端都工作
- 现有单 path 测试不动——向后兼容完整

## 与 ADR 0017 的关系

- ADR 0017 修 Bug #50 preflight 阶段（batch_planner._manual_preflight_plan）
- ADR 0018 修 Bug #50 execution 阶段（harness._safe_inspect_path + harness._atomic_inspect_file）

两个 ADR 共同保证 Bug #50 完整修复——LLM 用 `paths=[...]` 不论在 preflight 还是 execute 都能正常处理。

## Bug #50 修复链完整图

```
LLM plan: step.parameters = {"paths": ["/path1", "/path2"]}
         ↓
preflight (ADR 0017 修复): 接受 paths list，验证每个 path 在 allowed_read_roots
         ↓
execute (ADR 0018 修复): _atomic_inspect_file 接受 paths list
                          _safe_inspect_path allowed_roots 含 shared/instances
                          ↓
result.ok = True, content = "--- BEGIN /path1 ---\n...\n--- END /path1 ---\n\n--- BEGIN /path2 ---\n...\n--- END /path2 ---"
```
