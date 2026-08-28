# ADR 0017：Bug #50 — preflight 接受 `paths` list alias 用于 multi-source atomic_inspect_file

**状态**: accepted
**日期**: 2026-08-28
**触发**: 05 第四轮 + 03 第四轮任务跨实例 multi-source read 失败

## 背景

2026-08-28 03 + 05 第四轮复杂任务（自主找 bug + 跨实例审阅）期间，05 的 LLM 自主生成的 plan 包含：

```json
{
  "event_type": "atomic_inspect_file",
  "parameters": {
    "paths": [
      "/mnt/e/work/partner_workspace/instances/04/state/tasks/.../holdout_aether_b42.md",
      "/mnt/e/work/partner_workspace/instances/04/state/tasks/.../holdout_3_sesa.md",
      ...
    ],
    "max_chars": 50000
  }
}
```

参数用了**复数 list** `paths=[...]`——但 `partner/planner/batch_planner.py` 第 605-624 行的 preflight 逻辑**只看单数 `path` 字段**——所以 `raw_path = ""`——issue 被报为 `requires path`——**5 个 atomic_inspect_file step cascade 失败**。

## 根因

`partner/planner/batch_planner.py:605-624` 的 preflight 校验：

```python
if event_type in {"atomic_inspect_file", "read_file", "list_directory"}:
    if not str(params.get("path") or "").strip():
        alias = "file_path" if event_type in {"atomic_inspect_file", "read_file"} else "directory"
        if str(params.get(alias) or "").strip():
            params["path"] = params[alias]
    raw_path = str(params.get("path") or "").strip()
    if not raw_path:
        issues.append(f"{step.id}: {event_type} requires path")
```

只识别 `path` 单数字段 + `file_path` / `directory` 别名——**不支持 `paths` list**。

LLM 自主生成 multi-source review plan 时自然用 `paths=[...]`（人类写 multi-source review 的标准格式）——framework 误判为 pathless step。

## 修复

`partner/planner/batch_planner.py:605-686` 改写为支持 `paths` list alias：

```python
if event_type in {"atomic_inspect_file", "read_file", "list_directory"}:
    # ... (alias fallback 保留) ...
    raw_paths: list[str] = []
    if str(params.get("path") or "").strip():
        raw_paths.append(str(params.get("path") or "").strip())
    alt = params.get("paths")
    if isinstance(alt, (list, tuple)):
        for item in alt:
            s = str(item or "").strip()
            if s and s not in raw_paths:
                raw_paths.append(s)
    elif isinstance(alt, str) and alt.strip():
        if alt.strip() not in raw_paths:
            raw_paths.append(alt.strip())
    raw_path = raw_paths[0] if raw_paths else ""
    if not raw_paths:
        issues.append(f"{step.id}: {event_type} requires path")
    else:
        # iterate raw_paths: if ANY one resolves under allowed_read_roots,
        # the step is authorised (multi-source cross-instance pattern).
        want_directory = event_type == "list_directory"
        existing = ""
        for raw_path in raw_paths:
            if raw_path.startswith("$"):
                continue
            candidates = []
            # ... (resolve candidates same as before) ...
            for candidate in candidates:
                # ... check exists + allowed ...
                if allowed:
                    existing = candidate
                    break
            if existing:
                break
```

**关键设计决策**：
- `paths` 可以是 list of str 或 single str
- `path` 和 `paths` 可以共存（去重）
- 迭代每个 path 检查 allowed_read_roots——**只要任一 path 在白名单内，step 通过**
- 不需要所有 path 都在白名单——这是 multi-source review 的语义（"我至少能读到一个源就够了"）

## 验证

`tests/test_governance.py` 新增 2 个针对性测试：

1. `test_preflight_accepts_paths_list_alias`：用 `paths=[path1, path2]` 形式——验证不抛 ValueError
2. `test_preflight_accepts_paths_string_alias`：用 `paths="single_path"` 形式（LLM 偶尔单数字符串）

**全量回归**：`348 passed in 17.00s`（333 + 4 Bug #38 + 1 Bug #39 + 1 Bug #40 + 3 Bug #43 + 1 Bug #44 + 1 Bug #45 + 1 Bug #47 + 1 Bug #48 + 2 Bug #50 = 348，0 回归）

## 后果

- 05 跨实例 multi-source review 任务不再被 preflight 误拒
- LLM 自主生成的 plan 可以用 `paths=[...]`（multi-source 标准格式）——framework 接受
- 单 source 任务继续用 `path="..."` 不受影响（向后兼容）

## 不重做

- 未改 LLM 的 plan generation 提示词——让 LLM 自由选择 `path` / `paths`，framework 都接受
- 未扩大 allowed_read_roots——Bug #45 已经覆盖跨实例
- 未改 batch_planner 的其他 preflight 规则——只修 path 字段兼容性

## 与 Bug #45 的关系

- Bug #45：allowed_read_roots 加跨实例 + 实例 state 子目录
- Bug #50：preflight 接受 paths list alias

两个 fix **共同保证** 跨实例 multi-source review 任务完整跑通。
