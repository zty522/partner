# Migration Plan：ADR 0099 + ADR 0100 实例-项目解绑 + 共享 Worker Pool

> 配套 ADR 0099（解绑）+ ADR 0100（instance 退化为纯收发 + 共享 worker pool）
> 本文是**操作步骤**——按此文档逐步执行，每步有显式验收门。
> **更新 ADR 不在此处**——ADR 改动要用户批准，本文随时可改。

---

## 状态看板（每次执行更新）

### ADR 0099 系列

- [x] Phase 1：ADR 0099 + 迁移计划（Approved 2026-09-12）
- [x] Phase 2：基础设施 4 模块（session_context / project_classifier / slot_arbiter / dynamic_project_registry）✅
- [x] Phase 3：分类器 + 调度切换（`feature_flag="new"`）✅
- [x] Phase 4：旧项目归档 + flag 全切 ✅
- [x] Phase 5：清理 + 文档更新 — **大幅缩减**，因为 ADR 0100 反转了 instance 角色

### ADR 0100 系列（新增）

- [ ] Phase 6：instance 退化为纯收发 + 共享 worker pool（当前阶段）
- [ ] Phase 7：scheduler 调整 + supervisor 改造
- [ ] Phase 8：端到端 canary + 文档收尾

---

## Phase 5（缩减后）

### 目标

**删除 slot_arbiter 相关代码**，把 `assigned_instance` 字段改为 advisory。

### 步骤

#### 5.1 删除 SlotArbiter 相关

- 删除 `partner/governance/slot_arbiter.py` 整个文件
- 删除 `tests/test_slot_arbiter.py` 整个文件
- `partner/application/service.py` 里 SlotArbiter 调用代码删除

**验收**：
- `pytest tests` ≥ 590 passed（删 8 个 slot_arbiter 测试）
- 不再 import SlotArbiter

#### 5.2 `assigned_instance` 改为 advisory

`JobRecord.assigned_instance` 字段保留（不改 schema），但：

- `service.py:submit()` 写 `assigned_instance = persona_hint or "02"`
- `runtime/event_worker.py` 不再读 `assigned_instance`
- 路由决策不再以 `assigned_instance` 为依据

**验收**：grep `assigned_instance` 在 `partner/runtime/` 下无读取用途。

#### 5.3 (跳过 Phase 5 的"删 _PROJECT_MARKERS / PROJECT_TO_INSTANCE / PROJECT_TITLES"步骤)

这步原本在 ADR 0099 Phase 5 里。Phase 6 之后这些常量**保留为历史常量的 fallback**——`service.py` 在 `feature_flag == "legacy"` 时还会用到。当 ADR 0100 完全稳定后（Phase 8 末）再删。

---

## Phase 6：Instance 退化为纯收发 + 共享 Worker Pool

### 目标

按 ADR 0100：instance 进程**不**起 EventWorker，只跑 QQ bridge + LLM 同步 + 写 JobRecord。
独立 worker 进程从 `state/application/jobs/` 拉 JobRecord，跑 EventFlowRunner。

### 步骤

#### 6.1 新增 `partner/runtime/shared_worker.py`

独立进程主循环：

```python
class SharedWorker:
    def __init__(self, workspace: str, slot_index: int):
        self.workspace = workspace
        self.slot_index = slot_index
        self.event_worker = EventWorker(workspace, instance_id=f"worker-{slot_index}")
    
    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            job = self._pick_next_queued_job()
            if job is None:
                self._wait_for_new_job()
                continue
            self._execute_job(job)
    
    def _pick_next_queued_job(self) -> JobRecord | None:
        # 轮询 state/application/jobs/ 找 status=queued 的
        ...
    
    def _wait_for_new_job(self) -> None:
        # 等 1 秒再轮询，避免空转
        ...
    
    def _execute_job(self, job: JobRecord) -> None:
        # 调 event_worker 跑 Flow
        self.event_worker.execute_job(job)
```

#### 6.2 写 30+ unit test

- `test_worker_picks_oldest_queued_job`
- `test_worker_skips_legacy_path_jobs`
- `test_worker_handles_no_jobs_idle_sleep`
- `test_worker_marks_running_then_completed`
- `test_worker_handles_failure`
- `test_worker_writes_artifact_to_share_projects`
- `test_worker_emits_message_via_event_flow`
- `test_worker_respects_resource_capacity`
- 等等

#### 6.3 改 `partner/runtime/instance_host.py`

