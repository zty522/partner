# ADR 0100：Instance 退化为纯收发 + 共享 Worker Process Pool

状态：Accepted
决定日期：2026-09-12（用户确认"instance 只接发消息 + worker 跑业务"架构）
日期：2026-09-12
前置：ADR 0099（解绑 instance 与项目）、ADR 0098（纯 Event 生产）
触发：用户 2026-09-12 明确"实例应该只负责接发消息，运行跟实例无关"

## 一句话定义

Instance 进程 = 纯 QQ 通道 + LLM 同步调用 + 写 JobRecord 到共享队列；**不**起 EventFlowRunner。

Worker 进程（独立 process pool）= 从共享队列拉 JobRecord，跑 EventFlowRunner，写产物到 `share/projects/<project_id>/`。

Instance 与 Worker 在 OS 层面是不同进程、靠 JobRecord 队列通信。

## 背景与动机

ADR 0099 把"instance 绑固定项目"这个假设拆掉了——但留了一个中间状态：

> 每个 instance 同时承担"接 QQ 消息"和"跑业务"两件事，
> SlotArbiter 决定哪个 instance 的 worker 跑哪个 Job。

这违背了用户的实际意图：
- 用户期望：发给 02 的消息由 02 接、02 自己跑
- 用户原话："实例应该只负责接发消息，运行跟实例没啥关系"

SlotArbiter 跨实例派工反而把责任链拉长——02 接消息、01 干活、用户以为是 02 在跑。Phase 4 实际触发时，OmpA 消息被 02 接、`assigned_instance='01'`、结果 01 跑——这种"接发与运行分离"的语义让用户困惑。

C1 反转彻底：instance 不再跑业务，只负责收发；worker 独立进程池。

## 决策

### 1. Instance 进程职责（瘦）

`partner/runtime/instance_host.py` 启动后**只**做 4 件事：

1. 启动 QQ bridge（保持不变）
2. 接收 QQ 消息
3. 调 LLM 同步（intent_observe / counter_read / synthesize / classifier）
4. 写 JobRecord 到 `state/application/jobs/<id>.json`
5. 立即返回 Submission 给 QQ（不等 worker）

instance **不**启动 EventWorker、不跑 EventFlowRunner、不写项目产物。

### 2. Worker 进程职责

新增 `partner/runtime/shared_worker.py`：

- 启动后从 `state/application/jobs/` 扫描 status=queued 的 JobRecord
- 调 EventFlowRunner 跑该 Job 的 pinned Flow
- 写产物到 `share/projects/<project_id>/`
- 写 EventSummary 到 `event_fabric/summaries.jsonl`
- 更新 JobRecord status=completed / failed

worker 数量：**与 instance 数量解耦**，由 ResourceScheduler 动态伸缩（已有 `effective_max_active` API）。
默认 3，可按 CPU/内存动态增减到 8、12 等。原则是 instance 提供 QQ 通道、worker 真正跑业务——5 个 instance + N 个 worker 是两种独立维度。

### 3. 队列通信

JobRecord 已经是 file-based 队列——`state/application/jobs/*.json`。

instance → worker：instance 写 `status=queued`，worker 轮询 `*.json` 找到新文件。
worker → instance：worker 更新 `status=completed`；instance 不需要主动读——**消息推送不依赖 instance 状态**。

ACK 推送链路：
- instance 跑完同步阶段（classifier + project_init）→ 立即回 Submission 给 QQ（"已自动创建新项目 X"）
- worker 跑完 EventFlowRunner → 写 `event_fabric/summaries.jsonl`
- **新 Event** `delivery.message_push_pull`：worker 完成后，独立 instance 进程（哪个都行）轮询 summaries，把 notification_kind=milestone/blocked/final 的 push 到 QQ

注：上面这个 push-pull 模型对架构侵入较小；如果想更简洁可以加个 `event_fabric/notifications.jsonl`，worker 直接写、QQ bridge 进程读——但都需要改 5+ 文件。先保持 polling 简单版。

### 4. 项目产物路径变化

```
# 改前（C0/C2）
share/projects/<project_id>/
  project_brief.md
  governance/receipts/
  external_artifacts/<uuid>/

# 改后（C1）
share/projects/<project_id>/                ← 不再加 instance 维度
  project_brief.md
  governance/receipts/
  external_artifacts/<uuid>/
```

**关键决定**：`share/projects/` 下**不再有 `instances/<id>/` 层级**。理由：
- 项目天然是跨 instance 的（一个 project_id 不该绑死某个 instance）
- instance 退化成"接发 worker"，写产物的不是 instance 是 worker
- worker pool 是共享的、产物也是共享的

`share/projects/registry.json` 移除 `owner_instance` 字段——项目不再有所有者。

### 5. assigned_instance 改为 advisory

