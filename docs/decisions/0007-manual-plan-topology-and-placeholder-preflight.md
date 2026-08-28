# ADR 0007：manual_stable 三步拓扑与占位判定双向收紧

**状态**: accepted
**日期**: 2026-08-27
**触发**: Bug #38 — 03 任务 1/3 只读诊断 plan 被 preflight 拒绝 3 次

## 背景

2026-08-27 切换到 03 实例后，向 03 注入任务"只读 partner/mind/harness.py
中 `_is_placeholder_content` 函数（行号 3147 附近），不要修改任何代码"。

03 在 8 分 44 秒内连续被 `manual_plan_preflight` 拒绝 3 次，每次错误相同：

```
manual plan preflight failed:
  step4_create_report: evidence-dependent output must reference a dependency result,
                       not embed a static template;
  step4_create_report: output content contains an unfilled template
```

具体事件链（`/mnt/e/work/partner_workspace/instances/03/state/tasks/458b5e1c-.../task_log.jsonl`）：

```
15:16:51 planner_experiment_intervention
15:16:51 robust_execute_start
15:19:03 robust_execute_success
15:19:03 manual_plan_preflight_failed   ← 第 1 次
15:22:07 robust_execute_timeout          ← 180s 超时
15:22:07 manual_plan_preflight_failed   ← 第 2 次
15:25:11 robust_execute_timeout
15:25:11 manual_plan_preflight_failed   ← 第 3 次
15:25:11 batch_plan_handler_failed
```

03 收到指令后，planner 自动生成了"读 harness.py → 写 markdown 诊断报告"的
两步 plan，并用 `create_file` 内嵌静态模板字符串（典型"Output product 1"，
但长度足够绕过短占位判定），被 preflight 第 862-878 行的硬门拒绝。

## 根因诊断

定位到 `partner/planner/batch_planner.py:121` `_manual_preflight_plan`
函数内第 862-878 行：

```python
if raw_path.lower().endswith((".md", ".py")) and content and not content.startswith("$") and len(content) < 100:
    issues.append(f"{step.id}: output content is a short placeholder")
if dependency_ids and content and not content.startswith("$"):
    issues.append(
        f"{step.id}: evidence-dependent output must reference a dependency result, "
        "not embed a static template"
    )
```

第 864-868 行的 `evidence-dependent output` 判定**对所有不含 `$` 引用
的 content 全部拒绝**，没有把"合法的真实分析报告"和"嵌入静态模板"区分开。

Codex 8/27 在 batch_planner.py 里的 +98 行改动（`candidate_contract`）
已经给 candidate 臂（路径隔离 canary 任务）加了"必须使用 read → generate_text
→ writer 三步拓扑"的硬约束，**但只对带 `[strategy_id=candidate_preflight_contract_v2]`
marker 的任务有效**。普通 manual_stable 任务（无 marker）不会注入这条
prompt，planner 仍按旧习惯生成"read + create_file(static)"的 plan。

## 决策

采用两条互补的修复，都集中在 `_manual_preflight_plan` 与
`_manual_environment_contract` 函数内：

### 修复 1: 普通任务 prompt 加同款三步拓扑约束

`_manual_environment_contract` 函数返回的 prompt 末尾追加：

```
- [manual_stable 通用] 当任务是"读一个或多个文件并生成 Markdown/TXT 报告"时，
  必须使用 read → generate_text → writer 三步拓扑：
    1) atomic_inspect_file 读取每个真实输入文件；
    2) generate_text 把 read 的 result.content 整理为完整报告正文；
    3) atomic_write_artifact / create_file 写 writer，content 必须是
       ${generate_text_step_id}.result.content 引用，**不能**内联静态字符串。
  不要用 create_file/atomic_write_artifact 直接写分析报告正文。
  例外：当用户消息明确只读（包含"只读"、"诊断"、"不修改"、"不写文件"等关键词），
  可以只规划 atomic_inspect_file / atomic_list_project_files，不写任何 writer。
```

这条规则对非 candidate 的 manual_stable 任务同样生效——不依赖
experiment marker。Codex 8/27 的 candidate_contract 修复了 candidate 臂的
同类问题；这里把同款修复扩到所有 manual 任务。

### 修复 2: preflight 占位判定双向收紧

第 862-868 行的 `short placeholder` 与 `evidence-dependent output` 判定
改为"短内容或含占位关键词 = 拒绝；长内容 + 非占位 = 放行"：

```python
placeholder_pattern = re.compile(
    r"(?:待补充|output product|placeholder|在此填写|TODO|由.+步骤填入|<来自|<同>)",
    re.I,
)
content_is_placeholder = bool(
    content
    and not content.startswith("$")
    and (
        len(content) < 100
        or placeholder_pattern.search(content)
    )
)
if raw_path.lower().endswith((".md", ".py")) and content_is_placeholder:
    issues.append(f"{step.id}: output content is a short placeholder")
if dependency_ids and content and not content.startswith("$") and content_is_placeholder:
    issues.append(
        f"{step.id}: evidence-dependent output must reference a dependency result, "
        "not embed a static template"
    )
```

语义变化：
- **仍然拒绝**：① content 长度 < 100；② content 含已知占位关键词
  （"待补充"、"output product"、"placeholder" 等 8 个）
- **新放行**：content ≥ 100 字 且不含占位关键词（合法的真实分析报告）
- **保留**：content 以 `$` 开头（指向依赖结果的引用）始终放行

第 870-878 行的 `unfilled template` 判定（empty_fields ≥ 2 或占位关键词）
保留原行为——这部分已经覆盖了"看起来像模板但长度足够的伪内容"。

## 后果

1. **Planner 行为变化**：所有 manual_stable 任务在收到"读+写报告"指令时，
   应当自动选择三步拓扑；不再依赖 candidate marker。
2. **preflight 行为变化**：长内容（≥100 字）+ 非占位的真实分析报告
   不再被无理由拒绝；短内容 / 含占位关键词的伪内容仍被拒绝。
3. **回归测试**：
   - `tests/test_manual_stable_mode.py::test_manual_stable_environment_contract_requires_three_step_topology_for_unmarked_tasks`
   - `tests/test_manual_stable_mode.py::test_manual_preflight_allows_long_substantive_content_without_dependency_reference`
   - `tests/test_manual_stable_mode.py::test_manual_preflight_still_rejects_short_placeholder_content`
   - `tests/test_manual_stable_mode.py::test_manual_preflight_allows_zero_write_plan_for_read_only_user_message`
4. **全量回归**：337 passed in 12.16s（333 基线 + 4 新增）。
5. **未变更边界**：candidate 臂的 `candidate_contract` 不受影响，路径隔离
   canary 实验的 _机制 不变。

## 不重做

- 不改 Codex 8/27 在 batch_planner.py 加的 candidate-only deterministic
  binding（第 235-282 行）；那条是路径隔离实验的专门约束，作用域正确。
- 不动 preflight 第 870-878 行的 `unfilled template` 判定——empty_fields
  ≥ 2 这个 heuristic 已经能捕获多数伪模板情况，无需重新设计。
- 不改 harness.py / executor.py——根因在 planner prompt + preflight 阈值，
  不在执行器或 harness。