```python
# 改前
class InstanceHost:
    def __init__(self, workspace: str, instance_id: str):
        self.workspace = workspace
        self.instance_id = instance_id
        self.worker = EventWorker(workspace, instance_id)
        self.bridge = None
    
    def run(self) -> None:
        # 同时跑 QQ bridge + worker
        ...

# 改后
class InstanceHost:
    def __init__(self, workspace: str, instance_id: str):
        self.workspace = workspace
        self.instance_id = instance_id
        self.bridge = None
        # 不再 self.worker = EventWorker(...)
    
    def run(self) -> None:
        # 只跑 QQ bridge
        ...
```

#### 6.4 改 `scripts/run_instance_native_runtime.py`

加 worker spawn：

```python
# 在 supervisor 启动 5 instance 之后，再起 N worker
WORKER_COUNT = 3  # Phase 7 由 ResourceScheduler 决定
for i in range(WORKER_COUNT):
    subprocess.Popen([
        sys.executable, "-m", "partner.runtime.shared_worker",
        "--workspace", str(root),
        "--slot-index", str(i),
    ])
```

#### 6.5 `partner/application/service.py` 修

- `assigned = persona_hint or "02"`（不再调 SlotArbiter）
- 删除 import `SlotArbiter`

#### 6.6 端到端验证

发 OmpA 消息给 02，验证：
- 02 立即回 Submission（< 5 秒）
- worker 之一从队列拉 JobRecord
- worker 跑 Flow 完成
- 产物写 `share/projects/<project_id>/`
- 终态消息通过 instance 推回 QQ

#### 验收

- 8 个进程（5 instance + 3 worker）都 alive
- `pytest tests` ≥ 620 passed
- OmpA 消息端到端 ≤ 90 秒完成

---

## Phase 7：Scheduler 调整 + Supervisor 改造

### 目标

ResourceScheduler 适配新的"instance + worker"拓扑。

### 步骤

#### 7.1 改 ResourceScheduler

- `effective_max_active` 现在 = worker count 而非 slot count
- instance 不再贡献 slot——它的"忙"由 QQ message throughput 衡量（advisory）

#### 7.2 `partner/governance/scheduler.py` 改

- 移除 `slot_quantum_project_steps`（instance 不再 project-coupled）
- 新加 `worker_count` 参数（默认 3，由 `config/partner_config.json` 读）

#### 7.3 supervisor 重写

按 `effective_max_active` 动态起 worker。

---

## Phase 8：端到端 canary + 文档收尾

### 目标

按 ADR 0098 的 canary 标准重跑一遍验证。

### 步骤

#### 8.1 canary 9/9 重跑

按 `decisions/0098-sprint36-pure-event-production-cutover.md` 的 canary 清单重跑：

- 5 instance alive
- 3 worker alive
- QQ 通道 connected
- 发 9 条测试消息覆盖各种 intent
- 9/9 终态正确

#### 8.2 文档更新

- `docs/catalog.yaml` 入口更新
- `docs/handoff/reading_order.md` 更新
- `docs/architecture/instance_native_runtime.md` 标记 deprecated、新增 `shared_worker_runtime.md`
- ADR 0060 / 0087 加 `Superseded by: 0099 + 0100` 注释

#### 8.3 ADR 状态

- ADR 0099: Proposed → Implemented
- ADR 0100: Proposed → Implemented
- ADR 0060 / 0087: 加 Superseded 注释

#### 验收

- 全仓回归 ≥ 620 passed
- canary 9/9 通过
- 文档同步

---

## 回滚预案

任意 Phase 失败时：

| Phase | 回滚动作 | 风险 |
|---|---|---|
| Phase 5 | 删除的代码从 git 恢复；assigned_instance 仍 advisory | 低：纯删除 |
| Phase 6 | 删除 shared_worker.py；恢复 instance_host.py EventWorker 启动 | 中：~5 个文件 |
| Phase 7 | ResourceScheduler 恢复 | 低：参数回滚 |
| Phase 8 | 文档回滚 | 极低 |

Phase 6/7 之前任意点回滚成本 < 30 分钟。

---

## 时间估算

| Phase | 工作量 | 风险 | 估计耗时 |
|---|---|---|---|
| Phase 5 | 删 slot_arbiter | 低 | 1-2 小时 |
| Phase 6 | shared_worker + instance_host + service | 中 | 4-6 小时 |
| Phase 7 | scheduler + supervisor | 低 | 1-2 小时 |
| Phase 8 | canary + docs | 低 | 1-2 小时 |

**总计**：~1-2 天。

---

## 不在本计划范围

- 嵌入 / embedding backend（tfidf vs sentence-transformer vs API）—— ADR 0100 之后单独 ADR
- GUI / TUI / Web instance 实现——非 ADR 0100 范围
