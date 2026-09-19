# 02 Event 流 → commitment 内核 接点验收（2026-09-19）

## 结论

**通过。** 实例 02 通过正式 Event 流消费了一条消息，Event 流在 `commitment` 节点调用内核，
内核产生并记录了携带 trace token 的 BetRecord，BetRecord 进入 event log 与 Job timeline。

## 接入点（第一步的答案）

**Event Flow 的哪个节点应该调用 commitment 内核**：
`DIRECT_ANSWER` 流程在 `understand_3`（消息理解完成）之后新增一个 `commitment` 节点，
事件类型 `commitment.bet_record`。选择理由：

- 现有 `direct_answer` 是消息类任务的真实执行路径（上一轮 root Job 跑的就是它），
  在此接入不需要新造 Flow 类型；
- 节点放在 `understand_3` 之后、与 `answer` 并列，因此在消息被理解后立即记录，且不依赖
  任何 LLM 产物；
- 没有执行动作：节点只冻结并落盘一条 BetRecord，然后结束。

版本处理沿用既有模式：`direct_answer` 升到 **1.1.0**，旧的 **1.0.0** 拓扑保留在
`LEGACY_PRESENTATION_FLOWS`，仍可按 (name, version) 解析（已 pin 的请求不受影响）。

## 关键 ID

| 项 | 值 |
|---|---|
| trace token | `runtime_trace_commit_02_1789811949` |
| instance | `02` |
| root Job | `job_c5842c0402674a06`（status=completed） |
| root Event | `evt_a8b6d004a5d34628`（`interaction.message_received`） |
| Flow | `flow_d7b9bfa62c824516`（`direct_answer` 1.1.0） |
| commitment 事件 | `evt_c8c49bb182914cb7`（`commitment.bet_record`） |
| commitment BetRecord | `bet_job_c5842c0402674a06`（schema `commitment/2`，status `COMMITTED`，env `isolated_sample`） |
| claim owner | `shared-02-42ffb7c5-...:171470-d35454` |

## 关键 Event 与时间（UTC）

| 时间 | 阶段 | 证据 |
|---|---|---|
| 09:59:10 | message_received | `evt_a8b6d004a5d34628`（outcome 含 trace token） |
| 09:59:10–09:59:18 | understand_1..3 | `completed_event_ids` 前三项 |
| — | **commitment 节点被触发** | `completed_event_ids` 第 4 项 `commitment` |
| 09:59:22 | **BetRecord created + recorded** | `evt_c8c49bb182914cb7`；`bet.json` / `manifest.json`；bet 链事件 `bet_revision → lifecycle → bet_recorded` |
| 09:59:28 | flow 终止（job completed） | `completed_event_ids` 10 项（`commitment` 在 `understand_3` 与 `answer` 之间） |
| — | 有界 worker 退出 | `steps=['ran:job_c5842c0402674a06','no_scoped_job_left']`，exit 0，18.52s |

## trace token 出现的三处（全部满足）

1. **message_received 事件**：`evt_a8b6d004a5d34628` 的 outcome 文本含 token；
2. **commitment BetRecord**：`bet.json` 的 `question` 与 `context/snapshot.json` 含 token；
   bet 自身事件链 `bet_recorded.payload.trace_token` = token；`manifest.extra.trace_token` = token；
3. **Web timeline**：`/api/jobs/<id>/timeline` 的实现是 `repo.history(job_id)`，其第 3 行
   （seq 2276）为：
   ```
   kind        : commitment_bet_recorded
   from->to    : running -> running          (审计记录，不是状态转换)
   detail      : {"bet_id": "bet_job_c5842c0402674a06",
                  "trace_token": "runtime_trace_commit_02_1789811949",
                  "state": "COMMITTED", "run_id": "event_flow_job_c5842c0402674a06",
                  "store": "/mnt/e/work/partner_workspace/state/commitments/..."}
   ```

## Job DB 前后差异

| | 值 |
|---|---|
| DB | `/home/os/.local/share/partner/runtime/83d524ab71b932a6/jobs.db` |
| 运行前 | queued 140 / running 20 / completed 73 / failed 34 = **267** |
| 运行后 | 267 条旧记录 status 与 updated_at **逐条不变**（changed_old = 0） |
| 新增 | 仅 `job_c5842c0402674a06`（无子 Job） |

## 本轮改动

1. `partner/event_fabric/models.py`：`EVENT_SERIES` 增加 `commitment`
   —— 这是第一次跑失败的直接根因，且是**已记录的先例**
   （`partner/observe/precedents.py` `case_05_event_series_unknown`：
   新增 EventDefinition 的 series 未注册会被 `EventLedger.create` 拒绝，
   表现为 `job error = checkpoint_crashed: ValueError: unknown Event series: <name>`）。
2. `partner/events/commitment.py`：新增 `commitment.bet_record` 事件与
   `commitment_bet_record` handler（**record-only**：冻结 + 落盘 BetRecord + 写 bet 事件链 +
   timeline 审计记录；不执行动作、不调 LLM、不碰项目数据；`environment=isolated_sample` 保证不可发布）。
3. `partner/index/job_repository.py`：新增 `note(job_id, actor, kind, detail)`，
   向 `job_history` 追加**不改 status** 的审计行（from_status == to_status），
   使内核记录在 Job timeline 可见，且不可能被误认为状态转换。
4. `partner/event_flows/builtins.py`：`DIRECT_ANSWER` 1.1.0 增加 `commitment` 节点；
   1.0.0 作为 `DIRECT_ANSWER_V1` 保留在 `LEGACY_PRESENTATION_FLOWS`。

未修改 commitment 内核语义、测试或证据结构；未触碰 139/140 queued 与 20 running 历史 Job；
未复活 `desktop_inbox`；未新造消息系统。

## 失败与修复记录

| # | 失败点 | 原始错误 | 分类 | 修复 |
|---|---|---|---|---|
| 1 | flow 启动即崩 | `checkpoint_crashed: ValueError: unknown Event series: commitment` | wiring failure | `EVENT_SERIES` 注册 `commitment`（先例 case_05 的标准修法） |
| 2 | 状态不一致 | `bet.json` 为 COMMITTED 而 `state.json` 为 PROPOSED | wiring failure | lifecycle 状态改为与冻结记录一致（`frozen.status`） |

## 测试

```
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/runtime/     # 14 passed
PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider tests/commitment/  # 141 passed
python3 -c "from partner.events import builtin_definitions; print(len(...))"  # 136
git diff --check                                                          # clean
```
新增 `tests/runtime/test_commitment_event_binding.py`（6 项）：`commitment` series 已注册；
catalog 含 `commitment.bet_record`；`direct_answer` 1.1.0 含 commitment 节点且 1.0.0 仍可解析；
handler 产出含 token 的 BetRecord + bet 事件链 + timeline 审计行；无 token 时拒绝且不写任何东西；
record-only bet 的环境不可发布。

## 遗留

- 本轮只验证"02 Event 流 → commitment 内核"这一个接点，**未**做分子实验、PDF、QQ、legacy 语义、
  benchmark、生产接入。
- BetRecord 停在 `COMMITTED`（record-only），未执行、未测量、未结算——这是本轮的设计边界。
- 第一次失败的 Job `job_c1b793c1fe5d4066`（status=failed）作为失败证据保留，未删除、未改写。