`JobRecord.assigned_instance` 字段保留（不改 dataclass schema），但：

- instance 写 JobRecord 时填 `assigned_instance = persona_hint`（消息来源实例）
- **这个字段不再被任何路由逻辑读**——只是 audit 信息
- worker 跑 Job 时不读它
- 跨实例派工的 SlotArbiter 代码路径**删除**

### 6. 删除 SlotArbiter

`partner/governance/slot_arbiter.py` 整个删除。Phase 2 写的 8 个测试一并删除。

`partner/application/service.py` 里调用 `SlotArbiter.pick_idle_instance()` 的代码删除，恢复成 `assigned = persona_hint or "02"`。

### 7. Supervisor 脚本改

`scripts/run_instance_native_runtime.py`：

```python
# 改前
subprocess.Popen([python, "-m", "partner", "--instance-id", "01", ...])
subprocess.Popen([python, "-m", "partner", "--instance-id", "02", ...])
...  # 5 个 instance

# 改后
subprocess.Popen([python, "-m", "partner", "--instance-id", "01", ...])
...  # 5 个 instance，纯收发
for i in range(worker_count):
    subprocess.Popen([python, "-m", "partner.shared_worker", "--slot-index", str(i), ...])
```

systemd unit `partner-instance-native.service` 不变——仍拉这个 supervisor 脚本。

### 8. Migration 步骤（不分阶段一次切）

**前提**：用户已经全切 flag 到 `new`（Phase 4 已做），5 instance 都在新代码上。

**Step 1**：把 slot_arbiter 相关代码删
- 删除 `partner/governance/slot_arbiter.py`
- 删除 `tests/test_slot_arbiter.py`
- 改 `service.py` 不再 import SlotArbiter
- 跑全仓回归，必须 598 - 8 = 590 passed（删了 8 个 slot_arbiter 测试）

**Step 2**：写 `partner/runtime/shared_worker.py`
- 单进程 EventFlowRunner 消费器
- 30-50 个 unit test

**Step 3**：改 `instance_host.py`
- 删 EventWorker 启动代码
- instance 只跑 QQ bridge

**Step 4**：改 `run_instance_native_runtime.py`
- supervisor 同时起 5 instance + 3 worker

**Step 5**：重启 + 端到端验证
- 发 OmpA 消息给 02
- 验证：02 QQ bridge 接消息 → 写 JobRecord → 02 立即回 Submission
- 验证：worker A / B / C 之一从队列拉 JobRecord → 跑 Flow → 写产物
- 验证：终态消息通过 instance QQ bridge 推送回用户

**Step 6**：写 ADR 0100 的"已实施"状态 + 更新 migration_plan_0099.md 的 Phase 5 / 6 步骤

**Step 6.5**：标记"传入实例 ID"作为项目元数据（用户 2026-09-12 明确要求）
- `JobRecord.intake_instance_id` 字段新增（保留 `assigned_instance` advisory 但不参与路由）
- `share/projects/<project_id>/project_contract.json` 加 `intake_instance: "02"` 字段
- worker 跑 Flow 时把 `intake_instance_id` 透传到 EventContext
- 终态消息推送：worker 写完 summary → polling 进程按 `intake_instance_id` 把消息推到对应 instance 的 QQ 通道
- **理由**：5 个 instance = 5 个 QQ Bot；worker 跑完发报告给用户，必须发到那个用户最初聊的 Bot 而不是别的；不保留 `intake_instance_id` 映射，消息会发错 Bot（用户根本看不到）

## 影响范围

### 必须改的代码

- `partner/runtime/instance_host.py` —— 删 EventWorker 启动
- `partner/application/service.py` —— 删 SlotArbiter 调用、`assigned = persona_hint`
- `scripts/run_instance_native_runtime.py` —— 加 worker spawn
- `partner/projects/project_registry.py` —— 移除 `owner_instance` 字段（advisory）
- `partner/events/interaction.py` —— `project_init` 不再写 `instances/<id>/` 前缀

### 必须新建

- `partner/runtime/shared_worker.py` —— worker 进程主循环
- `tests/test_shared_worker.py` —— ~30 test

### 必须删除

- `partner/governance/slot_arbiter.py` —— 整个文件
- `tests/test_slot_arbiter.py` —— 整个文件

### 不改

- `partner/projects/session_context.py` —— 跟 instance 解耦了
- `partner/projects/dynamic_project_registry.py` —— 同上
- `partner/application/project_classifier.py` —— 同上
- `partner/event_fabric/flow.py` / `runner.py` / `selectors.py` —— worker 直接消费
- `partner/governance/project_scaffold.py` —— 路径不变（不再有 instances/ 前缀）

## 验收门

### Step 1 后

