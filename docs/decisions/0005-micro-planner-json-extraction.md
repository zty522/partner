# ADR 0005：micro planner 输出提取统一入口与 caller-side retry

**状态**: accepted，2026-08-25 amended  
**日期**: 2026-08-25  
**触发**: Bug #36 — 手动阶段 1 第 1-9 轮失败 + 实机验证修复

## 背景

2026-08-25 manual_stable canary 期间，03 实例收到用户消息后多次在
`partner/mind/harness.py:589-592` 或 `partner/planner/batch_planner.py:389`
抛出 ValueError：

```
Batch planner returned invalid JSON [type=ValueError, pos=unknown]:
micro planner output must be a JSON array or {plan: []}
```

经过 9 轮手动注入测试，识别出**三个不同根因** + **一个能力边界**：

### Bug #36 phase 1（已修）：LLM 输出格式问题

**根因**：LLM（实际是 MiniMax-M3，task_log metadata 显示 deepseek-v4-flash
是 batch_planner.py:234 的 fallback 字符串未覆盖）在长 prompt 下输出格式多样：

1. **裸 step 列表**：`[{"id":...,"event_type":...}]` 而非 `{"plan":[...]}`
2. **多 JSON 块 + 末尾空 list**：`depends_on:[]` 抢占 raw_decode candidate
3. **thinking-only**：`<think>...</think>

任务内容为空。` 没有 JSON

旧 prompt 仅说"不要输出解释，只输出 JSON 对象"是软指令，不足以约束。

**修复** (`partner/mind/harness.py`)：
1. `_json_from_llm` 入口 `<JSON_OUTPUT>...</JSON_OUTPUT>` 标签提取
2. raw_decode candidates 末尾新增 bare step list auto-wrap（取**第一个** list-of-dicts）
3. Retryable hint：thinking-only 输出标记为 retryable ValueError

### Bug #36 phase 2（撤回）：错误地方加 retry

**初始误判**：在 `partner/mind/harness.py:MicroPlanner.plan()` 加 caller-side
retry loop（最多 3 次）。

**撤回原因**：production 实际走的是 `partner/planner/batch_planner.py:BatchPlanner`，
不是 `MicroPlanner`。`partner/mind/executor.py:6574` 显式 `BatchPlanner.from_workspace`。
修 MicroPlanner 不影响 production。已撤回。

### Bug #36 phase 3（已修）：BatchPlanner retry budget + 强化 retry prompts

**根因**：`partner/planner/batch_planner.py:341` 中 manual_stable mode
`_max_retries = 1`，不够覆盖 LLM ~50% thinking-only 失败率。

**修复** (`partner/planner/batch_planner.py`)：
1. `_max_retries = 3 if _manual_mode else ...`，manual_stable retry 1 → 3
2. Ultra-short retry prompt 改为 `<JSON_OUTPUT>...</JSON_OUTPUT>` 显式标签包裹

### Bug #36 phase 4（撤回）：content 字段硬约束

**初始设计**：在 manual_stable prompt 加"atomic_write_artifact 的 content 必须
≥200 字符真实正文或拆两步法"。

**撤回原因**：第 9 轮实机中，LLM 接受约束后**重新规划任务**，使用 03 不持有的
`analyze` / `check_quality` endpoint → step 3 失败 → 后续依赖跳过。
**这是任务设计与 03 实例能力错配，不是 prompt 能修**。已撤回。

## 决定

保留 **phase 1**（harness.py JSON 提取）+ **phase 3**（batch_planner.py retry + 强化 prompt）。
撤回 **phase 2**（修错地方）+ **phase 4**（导致 endpoint 错配）。

## 修复成效（实机验证）

| 阶段 | 轮次 | micro planner 状态 |
|------|------|-------------------|
| Bug #36 phase 1+3 前 | 1, 2, 4, 6 | 4/4 失败 |
| Bug #36 phase 1+3 后 | 7, 8 | 2/2 成功 |

**结论（当时样本）**：修复后连续 2/2 次成功，只能证明故障路径得到改善，不能外推为稳定
成功率 100%。后续 04/05 仍出现 invalid JSON，说明 retry 次数不是完整根因。

## 不修的事

1. **LLM content placeholder 行为**：第 8 轮 LLM 给出 "Output product 1" 这种
   短句占位 content，触发 partner 框架 `_is_placeholder_content`（len < 200
   for .md/.py）。这是 MiniMax-M3 LLM 行为 + 03 实例"无上下文写文档"的能力
   错配问题，不是 framework bug。
2. **03 实例项目主线（brief/canary.md/stub.py）**：9 轮全部失败，根因是
   03 = "Partner 框架与前端"角色不适合凭空写项目文档。需要 03 先读 partner
   代码 → 定位真实代码问题 → 写 patch + pytest，才是 03 的真实工作。
3. **MiniMax-M3 thinking mode 行为**：LLM 在 ~50% 长 prompt 第一次调用时
   只输出 reasoning 不输出 JSON。修 prompt 只能提概率到 ~100%，但 LLM
   行为本身不是 partner 框架能改的。

## 测试

- `tests/test_micro_planner_extraction.py`: 14 个测试覆盖 phase 1 + 3
- 当时全量 pytest 227 passed / 0 failed；后续修订基线见 `docs/testing/last_pytest.txt`
- 实机 9 轮追踪保留在 `instances/03/state/event_pipeline.jsonl` 和
  `qq_chat_history.jsonl`

## 相关文档

- `docs/change_log.md`（2026-08-25 第 4-9 轮完整记录）
- `docs/current_status.md`（第 6 节下一阶段优先级）
- `docs/testing/last_pytest.txt`（当前 baseline）
- `tests/test_micro_planner_extraction.py`

## 2026-08-25 修订：从“提高 retry”转为执行前语义门

后续 04/05 实跑发现，完整 JSON 只是第一层：模型仍会编造输入文件、输出不支持的 event、生成
空模板、使用错误步骤引用，或让完整 plan 被参数内嵌套 JSON 抢占。继续增加 retry 会增加成本，
不会修复语义错误。因此补充以下决定：

1. `_json_from_llm` 先解析完整响应并优先顶层 `plan/steps`，不能让嵌套 `{path, content}` 抢占。
2. `manual_stable` 在执行前做 event、依赖、真实输入路径、输出目录、引用和模板语义预检；只做
   两次带明确错误的定向修复，之后 fail closed。
3. 读写权限分离。只读白名单包含源码、文档、外部资料和 governed evidence；写仍只进 task 目录。
4. `extract` 必须保留步骤 `data`，使用完整外层 JSON 严格解析，并验证逐字引文及命名源归属；
   截断的外层 JSON 即使包含可解析的内层对象也必须失败。
5. `manual_stable` 写后不触发 strict reflect，避免一次手动任务被暗中改造成自治链。

实跑结果：04 的第三次来源核对生成 3,728 B Markdown，6/6 引文逐源匹配；05 从 immutable
evidence bundle 的首版 candidate 因回滚定义错误被拒；修订版生成 3,741 B 报告，保持
`promotion=false` 且不恢复已知坏路径。当前全量回归 252 passed。
