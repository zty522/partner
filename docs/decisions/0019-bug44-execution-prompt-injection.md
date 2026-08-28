# ADR 0019：Bug #44 完整修复 — `${step_X.result.content}` 引用注入到 generate_text prompt

**状态**: accepted
**日期**: 2026-08-28
**触发**: 03 第六轮任务暴露 generate_text prompt 没拿到上游 step content

## 背景

ADR 0014 改了 `partner/planner/batch_planner.py:normalize_ref_syntax` 把 `${step1.result.content}` braces形式标准化为 `$step_1.result.content`——但**只做格式转换，不做实际值替换**。

下游 step (generate_text) 的 `_agent_event_handler` (harness.py:4797) 处理 prompt 时**没调用 `_resolve_step_variables`**——所以 `${step1.result.content}` 字面字符串被传给 LLM——LLM 看到的是字面 `${step1.result.content}` 不是真实上游内容——LLM 拒绝编造。

03 第六轮任务（用 `${step1.result.content}` `${step2.result.content}` 引用）的 step3 result:
> "step1 data — but it is **truncated** mid-function ... No `path`, `size`, or `hex` metadata is included"
> "_resolve_refs: token not located inside the step1 excerpt supplied. Not asserted."

LLM 实际上**没收到 step1 真读内容**——只收到了"prompt 字面"或被截断。

## 根因完整链路

```
LLM plan: step3.parameters = {
  "prompt": "基于 ${step1.result.content} 和 ${step2.result.content} 的真实读取内容撰写..."
}
         ↓
batch_planner.py:normalize_ref_syntax():
  "标准化 ${step1.result.content} 为 ${step1.result.content}" (格式不变)
         ↓
_execute_step (harness.py) 调用 step3 event handler:
  _agent_event_handler(ctx, params)
  ├── task = params.get("task") or params.get("prompt")  # 字面 ${step1.result.content}
  ├── supplied_context = params.get("data") or content  # 空或 LLM 没填
  ├── agent.execute(task)  # LLM 看到字面 ${step1.result.content}
         ↓
LLM: "step1 data truncated mid-function..." 拒绝编造
```

**核心问题**：`_agent_event_handler` **没调用 `_resolve_step_variables`**。

## 修复

`partner/mind/harness.py` 加新 helper + 在 `_agent_event_handler` 注入:

### Helper: _normalize_step_aliases

```python
def _normalize_step_aliases(text: str) -> str:
    """Normalise ${step1} / {{step1}} aliases to $step_1 form."""
    if not isinstance(text, str) or ("$" not in text and "{{" not in text):
        return text
    import re as _re
    def _strip_step_prefix(name: str) -> str:
        n = str(name or "").strip()
        if n.startswith("step_"):
            return n[len("step_"):]
        if n.startswith("step"):
            return n[len("step"):]
        return n
    def _make_alias(name: str, tail: str | None) -> str:
        clean = _strip_step_prefix(name)
        suffix = f".result.{tail}" if tail else ".result.content"
        return f"$step_{clean}{suffix}"
    text = _re.sub(
        r"\\$\\{([A-Za-z0-9_-]+)(?:\\.result\\.([A-Za-z0-9_.-]+))?\\}",
        lambda m: _make_alias(m.group(1), m.group(2)),
        text,
    )
    text = _re.sub(
        r"\\{\\{\\s*([A-Za-z0-9_-]+)(?:\\.result\\.([A-Za-z0-9_.-]+))?\\s*\\}\\}",
        lambda m: _make_alias(m.group(1), m.group(2)),
        text,
    )
    return text
```

### _agent_event_handler 注入

```python
# 在 task 拼接前:
task = _normalize_step_aliases(task)
if ctx.task_instance:
    task = _resolve_step_variables(task, ctx.task_instance)

# 在 supplied_context 拼接前:
for key, value in supplied_context.items():
    try:
        if isinstance(value, str):
            resolved_value = _normalize_step_aliases(value)
            if ctx.task_instance:
                resolved_value = _resolve_step_variables(resolved_value, ctx.task_instance)
            normalised_context[key] = resolved_value
        else:
            normalised_context[key] = value
    except Exception:
        normalised_context[key] = value
```

## 验证

### 单元测试
`tests/test_governance.py::test_normalize_step_aliases_handles_braces_form`:
- `${step1.result.content}` → `$step_1.result.content`
- `{{step1.result.content}}` → `$step_1.result.content` (Jinja)
- `${step2.result.json}` → `$step_2.result.json`
- `${step3}` → `$step_3.result.content` (default tail)
- 非 step 引用保持不变

### 端到端验证（03 + 05 第九轮）
- ✅ **03 finding_report.md 真发到 QQ**：3 对 verbatim source_path + evidence_quote——引用自 step1 真读的 harness.py
- ✅ **05 cross_instance_review_v9.md 真发到 QQ**：3 对 verbatim evidence_quote（来自真读 Aether/SESA/CytoBridge） + 真 evaluate metrics (pairs=10, baseline=0.538 vs candidate=0.9)
- ✅ LLM 不再"上游事实无法被验证"拒绝——能 verbatim 抽 step1 真读内容

### 全量回归
**351 passed in 12.18s**（333 + 4 Bug #38 + 1 Bug #39 + 1 Bug #40 + 3 Bug #43 + 1 Bug #44 + 1 Bug #45 + 1 Bug #47 + 1 Bug #48 + 2 Bug #50 preflight + 2 Bug #50 execute + 1 Bug #44 normalize = 351，0 回归）

## 后果

- LLM 跨步引用 `${step_id.result.content}` 不再是字面字符串——真拿到上游 content
- LLM 能 verbatim 抽 evidence_quote 做真双行引用
- generate_text 不再因"上游事实无法被验证"而拒绝写报告
- 03 + 05 自主度显著提升（03 第九轮直接 verbatim 抽 step1 真读 evidence_quote 端到端 verified）

## 与之前 ADR 的关系

- **ADR 0014**（Bug #44 initial fix）：改了 batch_planner 接受 braces 形式，但只在 preflight
- **ADR 0019**（本次）：修了 `_agent_event_handler` 在 execute 路径也调用 `_resolve_step_variables` + `_normalize_step_aliases`

两个 ADR 共同保证 `${step_id.result.content}` 引用在两端都工作。
