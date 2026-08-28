# ADR 0020：Bug #55 — push_files 自动从上游 step result 落盘

**状态**: accepted
**日期**: 2026-08-28
**触发**: 03 / 05 第十二轮+第十三轮 `source not found` 失败

## 背景

Partner LLM 在生成 plan 时，频繁用 3 步链路：

```
atomic_inspect_file → generate_text → push_files
```

**问题**：generate_text 只生成文本不落盘；push_files 必须有 source file path 才能推——**LLM 必须加中间 create_file 步骤**才能让 push_files 找到文件。如果 LLM 忘了这一步，task 失败。

03 / 05 第十二轮、第十三轮连续 4 次"source not found: ...finding_report.md"。

## 根因

`partner/v2/push_events.py::atomic_push_files` 第 70 行：

```python
source, provenance = _resolve_source(ctx, str(params.get("source") or ""))
...
if not os.path.exists(source):
    return {"ok": False, ..., "error": f"source not found: {source}"}
```

**只检查文件存在性，不接受 inline content**。Partner framework 必须让 LLM 手动加 create_file 中间步。

## 修复（两层）

### Layer 1: atomic_push_files 接受 inline content + filename

`partner/v2/push_events.py` 修改 `_resolve_source` 之后、os.path.exists 检查之前：

```python
inline_content = str(params.get("content") or "")
inline_filename = str(params.get("filename") or "")
if not source:
    if inline_content and inline_filename:
        workdir = _working_dir(ctx)
        if workdir and os.path.isdir(workdir):
            materialised = os.path.join(workdir, inline_filename)
            with open(materialised, "w", encoding="utf-8") as f:
                f.write(inline_content)
            source = materialised
            provenance = "inline_content_materialised"
    if not source:
        return {"ok": False, ..., "error": provenance}
```

### Layer 2: harness 在 push_files / atomic_push_files 时自动从上游 step 落盘

`partner/mind/harness.py:1494` 修改匹配条件 + 加 materialise 逻辑：

```python
if isinstance(params, dict) and step.event_type in {"atomic_push_files", "push_files"}:
    source = params.get("source")
    if source is None or ...:  # source empty fallback
        ...
    else:
        # Bug #55: 当 source 文件不存在但 step 有 depends_on，自动从
        # 上游 step (generate_text / execute_code) 拉 content 落盘。
        workdir = ...
        candidate = ...
        if candidate and not os.path.exists(candidate):
            for upstream_id in (step.depends_on or []):
                result_file = os.path.join(workdir, f"_step_{upstream_id}.result.json")
                if not os.path.exists(result_file): continue
                try:
                    with open(result_file, "r") as f:
                        rj = json.load(f)
                    content = (rj.get("result") or {}).get("content") or ""
                    if content and len(content) >= 100:
                        upstream_content = content
                        break
                except Exception: continue
            if upstream_content:
                basename = os.path.basename(source) or "report.md"
                materialised = os.path.join(workdir, basename)
                with open(materialised, "w") as f:
                    f.write(upstream_content)
                params = dict(params)
                params["source"] = basename
                ctx.task_instance.append_log("push_source_materialised_from_upstream", {...})
```

**关键**：把匹配从 `"atomic_push_files"` 扩到 `{"atomic_push_files", "push_files"}` ——partner LLM 生成 plan 时 event_type 是 `push_files`（无 atomic_ 前缀）。

## 验证

### 03 第十四轮 + 05 第十四轮（Bug #55 fix verified）

**03 第十四轮**：
- 3 步极简：read batch_planner.py → generate_text → push_files
- framework 从 step2.result.content 自动落盘成 `finding_report.md`
- ✅ `send_file_proactive result=True file=finding_report.md`
- **finding_report.md（1790 B）真发到 QQ**——8 对 verbatim source_path + evidence_quote 双行引用

**05 第十四轮**：
- 3 步极简：读 04 holdout aether → generate_text RecommendationRecord → push_files
- ✅ `✅ 3/3 create_file` + push 真发
- finding_report.md 包含 ≥3 对 verbatim source_path + evidence_quote（Aether README 引用）
- 给出 Partner v2 "外部代码仓评估适配层" 建议（external_repo_layout, eval_output_dir, entrypoint_cli, runtime_baseline 四个声明字段）

### 全量回归

**351 passed in 20.00s**（0 回归）

## 后果

- LLM 不再必须加 create_file 中间步——3 步链路直接 verified
- 即使 LLM 没显式传 content，framework 也自动从上游 step 拉 content 落盘
- push_files source empty 时 fallback 到当前 task directory auto-discover（之前已有逻辑）
- push_files source 非空但文件不存在时，新增"自动从上游 step 拉 content materialise"路径

## 与 ADR 0017 / 0018 / 0019 的关系

- **ADR 0017 + 0018**：atomic_inspect_file 接受 paths= list alias
- **ADR 0019**：generate_text prompt 注入上游 step content（data flow）
- **ADR 0020**（本次）：push_files 自动从上游 step 拉 content 落盘

**共同保证 3 步链路 `read → generate_text → push_files` 在两端都工作**——LLM 不必记中间 create_file 步骤。
