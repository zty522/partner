# ADR 0063：batch_planner 必须区分上游 adapter/网络错误与不可用 sentinel

- 状态：Accepted / Production Active
- 日期：2026-09-05
- 前置：ADR 0005（micro-planner JSON 抽取）、ADR 0060（instance_native production runtime）
- 触发：03 实例 19:16 ~ 20:08 连续 10 次 task_failed，`event_pipeline.jsonl` seq=333→342
- 关键文件：`partner/planner/batch_planner.py`、`tests/test_batch_planner_adapter_errors.py`

## 背景与动机

2026-09-05 用户观察到 03 实例 QQ 端持续刷"收到指令【03实例自动续跑】...正在思考...⏹️ 已停止..."消息。原始 report 把问题表述为"03 一直发 QQ 是不是脚本在持续灌"。

实际追查 `instances/03/state/event_pipeline.jsonl` 后看到的事实是：

```
{"event_id":"ev_43d15069","type":"user_message","sender_id":"partner_03_self_drive",
 "text":"【03实例自动续跑】项目：03实例原生项目续跑_项目_Partner 框架与前端优化..."}
{"event_id":"ev_b8bfdd53","type":"task_failed","msg":"❌ Batch planner returned invalid JSON
 [type=ValueError, pos=unknown]: n"}
```

10 次循环，每次 sender_id 都是 `partner_03_self_drive`（自驱注入），每次失败信息都长一样：

```
Batch planner returned invalid JSON [type=ValueError, pos=unknown]: n
```

**`pos=unknown` 这个细节是破案关键**。如果是真正的 JSON parse 失败，`pos` 应该是数字（0/1）；`pos=unknown` 说明异常是 `ValueError` 而不是 `json.JSONDecodeError`。ValueError 来自 `_json_from_llm` 中"thinking-only 输出"那条 raise：

```python
raise ValueError(
    "JSON parse failed before extraction. "
    "RETRYABLE: LLM emitted thinking block but no JSON payload"
)
```

而 retry 循环 3 次全失败后，行 2224 抛 RuntimeError：

```python
raise RuntimeError(f"Batch planner returned invalid JSON [type={type(exc).__name__}, pos={getattr(exc, 'pos', 'unknown')}]: {exc}") from exc
```

这里的 `exc` 是外层 except 块捕获的那个 ValueError，`pos` 属性不存在所以显示 'unknown'。但 raise 消息模板还说"invalid JSON"——**这个错误消息本身就在撒谎**，因为原始问题根本不是 JSON。

进一步追 `instances/03/state/logs/agent_runs.jsonl`（Hermes adapter 的真实 stdout_preview）：

```json
{"backend":"hermes","purpose":"batch_plan","status":"ok","elapsed_ms":12851,
 "stdout_preview":"API call failed after 3 retries: HTTP 429: 已达到 Token Plan 用量上限：
 请升级 Token Plan 套餐或购买积分补充用量。 (2056)"}
```

**真相**：Hermes token plan 配额耗尽，所有 batch_plan 调用都收到 HTTP 429 错误体。robust executor 把这个错误字符串当成正常 `result.value` 返回给 batch_planner。batch_planner 没识别它是 HTTP 错误体，喂给 `_json_from_llm`，后者找不到 `<think>` 块但 JSON parse 又失败，最后 ValueError → RuntimeError("invalid JSON")。每 4 分钟 self-drive tick 再注入一次，循环 10 次。

## 根因（两层）

### 根因 1：batch_planner 缺乏"上游 adapter 错误体"识别

`partner/planner/batch_planner.py` 中只有 `_is_unavailable_sentinel` 检测 2 个固定字符串：

```python
def _is_unavailable_sentinel(text: str) -> bool:
    raw = str(text or "").strip()
    return any(token in raw for token in (
        "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE",
        "Error: agent backend not available",
    ))
```

而 robust executor 的 result.value 可能是任何东西——HTTP 错误体、网络异常字符串、API quota 提示。这些都应该走"fail-fast / 不重试 JSON parse / 暴露真实错误"路径，而不是被当作"LLM 返回值"喂给 `_json_from_llm`。

### 根因 2（外部）：Hermes token 配额耗尽

这是事故的外因，不在 framework 代码范围。03/04/05 三个实例的所有 LLM 调用（batch_plan / approval_classify / intent_classify）在 2026-09-05 19:00 后全部 HTTP 429。修复需要买/续配额，不在本 ADR 范围。**但 ADR 必须诚实记录：framework 错误地掩盖了这个外因**——这是本 ADR 要修的内因。

## 决策

### 1. 新增 `_is_adapter_error_text` 检测器

在 `batch_planner.py` `_is_unavailable_sentinel` 旁边加：

```python
_ADAPTER_ERROR_TOKENS = (
    "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
    "API call failed after",
    "Token Plan 用量上限", "Token Plan 套餐",
    "Connection reset by peer", "Connection refused", "Connection timed out",
    "Server error '5", "Bad Gateway", "Internal Server Error",
    "Service Unavailable", "Gateway Timeout",
    "All connection attempts failed", "Cannot connect to host",
    "ECONNREFUSED", "ECONNRESET", "ETIMEDOUT",
)

def _is_adapter_error_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    return any(token in raw for token in _ADAPTER_ERROR_TOKENS)
```

### 2. 两处调用点先判 adapter 错误，再判 sentinel

