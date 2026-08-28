# ADR 0010：generate_text 路由到 report purpose（无工具 + 单轮）

**状态**: accepted
**日期**: 2026-08-27
**触发**: Bug #41 — 04 holdout 1/5 输出 tool_call JSON envelope

## 背景

2026-08-27 推进 04 holdout 1/5 (Aether 项目 holdout 报告) 时，LLM 通过 `generate_text` 事件生成 holdout 报告内容，但
**输出格式是 OpenAI-style tool_call JSON envelope**:

```json
```json
{
  "name": "write_file",
  "arguments": {
    "path": "holdout_report.md",
    "content": "# Holdout 报告：Aether 与 Partner 拓扑契约差异分析\n..."
  }
}
```
```

这个 JSON envelope 被 `compose_holdout_report` step 的 `result.content` 接收，作为
下游 `create_file` step 的 `${step.result.content}` 引用，最终写入
`holdout_aether_world_model.md` 文件。文件里的真实内容是正确的 holdout 报告（3 个技术点、2 个
结构性差异、对 Partner 的建议、两个 source_path+evidence_quote 双行），但**被包装成 tool_call
envelope**——读起来像"我打算写这个内容"而不是"这是实际内容"。

## 根因

`partner/adapters/adapter.py` 第 1023-1026 行：

```python
elif purpose == "action":
    # action event 也需要真实工具，但不能继承长期 project 的超长超时。
    cmd.extend(["-t", self._resolve_tools_for_purpose("action"), "--ignore-rules"])
```

`generate_text` event 在 partner harness 的 `_agent_event_handler`（`partner/mind/harness.py:4922`）
走 `purpose="action"` 路径——**`action` purpose 默认给 LLM `terminal,file,web` 工具**。LLM
看到这些工具后误以为应该"用 file 工具写文件"，于是输出 `write_file` tool_call 形式，partner
framework 的 `_clean_text` 没检测这种 envelope（只检测 ```markdown|md|text 围栏），结果整个
tool_call JSON 原样写到 .md 文件。

对比 `_action_think` 和 `report` purpose（adapter.py 1027-1032）：

```python
elif purpose == "action_think":
    cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
elif purpose == "report":
    cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
```

这两个 purpose 用 `-t ""`（无工具） + `--max-turns 1`（单轮）——LLM 必须直接返回纯文本。

## 修复

### Step 1: `partner/skills/external_agent_skills.py` 加 `purpose` 参数

让 `execute_agent_task` 接受显式 `purpose` 参数透传到 `_call_general_agent`：

```python
async def _call_general_agent(
    agent: str, workspace: str, task: str, task_instance: Any,
    allow_web: bool, agent_params: dict | None = None,
    *, purpose: str = "action",   # ← 新增
) -> SkillResult:
    ...
    reply = adapter.chat(prompt, purpose=purpose)
```

`execute_agent_task` 内部从 `agent_params.get("purpose", "action")` 提取 purpose 透传。

### Step 2: `partner/mind/harness.py` 路由纯文本事件到 `report` purpose

```python
# Hermes 2026-08-27 fix (Bug #41)
_PURE_TEXT_EVENTS = {"generate_text", "write_report", "summarize", "analyze"}
_resolved_purpose = "report" if event_name in _PURE_TEXT_EVENTS else "action"
agent_params.setdefault("purpose", _resolved_purpose)
```

`_PURE_TEXT_EVENTS` 集合列出**所有"生成纯文本"语义的事件**——它们不需要 `terminal,file,web` 工具，
只需要让 LLM 输出文本。`report` purpose 已有 `-t "" --max-turns 1` 配置。

其他 action 事件（`web_search`、`query_api`、`download_file`、`web_fetch`、`create_diagram` 等）
保持默认 `action` purpose + 完整工具链。

## 验证

**04 holdout 1/5 重跑**（重启 04 加载新代码后）：

| step | event_type | content 类型 | 字节数 |
|---|---|---|---|
| read_aether_root_readme | atomic_inspect_file | ✅ plain markdown | 6695 |
| read_aether_eval_readme | atomic_inspect_file | ✅ plain markdown | 678 |
| compose_holdout_report | **generate_text** | ✅ **plain markdown** | **1179** |
| compose_holdout_report_truth_extract | extract | plain JSON | 5706 |
| write_holdout_report | create_file | 写入 holdout_aether_b41.md (1489 B) | - |
| push_report_to_qq | push_files | ❌ 30s TimeoutError (Bug #42 candidate) | - |

**`compose_holdout_report` 的 `result.content` 是纯 markdown**：

```
Holdout 报告 - Bug #41 修复验证
Aether 技术点(3 项):
1. 构建: Python 3.10 + conda, pip install -r requirements.txt...
2. CLI: scripts/demo.py 单入口三模式 --task reconstruction|prediction|planning 分发...
3. Evaluation: video_depth/run_aether.sh 与 rel_pose/run_aether.sh 双 shell 驱动...
v2 拓扑差异(2 项):
- A: 本 holdout 强制 source_path+evidence_quote 相邻双行真值...
- B: 本报告字节硬约束 1000-1500, 与 v2 禁止 candidate 内联静态字符串互补...
Partner 建议: 在 candidate_preflight_contract_v2 中将 source_path/evidence_quote 相邻双行校验设为必经 preflight 节点...
source_path: /mnt/e/work/partner_workspace/external/code/Aether/README.md
evidence_quote: Aether addresses a fundamental challenge in AI: integrating geometric reconstruction with generative modeling
source_path: /mnt/e/work/partner_workspace/external/code/Aether/evaluation/README.md
evidence_quote: Our evaluation setups mainly follow [CUT3R](https://github.com/CUT3R/CUT3R)...
```

不再是 `\`\`\`json\n{name: write_file, ...}\n\`\`\`` envelope——**Bug #41 修复 verified**。

## 全量回归

`339 passed in 12.99s`（333 + 4 Bug #38 + 1 Bug #39 + 1 Bug #40 = 339，0 回归）

## Bug #42 candidate（未修）

`qq_official_bridge.py:1383 send_file_proactive` 在 04 holdout 1/5 重跑时 30 秒 TimeoutError：

```
result = future.result(timeout=30)
→ raise TimeoutError()
```

`_bot.send_file` 协程 30 秒没返回。root cause 未深挖（不在本 ADR 范围）。可能是
qq_official_bot.py 的 send_file 实现有 bug 或网络限流。**Bug #42 候选修法**：
- 把 timeout 从 30s 加到 120s
- 加 retry 机制
- 或绕过 send_file 用 direct HTTP API

## 不重做

- 未改 `_clean_text` 函数本身（保留 ```markdown|md|text 围栏剥离逻辑）
- 未改 adapter.py `action` purpose 默认行为（其他 action 事件仍能调工具）
- 未把 `_PURE_TEXT_EVENTS` 做成可配置（保留硬编码，简单）
- 未在 harness_core 层加 prefilter（修在最贴近 root cause 的层）
- 未为 Bug #42 写代码（本 ADR 不涵盖 push_files 协程超时问题）
