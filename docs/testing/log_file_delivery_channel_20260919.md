# 日志文件出站渠道（2026-09-19）

## 为什么

QQ 渠道依赖平台侧的 bot↔用户关系（02 的已注销），且投递证据来自外部平台。改用**日志文件渠道**后，
出站不再依赖任何外部服务，投递证据是本地可核查的文件字节：offset + sha256 + 回读。

## 渠道语义

`delivery.send_text` 新增 `channel in {"log", "file"}` 分支（`channel_route` 同步接受）：

- 目标文件：`<workspace>/state/outbound/replies.log`（JSON Lines，追加写）
- 投递 = **durable append**：`write` + `flush` + `os.fsync`
- 回执 = `{"channel","path","offset","bytes","sha256"}` —— offset 是该行起始字节，sha256 是该行
  （含换行）的摘要，可对文件直接核验
- 每行含 `ts / instance / job_id / project_id / flow_id / node_id / content`
- 与 qq 渠道不同：**不写排队载荷**，因为它不是"稍后发送"，它就是发送

读取入口（就是你测试用的命令）：

```bash
cd /mnt/e/work/partner_commitment
PYTHONPATH=. python3 scripts/read_replies.py --workspace /mnt/e/work/partner_workspace \
    [--limit 5] [--job job_xxx] [--grep TOKEN] [--json]
```

## 端到端验证

| 项 | 值 |
|---|---|
| trace token | `log_trace_02_1789814040` |
| root Job | `job_b889b2a9b9554155`（instance 02，`project_iteration` 2.6.0，**channel=log**） |
| 结果 | `status=completed`，`error` 为空，有界 run `root_job_terminal:completed`（68.29s） |
| commitment 节点 | `commitment` + `commitment_execute` 均在 completed 内 |
| bet | `bet_job_b889b2a9b9554155` → COMMITTED → CLOSED / supported（timeline 两行都带 token） |
| 渠道输出 | `state/outbound/replies.log` 18:35:09 一行，instance=02，job/job_id 正确，正文可读 |
| 文件字节 | 596 → 1214（+618，与该行 bytes 一致） |
| Job DB | 273 → 274；旧记录逐条不变；仅新增该 root Job |
| 残留进程 | 0 |

## 顺带修掉的两个真实缺陷

1. **token 正则与操作者用词耦合**（implemented in `partner/events/commitment.py`）：原正则只认
   `runtime_trace…`，所以本轮用 `log_trace_02_…` 时 commitment 节点直接报
   `no trace token found in the triggering message`（节点失败 → flow `event_flow_failed`）。
   现改为不指定前缀：任何"含 trace 的标识符形状"都认（`runtime_trace_x` / `log_trace_x` /
   `trace_abc123` / `canary_trace_x-1`），而普通英文句子里的 "trace" 不会被误认。
2. **`partner/events/evolution_pipeline.py:156` 从错误模块导入 `build_flow_registry`**：
   `from partner.event_fabric import … build_flow_registry` → `ImportError`，节点失败。
   正确模块是 `partner.event_flows.registry`。这是上一轮 job 以 `error='event_flow_failed'`
   结束的直接原因（ledger 里有 4 条该 ImportError 的失败事件）。已修并有回归测试。

## 测试

```
tests/runtime/     32 passed（本轮新增：log 渠道追加+回执可核验、多次追加不丢行、channel_route 接受 log、
                              任意 trace token 形状被识别且不误吞英文、evolution_pipeline 导入回归）
tests/commitment/ 141 passed
git diff --check  clean
```

## 边界

- 未改默认渠道（仍 `local`）；要用日志渠道需显式 `submit_native(..., channel="log")`。
- 未删除 QQ 渠道与 relay 投递器，它们仍在（`scripts/relay_deliver_once.py`），只是不再需要。
- 日志渠道只证明"离开系统并落到可核查的文件"，**不证明有人类读过**；人侧确认仍是最终判据。
