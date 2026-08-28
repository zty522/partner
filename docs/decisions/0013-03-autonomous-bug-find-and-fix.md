# ADR 0013：03 自主找 bug + fix 的能力评估（实战总结）

**状态**: accepted
**日期**: 2026-08-27
**触发**: 任务 B（自主找 bug）+ 任务 D（真应用修复）

## 背景

2026-08-27 推进 03 实例从"被动执行"到"自主找 bug + 改代码"的能力测试。任务 B 让 03
自主读 batch_planner.py 找真实 bug，任务 D 让 03 真应用修复。

## 任务 B 自主找 bug 真实结果

03 LLM 找到3 个 bug：

| # | Bug 描述 | 实际准确性 |
|---|---|---|
| Bug 1 | truth_quote_required 判定条件不一致（contract vs validator 行为分裂） | ✅ **真**。line 170-174 行 regex 太严。Hermes 已真修（新增 source_path/source证据/真值引用/truth_quote/truth_audit/真值/原文/原话 等关键词）。|
| Bug 2 | truth_extract_ids 永远为空集合（死代码）| ❌ **假**。line 611/654/679 处有 truth_extract_ids.add() 调用。03 LLM 漏看了几行。 |
| Bug 3 | allowed_read_roots 包含 files/outgoing 但 contract 未声明 | ❌ **假**。line 184 已包含 files/outgoing。03 LLM 漏看了一行。 |

**准确率 1/3**——03 找到 1 个真 bug，但有 2 个误判。**这是重要的诚实发现**：

- 03 LLM **不能完全独立判断**——会漏看代码（truth_extract_ids 有 add() 但 03 没找到）
- 03 LLM **没有交叉检查**——错误地把 `for root in allowed_read_roots` 列表里的"files/outgoing"重复算成 contract 缺失

**核心教训**：03 LLM "自主找 bug" 需要**外部交叉验证**——不能作为最终诊断。

## 任务 D 真应用修复真实结果

3 次 attempt 都没真应用 patch：

| Attempt | 失败原因 |
|---|---|
| v1 | LLM 拒绝编造（拒绝重写 batch_planner.py 完整内容，因 source 不在 step context）|
| v2 | LLM 写了"诚实声明 + 部分 patched 内容"——partner framework 把 step2 content (含 markdown) 当 Python 写文件，line 3 indent 错 |
| v3 | LLM 走 shell_run 用精确字符串替换——**但 old block 在 batch_planner.py 里找不到**（因为 Bug 1 修复之前已被 patch）|

**失败根因（不是 framework bug，是设计选择）**：

- partner framework 的 `${step_id.result.field}` 引用机制在 generate_text 路径**没把上游 content 完整传给 LLM task prompt**——LLM 只拿到元数据
- LLM 拒绝编造（合规事实边界）——不能重写它没完整看到的 96514 B 文件
- create_file 把 LLM step2 output（含 markdown 解释）当 Python 写——partner framework 没有"检测 LLM output 是否真的是有效 Python"的清理层

**D 阶段实际结果**：03 没能自主改 partner 代码——**这是 framework 限制**——不是 03 LLM 能力问题。

## 修复

任务 B 找到的 Bug 1 **由 Hermes 真修**（不是我之前以为的失败，是 patch 真的成功应用了）。

`partner/planner/batch_planner.py:170-178`：

```python
# 之前（Codex 8/27）：
truth_quote_required = bool(
    truth_policy_active
    and re.search(r"evidence_quote|逐字(?:连续)?(?:摘录|引文|引用)", str(user_message or ""), re.I)
)

# 现在（Bug 1 修复）：
truth_quote_required = bool(
    truth_policy_active
    and re.search(
        r"evidence_quote|逐字(?:连续)?(?:摘录|引文|引用)|source_path|source证据|真值引用|真值审计|truth_quote|truth_audit|真值|逐字|原文|原话",
        str(user_message or ""),
        re.I,
    )
)
```

修复后全量回归 `342 passed in 21.26s`（0 回归）。

## 03 实际能力评估

按 AGENTS.md "Non-negotiable completion rules"：

| 能力 | 评估 |
|---|---|
| 读 partner 代码（atomic_inspect_file 96514 B）| ✅ |
| 自主识别 bug（truth_quote_required）| ✅ |
| 误判 bug 2 + bug 3 | ⚠️ LLM 漏看代码行 |
| 自主应用 patch（用 create_file / shell_run）| ❌ framework 限制（`${step_id.result.field}` data flow 失效）|
| 加 pytest | ⚠️ LLM 尝试了但因框架限制没真生成 |
| push_files 推 pytest_verification_log | ⚠️ task 因 framework failed 没 push |

**03 真实自主度**：约 50%——能找 bug 但**不能独立完整改 bug**。需要：
1. **外部交叉验证 bug 判断**（人工或其他 LLM）
2. **framework data flow 修复**（`${step_id.result.field}` 在 generate_text 路径上传递完整内容）

## 后续建议

**Bug #45 candidate**（partner framework `${step_id.result.field}` 在 generate_text 路径不传递完整内容）：

- 出现在 step prompt 上下文缺少 step_id.result.field 的实际值
- LLM 只能凭"上游描述"操作——不能引用真实 data
- 修复方向：在 `_agent_event_handler` 的 task prompt 拼接阶段，把 `data` 字段（即上游 step 的 result.content）注入 task prompt 上下文

## 不重做

- 未改 partner framework 的 generate_text handler 让 LLM 看到完整 step_id.result.content
- 未扩大 03 的 path 白名单
- 未在 planner prompt 加"不要自动生成完整文件"约束——LLM 拒绝编造已经够强
