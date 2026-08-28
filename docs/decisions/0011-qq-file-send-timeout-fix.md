# ADR 0011：QQ file send future.result timeout 30s → 90s

**状态**: accepted
**日期**: 2026-08-27
**触发**: Bug #42 — 04 holdout 1/5 push_files 协程 30 秒 TimeoutError

## 背景

2026-08-27 推进 04 holdout 1/5 (Aether world model) 时，4 步拓扑（read Aether README + read
Aether evaluation README + generate_text + create_file）全部 completed，`holdout_aether_b41.md`
1489 字节真写盘；但 push_files（step 5）失败：

```
[ERROR] [BRIDGE-DEBUG] Proactive file send failed:  file=holdout_aether_b41.md
  File "/mnt/e/work/partner/shells/frontend/qq_bot/qq_official_bridge.py", line 1383, in send_file_proactive
    result = future.result(timeout=30)
  File "/home/home/.../concurrent/futures/_base.py", line 458, in result
    raise TimeoutError()
```

`pushed=0/1, ok=False`，stop_reason  = "execution failed"。用户未在 QQ 收到文件。

## 根因

`shells/frontend/qq_bot/qq_official_bridge.py:1383`（proactive 文件推送）：

```python
future = asyncio.run_coroutine_threadsafe(
    self._bot.send_file(to_user, file_data, file_type, msg_type, ...),
    self._bot.get_event_loop(),
)
result = future.result(timeout=30)   # ← 30 秒
```

`self._bot.send_file`（`shells/frontend/qq_bot/qq_official_bot.py:381`）做两步：
1. **`upload_file`**（`_bot.send_file` 第 411 行）— `aiohttp.ClientTimeout(total=30)` POST base64 编码文件
2. **`send_message`**（`_bot.send_file` 第 420 行）— `future.result(timeout=15)`（`send_proactive` 第 489 行）

两步合计最坏 30 + 15 = 45 秒，但 `bridge.send_file_proactive` 的 `future.result(timeout=30)` 只给 30 秒——**upload_file 占满 30 秒时 send_message 还没机会跑**。

冷路径（首次推送、文件大小 ~ 2-4 KB）实测 40-50 秒才返回（网络 + base64 解码 + server 处理）；30 秒不够。

## 修复

`shells/frontend/qq_bot/qq_official_bridge.py` 两处 `future.result(timeout=30)` → `future.result(timeout=90)`：

```python
# Hermes 2026-08-27 fix (Bug #42): QQ file send is two-step
# (upload_file 30s + send_message) — give the future 90s
# so both can complete on cold paths.
result = future.result(timeout=90)   # line 1386 (was 30)

# Hermes 2026-08-27 fix (Bug #42): same rationale.
return future.result(timeout=90)        # line 1413 (was 30, in reply_with_file)
```

90 秒 = 30s (upload_file aiohttp timeout) + 15s (send_message future.result) + 网络余量（45s），足以覆盖 cold path。

## 验证

04 holdout 1/5 重跑（重启 04 加载新代码后）：

| step | event_type | result |
|---|---|---|
| read Aether root README | atomic_inspect_file | ok=True |
| read Aether evaluation README | atomic_inspect_file | ok=True |
| compose_holdout_report | generate_text | ok=True（plain markdown，无 JSON envelope）|
| write_holdout_report | create_file | ok=True，`holdout_aether_b42.md` 3806 字节 |
| **push_report_to_qq** | **push_files** | **ok=True, status=sent, pushed=1/1** ✅ |

stop_reason: **`deliverable file sent successfully`**（不再 TimeoutError）

## 全量回归

`339 passed in 15.24s`（333 + 4 Bug #38 + 1 Bug #39 + 1 Bug #40 = 339，0 回归）

## 后果

- 04 holdout 1/5 push_files 不再因 30 秒 timeout 失败
- 04 holdout 5/5 push_files 也仍可能受益（之前已经能成功但慢）
- 04 holdout 3/5-4/5 push_files 重新可用（之前因 citations<3 失败，但 push_files 是真发到 QQ 的）
- 所有走 push_files 路径的 04 任务未来都将受益

## 不重做

- 未改 `_bot.send_file` 内部两步 timeout（30 + 15），保持向后兼容
- 未改 upload_file 的 aiohttp.ClientTimeout（30）——这是 QQ API 的硬限制
- 未把 timeout 改成 120s 或更长（90s 已经覆盖 cold path + 余量）

## 与 Bug #41 的关系

Bug #41 是 content 生成阶段的 envelope 问题（修在 hermes adapter）；
Bug #42 是 push_files 阶段的网络 timeout（修在 QQ bridge）。
两个 fix 共同保证 04 holdout 链路完整跑通：

1. **Bug #41**：generate_text 输出纯 markdown（不再 ```json envelope）
2. **Bug #42**：push_files 真把 markdown 推到  送到用户 QQ

两个 fix 都 verified by 04 holdout 1/5 重跑。
