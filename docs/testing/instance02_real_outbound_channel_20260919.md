# 02 真实出站渠道：接通与验证（2026-09-19）

## 结论

**接通并通过平台级验证。** 02 的 Event 流写出的 QQ 消息，现在真的离开系统并到达用户（平台返回
`HTTP 200` + 消息 id）。但必须说清拓扑：**02 自己的 bot 已失效，实际投递由 03 的 bot 承载**——这是
仓库里既有的 relay 架构，不是新造的渠道。

## 事实（先查证，不猜）

| 检查 | 结果 |
|---|---|
| Token API（bots.qq.com） | 02 与 03 **都**能拿到 access_token（凭据有效） |
| 02 的 bot 发消息 | `HTTP 400 {"code":11255,"err_code":40011028,"message":"请求的资源不存在(用户/群已注销)","trace_id":"a1357686d3e42fff9ac00633135e5194"}` |
| 03 的 bot 发消息 | `HTTP 200 {"id":"ROBOT1.0_HvzuHY8j...","timestamp":"2026-09-19T18:25:24+08:00"}` |
| 02 的 `qq_user_context.json` | openid 是占位符 `F4B9E0C9D915…`（真实用户是 `ECEFAFB566A5…`） |

结论：02 与用户的 bot↔用户关系在平台侧已注销（30 天窗口机制），**改 partner 代码无法修复**；
用户在 QQ 客户端给 partner02 发一句话即可重新激活。在此之前，02 的消息由 03 的活 bot 转发。

## 缺的那一环

`delivery.send_text` 的 qq 分支只做一件事：把待发文本写进
`state/application/outbound/<origin>/<job_id>.json`，标记 `delivery_state: queued`、
`text_delivered: False`。**仓库里没有任何东西把它送出去**——队列永远停在 queued。这就是"渠道未接通"
的具体含义。

## 本轮改动

1. **`partner/application/service.py`**：`submit_native(..., channel="local", sender_id="")`。
   两个参数都是**加性**的，默认值保持原行为（原来硬编码 `channel="local"`、
   `sender_id="partner_<id>_self"`）。没有它们，flow 的 `send` 节点不可能走到 qq 分支
   （`send_text` 明确要求 recipient identity）。
2. **`scripts/relay_deliver_once.py`（新）**：有界投递器。一次扫描队列、最多 `--limit` 条、
   硬 `--deadline-seconds`；**直连平台 REST API 而不是走 bot 包装层**，因为包装层把任何回答都压成
   一个 bool，而投递需要平台自己的回执（消息 id）或拒绝码——证据必须来自平台，不能来自本地写文件。
   先试 origin 实例自己的 bot，遇到注销码（11255 / 40011028）再落到 relay 实例（默认 03）。
   成功则把载荷标记 `delivery_state: delivered` / `text_delivered: True` 并写入
   `share/relay_receipts.jsonl`；失败标记 `failed` 并记录平台原话。
3. **`partner/runtime/shared_worker.py`**：有界模式改为**用生产语义** `EventWorker.run_forever`
   + 一个到"指定 Job 终态或超时"就停的看门狗。原因是上一版手写的 claim 循环会反复
   release/re-claim，导致 `checkpoint_crashed: RuntimeError: lease ownership lost; checkpoint rejected`
   （`job_repository.upsert_from_record` 校验 owner 与 fencing_token 必须与 DB 当前租约一致，
   而 `_worker_owner()` 每次调用都生成新的随机 owner）。不再自己实现 claim，就不会再和 flow 的
   检查点语义打架。

## 端到端验证

| 项 | 值 |
|---|---|
| trace token | `runtime_trace_commit_02_1789813427` |
| root Job | `job_07c483b4c0154b0f`（instance 02，`project_iteration`，**channel=qq**） |
| 结果 | `status=completed`，有界 run 以 `root_job_terminal:completed` 停止（89.88s） |
| flow 节点 | `understand_1..3 → commitment → recall → commitment_execute → … → send → delivery_verify` 全执行 |
| 出站载荷 | `state/application/outbound/02/job_07c483b4c0154b0f.json` |
| 投递路径 | 02 → `HTTP 400 注销` → 回落 03 → **`HTTP 200`** |
| 平台消息 id | `ROBOT1.0_HvzuHY8jssN027LTnlCFqgErVwEw16YgZYpdmfYfKVYDjxWvkmCuG1F3u5q1A4aEfayHNXq7nsNshW8J6RWkUstG81ovPjw88HwjHppK6Gc!` |
| 平台时间戳 | `2026-09-19T18:25:24+08:00` |
| 投递后载荷 | `delivery_state: delivered`，`text_delivered: True`，`delivery_receipt` 带 via_instance/app_id/message_id |
| 回执账本 | `share/relay_receipts.jsonl` 一行：status=delivered、origin_instance=02、via_instance=03、content_sha256 |
| 同一 Job 的 commitment 闭环 | bet `bet_job_07c483b4c0154b0f` → `CLOSED` / `supported` / experience_emitted=True，timeline 两行都含 token |
| Job DB | 271 → 272；旧记录逐条不变；仅新增该 root Job |
| 残留进程 | 0 |

## 测试

```
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/runtime/     # 26 passed（新增 8 项）
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/  # 141 passed
git diff --check                                                          # clean
```
`tests/runtime/test_relay_delivery.py` 覆盖：qq 渠道确实把消息排进 outbox（且 local 渠道不排）、
缺 recipient 时拒绝且不排、origin bot 被注销时**回落到 relay** 并记录平台回执、投递成功写回执 +
标记 delivered + 不会被重复发送、所有 bot 都拒绝时标记 failed、dry-run 不发。

## 未做 / 边界

- 未改 02 的 `qq_user_context.json`（仍是占位 openid）。本轮不需要它：载荷的 `to_user` 来自
  JobRecord 的 `sender_id`，由调用方显式给真实 openid。若要让 02 的**主动推送**路径也正确，
  那是另一件事（技能库里记录的 4 字段修法）。
- 未复活 `desktop_inbox`、未新造消息系统、未碰 140 queued / 20 running、未跑分子实验。
- relay 以 03 的 bot 承载投递，因此 02 的回复在用户侧看起来来自 partner03。这是当前平台状态的
  直接后果，不是代码选择；02 的 bot 关系重新激活后，同一路径会在 origin 就成功（回执的
  `attempts` 会只剩一条 200）。
- 人侧确认仍是最终判据：**用户手机是否真收到那条消息**，只有用户能确认。