- `pytest tests` ≥ 590 passed（删 8 个 slot_arbiter 测试后）
- legacy flag 下行为不变（虽然 default 已经 new，但切回 legacy 应该也能跑）

### Step 2 后

- shared_worker 30+ 个 unit test 全过
- shared_worker 在手动启动状态下能从 jobs 目录拉 JobRecord 并跑完（手动 mock 测试）

### Step 5 后（端到端）

- 5 instance + 3 worker 共 8 个进程跑
- instance 只接消息、不跑业务（ps -p 看 EventFlowRunner 调用在 worker 进程不在 instance）
- 发 OmpA 消息给 02 → 02 立即回 Submission（< 5 秒）
- worker 拉 JobRecord → 跑完 → 产物写 `share/projects/<project_id>/`
- 终态消息通过 instance 推回 QQ

### Phase 5 全完成

- `pytest tests` ≥ 620 passed
- 5 instance 行为：只接发、QQ 通道正常、Slack 不死
- 3 worker 行为：拉队正常、空闲不空跑
- share/projects/ 下中文目录工作正常（filesystem UTF-8 验证）
- canary 9/9（按 Phase 4 末态重新跑，不依赖 9/9 历史数字）

## 后果

### 短期

- instance 进程变轻——只跑 QQ bridge + 同步 LLM
- worker 进程独立——可以横向扩展到 10、20 个
- "02 在干嘛"这个问题消失——02 不再"干活"，只"接消息"
- `share/projects/` 不再有 `instances/<id>/` 层级——所有 instance 写同一棵树
- **5 instance 可以同时跑超过 5 个项目**——只要 worker pool 容量够（之前 ADR 0100 写"3 worker"是错的，应该是 N worker decoupled from 5）

### 中期

- instance 挂了不影响业务——消息丢到 QQ 重发队列、worker 继续跑已有 Job
- instance 数量可动态伸缩——加 instance 06 只需 supervisor 多 Popen 一个
- worker 数量由 ResourceScheduler 决定——CPU/内存低时多开、高时少开

### 长期

- instance 可以换成纯前端 GUI/TUI/Web——QQ 只是其中一个 channel
- worker 可以拆成专用 worker（如：分子 worker、动力学 worker）——但这又是 ADR 0099 反向的回潮，建议不做
- 项目路由完全在 classifier L1-L4 里——instance 只是 transport，不参与决策

## 不变量

- **历史不删除**：所有 ADR 0099 / 0098 / 0097 / 0095 / 0090 系列的历史项目目录、Receipt、Evidence 全保留
- **Event-first**：所有改动继续走 Event Flow，不引入新 inbox 兼容层
- **5 instance 数量不变**：从 5 个 message-queue-instance 改成 5 个纯收发 instance，**数量不变**
- **QQ 通道不换**：5 个 bot app_id/secret 保持不变

## 拒绝的方案

### C2 温和版

instance 还是跑项目，只是不跨派。

拒绝原因：instance 既是前端又是 worker 的双重身份没解决。"实例应该只负责接发消息"是用户原话，C2 没满足这个。

### 加 instance 维度到 share/projects/

```
share/projects/instances/<id>/<project_id>/...
```

拒绝原因：项目天然是跨 instance 的。加 instance 维度等于把项目按 instance 分裂——这跟 ADR 0099 的"项目是一等公民"假设冲突。

### Worker 跟 instance 强绑定（每个 instance 自带 1 worker）

instance 启动时 fork 一个 worker 子进程。

拒绝原因：worker pool 必须跨 instance 才能负载均衡。强绑定让 02 挂了时它的 worker 也跟着死——没解决"instance 挂了消息丢"的问题。

## 用户决议（2026-09-12）

1. ADR 0100 状态：Proposed → **Accepted**
2. worker 数量：**与 instance 数量解耦**——5 instance 是 QQ 通道数，worker pool 按资源动态伸缩（不绑死 5）；原则是 instance 接发、worker 跑业务
3. ack 推送链路：用 **polling**（每 instance 轮询 summaries.jsonl，按 `intake_instance_id` 推到对应 QQ Bot）。Phase 6.5 加 intake_instance_id 字段后实施
4. assigned_instance 字段：**保留 advisory**（用于 audit），不删 schema；新增 `intake_instance_id` 字段作为**真正的路由依据**

附加决议（来自 2026-09-12 用户原话）：
- "实例应该只负责接发消息"
- "一个项目传入任何一个实例，不应该是实例本身在运行项目，而是后台启动一个实例（另一种形式的，就是跑event啥的）"
- "你要给这个项目的名字中标上，哪个实例传入的就标哪个，比如01或者02"
- "5 个实例（连了qq），但是可以同时跑5个以上的project（如果资源允许）"
- "项目跑的过程中要汇报的话也通过传入的实例发送消息报告"

