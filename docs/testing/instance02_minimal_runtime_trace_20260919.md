# 实例 02 最小真实运行时链路 trace（2026-09-19）

## 结论

**通过。** 一条带唯一 trace token 的消息经正式 Application Service 提交，由 **实例 02**
身份的有界 runtime 执行，走完正式 Event Flow（9 个节点），产出含 token 的正式回复，
回复可在**官方 Web 读模型**中查询；历史 264 个 Job 无一条被领取或改动。

## 关键 ID

| 项 | 值 |
|---|---|
| trace token | `runtime_trace_02_1789811057` |
| instance | `02` |
| root Job | `job_b04cab19c53c45d8` |
| root Event | `evt_c25cdd7516b54841`（`interaction.message_received`） |
| Flow | `flow_ae0f847cd2a4474d`（`flow_type=direct_answer`，`route=project_iteration`） |
| claim owner | `shared-02-42ffb7c5-a0b3-4626-994a-45db98e282fd...`（实例 02 身份） |
| 回复 update | `evt_05f27c5fa5234cbe`（`line=conversation`） |
| 投递 Event | `evt_0a3fb0d4209c4e8e`（`delivery.send_text`，`channel=local`） |
| 投递回执 | `receipt = {channel: local, projection: event_ledger}` |

## 链路与时间

| 时间(UTC) | 阶段 | 证据 |
|---|---|---|
| 09:44:18 | Application Service 提交 → root Job + root Event | `evt_c25cdd7516b54841` |
| 09:44:33 | intent_observe | `evt_22aa7c38dd7a4701` |
| 09:44:37 | intent_counter_read | `evt_64a3b06a354f445f` |
| 09:44:40 | understand（3 节点） | `evt_9b8e9771b75749e8` |
| 09:44:42 | compose / critic → 回复正文 | `evt_05f27c5fa5234cbe`, `evt_4325e51c6e3c45af` |
| 09:44:45 | 投递 `delivery.send_text` | `evt_0a3fb0d4209c4e8e` |
| 09:44:49 | Job → `completed`（history: create→claim→save） | `repo.history()` 3 行 |
| — | 有界 worker 退出 | `steps=['ran:job_b04cab19c53c45d8','no_scoped_job_left']`，exit 0，24.49s |

Flow 完整节点：`understand_1..3 → answer → compose → critic → deduplicate → send → verify`。

## 回复内容（官方读模型查询结果）

```
update_id : evt_05f27c5fa5234cbe
job_id    : job_b04cab19c53c45d8
project   : molecular_generation
line      : conversation
subject   : runtime_trace_02_1789811057

            这是按正式 Event 流原样返回的 trace token，未启动项目任务、未生成后续任务、未做任何实验。
delivery_policy: local   channel_ack: False
```
`ApplicationReadModel.updates(limit=60)` → 3 条，其中 1 条含 token；
`ApplicationReadModel.event_details(job_id=...)` → 10 条，其中 5 条含 token。

## Job DB 前后差异

| | 值 |
|---|---|
| DB | `/home/os/.local/share/partner/runtime/83d524ab71b932a6/jobs.db` |
| 运行前 | queued 140 / running 20 / completed 71 / failed 33 = **264**，mtime 2026-09-19 15:38:45 |
| 运行后 | 同 264 条状态与 `updated_at` **逐条不变**（changed=0，disappeared=0） |
| 新增 | 仅 `job_b04cab19c53c45d8`（status=completed, flow_id=flow_ae0f847cd2a4474d, root_event_id=evt_c25cdd7516b54841, assigned_instance=02, project=molecular_generation） |

## 最小修复（本轮唯一改动）

1. `partner/index/job_repository.py`：`list_by_status()` 新增可选 `job_id` / `flow_id` /
   `root_event_id`，**在 SQL WHERE 中过滤**，并返回 `flow_id`/`root_event_id`。默认参数
   完全保持原查询行为。
2. `partner/runtime/event_worker.py`：`EventWorker.__init__` 新增 `root_job_id`；
   `next_job()` 在其设置时走 `_next_scoped_job()`（只取该 root Job 或同 flow 的 Job），
   并带一道 flow 兜底校验，防止未打补丁的目录扫描路径返回无关 Job。
3. `partner/index/worker_patch.py`：`_queue_jobs` 在 `root_job_id` 存在时用
   `list_by_status(..., job_id=/flow_id=)` 走 SQL 过滤，不读全表、不扫目录。
4. `partner/runtime/shared_worker.py`：新增 `--root-job-id / --instance-id / --once /
   --deadline-seconds` 有界模式（`_run_bounded`）。无参数时行为与原来完全一致。

## 与用户给定链路的一处偏差（如实说明）

用户链路的第 1–2 步是"消息进 02 inbox → 02 runtime 读取 inbox"。经诊断，
`instances/02/state/desktop_inbox.jsonl` 在代码中**已被显式退役**
（`instance_native.py:722` 注释：“not the retired desktop_inbox.jsonl”），
当前**没有任何消费者**会读取该文件并提交到 Application Service
（`send_manual_task.py` 的 docstring 声称存在 "desktop_inbox poller"，但该 poller 不存在）。
因此本轮走的是**真实在生产使用的那条路径**：`PartnerApplicationService.submit_native()`
→ `jobs.db` + Event Flow → 有界 worker 执行 → 官方读模型回复。
"inbox row" 在现行架构中的等价物就是 Application Service 提交产生的 root Job。
本轮未新增第二套消息系统。

## 测试

```
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/runtime/      # 8 passed
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/   # 141 passed
python3 -c "from partner.events import builtin_definitions; print(len(...))"  # 135
git diff --check                                                          # clean
```
`tests/runtime/test_bounded_worker.py` 覆盖：无作用域查询行为不变；按 job_id/flow_id/
root_event_id 的 SQL 过滤（含 25 条积压干扰下不泄漏）；终态 root Job 不再返回；
`_next_scoped_job` 拒绝外来 flow 只取自身 flow；`next_job()` 在设定 root_job_id 时
不与全局队列交互；未设定时仍走原路径。

## 遗留

- 上一轮（`run_targeted_instance_task.py`）曾把"提交成功"误判为完成、标了 seen 并写入
  占位回复（row `manual_02_1789801063_417180df8f95`）。该过度声明已记录，
  本轮的 completion 判定改为以 Job/Event 终态为准。该脚本未被本轮删除，但其结论不采信。
- QQ 渠道本轮未验证（`channel=local`）；按用户要求本轮不修 QQ，不阻塞验收。
- 生产 runtime 的默认行为未改变：不带 `--root-job-id` 时仍是普通共享 worker。