**主调用路径**（行 2164 附近）：
```python
if not result.ok:
    raise RuntimeError(f"Batch planner LLM call failed: {result.error}")
raw = str(result.value or "")
# 新增：adapter 错误体 → 走与 sentinel 同等的 fail-fast / retry 路径，
# 但 raise 时用诚实的"adapter error"消息
if _is_adapter_error_text(raw):
    logger.error("[BATCH_PLANNER] adapter error body detected, raw output (first 500): %s", raw[:500])
    if attempt < unavailable_retries:
        logger.warning("[BATCH_PLANNER] adapter error, retrying...")
        if retry_delay:
            await asyncio.sleep(retry_delay)
        continue
    raise RuntimeError(
        f"Batch planner LLM adapter error (likely upstream HTTP/network): {raw[:500]}"
    )
if not _is_unavailable_sentinel(raw):
    break
```

**retry 调用路径**（行 2277 附近）做同样检查，命中直接 break 出 retry 循环 → 走外层 `RuntimeError("invalid JSON")` 但 raw_preview 里包含真实错误体。

### 3. 测试

`tests/test_batch_planner_adapter_errors.py`（14 个用例）：
- 6 个正向 case（钉真实 03 incident shape：429 quota、502 bad gateway、connection reset、All connection attempts failed、5xx 系列、timeout token）
- 4 个负向 case（unavailable sentinel 不误判、valid JSON 不误判、空串、1 字符垃圾 `n` 不走 adapter 路径——保持原 JSON parse 失败的诚实报错）
- 4 个 source-level wiring 测试（regex 钉 `_is_adapter_error_text` 在两处调用点都先于 sentinel 出现 + RuntimeError 消息不含 "invalid JSON"）

不复用现有 `_json_from_llm` 的集成测试框架（mock RobustExecutor + registry + task_instance 链路太脆弱），用源文 regex 钉结构更可靠。

### 4. Layer 2：self-drive 去重 guard

最初调查 `partner/governance/instance_native.py::recover_or_start` 和 `handle_terminal` 时假设 B 锚点在那里，但追 `event_pipeline.jsonl` 后发现：

- 03 incident 里 `task_instance.json` 显示 `completion_status: pending`，**根本没进 handle_terminal**——RuntimeError 不写进 governance metadata，`_failure_evidence` 看到的是 `ok=True`
- 真实循环路径 = `scripts/run_project_self_drive.py:run_once` 每 ~4 分钟 tick 调 `_write_inbox` 写新行，runtime 读到 → batch_planner 抛 → task 进 pending 状态 → 下次 tick 又写新行

**真正锚点 = self-drive 脚本**。`scripts/run_project_self_drive.py` 加：

```python
DEFAULT_FAILURE_WINDOW_MINUTES = 15
DEFAULT_FAILURE_THRESHOLD = 3
PAUSE_LEDGER_NAME = "auto_drive_pause.json"

def _recent_task_failure_count(workspace, instance_id, project_id,
                                window_minutes=DEFAULT_FAILURE_WINDOW_MINUTES, now=None):
    # 读 instances/<id>/state/event_pipeline.jsonl 末尾 256KB
    # 算近 window_minutes 内 type=task_failed 且 msg 含 ROLE_PROJECT[<id>] 标题的事件数
    ...

def _write_pause_ledger(workspace, instance_id, project_id,
                         failure_count, threshold, window_minutes, last_reason):
    # 写 instances/<id>/state/auto_drive_pause.json
    # operator 可读：status / observed_failure_count / last_failure_reason
    ...
```

`run_once` 写 inbox 前先查 dedup guard：

```python
failure_count, last_reason = _recent_task_failure_count(
    workspace, instance_id, project_id, window_minutes=window_minutes)
if failure_count >= threshold:
    _write_pause_ledger(...)
    summary["skipped"].append(f"{instance_id}:DEDUP({failure_count} failures in {window_minutes}min)")
    logger.warning(...)
    continue
```

阈值/窗口都通过环境变量可调（`PARTNER_SELF_DRIVE_FAILURE_THRESHOLD` / `PARTNER_SELF_DRIVE_FAILURE_WINDOW_MINUTES`），canary / production 可独立配置。

`tests/test_self_drive_dedup.py`（9 个用例）：
- 5 个 `_recent_task_failure_count` 单测（窗口内/外、无文件、非 task_failed 不计、其它 project 不计）
- 4 个 `run_once` 端到端（03 DEDUP 跳过/其它正常 emit、pause ledger 内容正确、env var override、health instance 不写 ledger）

## 验证

- 现有 `tests/test_micro_planner_extraction.py` 15 个用例全过（无回归）
- 新增 `tests/test_batch_planner_adapter_errors.py` 14 个用例全过
- 新增 `tests/test_self_drive_dedup.py` 9 个用例全过
- 用 03 instance 真实 event_pipeline.jsonl（2026-09-05 19:16~20:08 的 10 条 task_failed）跑模拟测试，DEDUP 正确触发，pause ledger 正确写入
- 真实 03 instance 现在的 count=0（事件已超过窗口），符合预期。**如果 Hermes 配额再次耗尽，第 3 次失败后 dedup 会立刻停止自驱注入，不再产生新的"收到指令...正在思考...⏹️ 已停止"循环**

## 教训

1. **错误消息要诚实反映真因**。"invalid JSON" 这种下游症状会掩盖上游根因（这里是 quota 耗尽）。
2. **adapter 边界必须显式鉴错**。`_json_from_llm` 假设输入是 LLM 输出，但 robust executor 的 `result.value` 可能根本不是 LLM 输出——边界处必须分类（adapter error / sentinel / 真实 LLM 输出）。
3. **错误消息里留 first 500 chars raw 很重要**。retrospective 阶段没有 log 也能从 raised RuntimeError 反推上游真实错误体。
4. **自循环放大器必须在源头去重**。光让 batch_planner 报错"诚实"不够——runtime 接到 RuntimeError 后不会更新 consecutive_failures（因为没走 handle_terminal），所以外层 dedup 必须放在 self-drive 写入 inbox 之前，而不是等 runtime 处理失败时。
