# ADR 0014：framework 限制修复（Bug #44 / #45 / #47）

**状态**: accepted
**日期**: 2026-08-27
**触发**: CDE 阶段暴露的 framework 限制让 03 / 05 真能力被压制

## 背景

CDE 阶段让 03 + 05 真启动做任务——结果发现多个 partner framework 限制压低了它们的真能力。
本次 fix 三个 root cause：

1. **Bug #44**：`generate_text` task prompt 不注入上游 step 的真实 content
2. **Bug #45**：allowed_read_roots 缺跨实例 + 实例 state 子目录
3. **Bug #47**：required_output_exts substring 误匹配（`false_word` → `.docx`）

## Bug #44：`${step_id.result.field}` data flow 失效

**问题**：03 任务 D 中，LLM 读了 96514 B 的 batch_planner.py result content，但 generate_text
task prompt 拼接时**不替换** `${step_id.result.field}` 引用——LLM 只能看到字面字符串
`${step1.result.content}`，拒绝编造 96514 B 的 patched 内容。

**修复**（`partner/mind/harness.py:_agent_event_handler`）：

```python
def _resolve_refs(text: str) -> str:
    if not text or "${" not in text:
        return text
    step_results = {}
    try:
        ti = getattr(ctx, "task_instance", None)
        if ti is not None:
            sr = getattr(ti, "step_results", None) or getattr(ti, "results", None)
            if isinstance(sr, dict):
                step_results = sr
    except Exception:
        step_results = {}
    def _replace(match):
        step_id = match.group("step")
        field = match.group("field") or "content"
        entry = step_results.get(step_id)
        if isinstance(entry, dict):
            value = entry.get(field)
        else:
            value = getattr(entry, field, None) if entry is not None else None
        if value is None:
            return match.group(0)
        if isinstance(value, str):
            return value
        try:
            import json as _json
            return _json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    return re.sub(
        r"\\$\\{?(?P<step>[A-Za-z0-9_-]+)(?:\\.result\\.(?P<field>[A-Za-z0-9_.-]+))?\\}?",
        _replace,
        text,
    )

task = _resolve_refs(task)
detailed_prompt = _resolve_refs(detailed_prompt)
```

**验证**：`tests/test_governance.py::test_agent_event_handler_substitutes_step_refs_in_prompt`

## Bug #45：allowed_read_roots 缺跨实例 + 实例 state 子目录

**问题**：05 E 任务想读 `instances/04/state/tasks/<task>/holdout_*.md`——
`allowed_read_roots` 只有 `workspace/state/tasks`（当前 instance）——跨实例被拒。
同时 `instances/05/state/queue.json` 等实例内子目录也不在白名单——05 想看自己的
queue 状态也被拒。

**修复**（`partner/planner/batch_planner.py:_manual_environment_contract`）：

```python
os.path.realpath(os.path.join(workspace, "state", "tasks")),
# Hermes 2026-08-27 fix (Bug #45): allow cross-instance reads for review
# tasks.  Also widen per-instance state/ to cover dialog / inbox / etc.
os.path.realpath(os.path.join(workspace, "state")),
os.path.realpath(os.path.join(shared_root, "instances")),
```

**验证**：`tests/test_governance.py::test_allowed_read_roots_includes_cross_instance_dirs`

## Bug #47：substring 误匹配

**问题**：03 task cba4faa8 的 user_message 含 pytest 函数名
`test_truth_quote_required_false_word`——但 `(word|docx)` regex 把 `false_word` 的 "word"
当 docx 输出关键词 → required_exts 加 `.docx` → ArtifactValidator 判 missing → task failed。
框架把"测试函数名"误判成"产物要求"。

**修复**（`partner/mind/executor.py` 第 2415 行和 2557 行）：

```python
# 之前：
if re.search(r"(word|docx)", text, re.I):
# 现在：
if re.search(r"\\b(word|docx)\\b", text, re.I):
```

**验证**：`tests/test_governance.py::test_required_output_exts_rejects_word_substring`

## 后果

- 03 / 05 后续任务不再被 framework 误判为 `expected artifacts missing`
- 03 能用 patched 上游内容做 generate_text（不再需要拒绝编造）
- 05 能真读其他 instance 的 task working_dir 跑独立评估
- 测试函数名中的 "word" / "docx" 子串不再误判

## 不重做

- 未改 task_instance.json 写入逻辑（Bug #48 task status=None 等真原因仍待查——但跟这次三个 bug 无关）
- 未扩大 allowed_read_roots 到所有磁盘路径——保守扩展到实际需要范围
- 未改 `_required_output_exts` 其他关键词 regex——只修 (word|docx) 这一处

## 全量回归

`345 passed in 9.85s`（333 + 4 Bug #38 + 1 Bug #39 + 1 Bug #40 + 3 Bug #43 + 2 Bug #44/45 + 1 Bug #47 = 345，0 回归）
