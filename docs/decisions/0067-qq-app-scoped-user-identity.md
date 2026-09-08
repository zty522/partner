# ADR 0067：QQ 用户身份按 Bot App 隔离，禁止跨实例硬锁与转发冒充

- 状态：Accepted / Production Active
- 日期：2026-09-07
- 前置：ADR 0064（runtime spawn + supervise）、ADR 0065（项目推进链路）
- 关键文件：`partner/evolution/sprint18_unified_patch.py`、`partner/__main__.py`、`partner/mind/executor.py`

## 真实故障

五个 Bot 的 App ID/Secret 均已分别配置，五条 WebSocket 也都能连接并出现 `Bot ready`，但只有 03 能向用户发消息。01、02、04、05 的真实 QQ API 回执均为 HTTP 400、`用户/群不存在`。

根因不是网络或 Bot 配置，而是 `lock_openid_for_workspace()` 把一条硬编码 OpenID 写入每个实例的 `state/qq_user_context.json`。QQ Official 的用户 OpenID 属于 **Bot App 命名空间**：同一用户面对 01–05 五个 App 会得到五个不同值。03 的 OpenID 对 01/02/04/05 均是非法目标。

旧代码还试图让失败消息经 03 relay 转发。这既不能修复原实例的发送能力，也会把“03 发出”冒充为“01/02/04/05 发出”，违反渠道真值和实例归属。

## 决策

1. 启动时只从当前实例自己的 `state/qq_chat_history.jsonl` 读取最近一条私聊入站消息，恢复当前 App 对应的 OpenID。
2. 不遍历兄弟实例，不接受调用方提供的 OpenID，不把群聊 sender 当作私聊主动推送目标。
3. 保留 `lock_openid_for_workspace` 名称作为兼容别名，但忽略旧 `target_openid` 参数并执行实例级恢复，防止旧调用重新污染。
4. QQ NACK 必须留在原实例的 delivery ledger 中并返回失败；关闭 03 的跨 Bot 自动 relay consumer 和失败 outbox 写入。
5. 历史 relay outbox 保留为故障证据，不删除、不计送达成功。

## 生产迁移与验收

- 从五个实例各自的真实入站 QQ 历史恢复了五个互不相同的 OpenID；日志和文档只记录指纹，不记录原值。
- 运行 `scripts/run_project_self_drive_oneshot.py`，五实例各发出一条由项目状态推导的任务：5 emitted、0 blocked、0 pending、0 no-next、0 error。
- 同一轮 `user_message_delivery.jsonl`：01/02/03/04/05 均出现新的 `status=sent`、`acknowledged=true`；修复前 01/02/04/05 为 `acknowledged=false`。
- 重启 `partner-instance-native.service` 后，五个实例均再次出现 `Bot ready`，五个 OpenID 指纹仍分别保持各自值，03 relay consumer 未启动。
- 定向回归：`18 passed`（实例身份、manual closure、oneshot）；另一次含 runtime 的联合测试有 18 passed、1 个测试因沙箱无 user-systemd bus 失败，不是 QQ 代码断言失败。
- 完整回归：`900 passed, 5 failed, 2 warnings in 106.72s`。除上述沙箱 systemd 项外，另有 4 个 Hermes 现有回归（active-learning canary、apply rollback 时间窗、两条 manual preflight 断言漂移）；均与 QQ 新增测试无关，但不得宣称全仓零回归。

## 本轮项目测试的诚实边界

QQ 5/5 恢复不等于五项目推进 5/5：

- 01/02/05 虽终态成功，但仍主要生成通用检查报告，`findings` 为固定句，下一行动为空。
- 03 对“验收条件未满足”产生了 `done`、正 Reward、policy eligible，并把新 molecular dynamics 任务记到旧 framework project，属于 false-success + project attribution 漂移。
- 04 Batch Planner 180 秒超时；failure owner/mechanism 为空，且未成为 learning observation。

上述问题进入下一轮 P0，不能用本 ADR 的 QQ 成功覆盖。
