# ADR 0064：runtime daemon 改为 spawn+supervise，instance 自己推进

- 状态：Accepted / Production Active
- 日期：2026-09-05
- 前置：ADR 0060（instance-native production runtime）、ADR 0061（real-action contract）、ADR 0063（adapter-error distinction + self-drive dedup）
- 触发：runtime daemon "alive 3 小时但业务没动" 的隐性 bug
- 关键文件：`scripts/run_instance_native_runtime.py`、`tests/test_runtime_spawn_architecture.py`

## 背景与动机

ADR 0063 修完了 batch_planner adapter 错误检测（Layer A）+ self-drive dedup guard（Layer B），单测 23 个全过。但用户要求"再次运行 5 个实例看看" 时发现：

- self-drive daemon 在跑（tick 0/1/2 都正确 emit 5 个 task 到 inbox）
- runtime daemon 也在跑（pid 52833，9 分多钟 idle）
- 但 5 个 instance 进程**根本没起来**——`instances/0X/instance.pid` 全是 17:10 留下的死 pid
- 5 个 instance 的 heartbeat 3 小时没更新（停在 20:08）
- agent_runs.jsonl 19:57 后没新记录
- desktop_inbox 里 self-drive 写的 5 行新消息**没人读**

## 根因：runtime 是"业务进度 watchdog"而不是"实例 orchestrator"

旧 `run_instance_native_runtime.py` 的 main 循环是：

```python
selected = reconcile_slots(...)           # 算哪些 instance 该 active
for instance_id in selected:
    recover_or_start(root, instance_id)  # 把 pending 状态调一下（**不 spawn**）
with TaskTerminalReceiver(root) as terminals:
    while not stopping:
        event = terminals.wait(max(1, args.watchdog_seconds))
        if event and authoritative_terminal_event(event):
            handle_terminal(...)         # 推进下一步
        elif not event:
            for active in active_slots:
                recover_or_start(...)    # watchdog 30s 检查
```

它**假设 instance 是独立进程在跑**，自己只做：
- pending 状态机推进
- 30 秒 watchdog 检查
- 处理 task terminal 事件

但**从来没有代码 spawn instance 子进程**——这是历史遗留 bug。之前能跑通是因为有外部 launcher（比如 cron、手动、`python -m partner`），instance 自己启动后 runtime 接收 terminal event。

**但 self-drive 写 instance 级 inbox（`instances/0X/state/desktop_inbox.jsonl`）依赖 instance 内部 inbox poller（`partner/mind/executor.py:3791` `desktop-inbox-poller` 线程）**。Instance 没起 → poller 没起 → inbox 堆积 → QQ 没回应 → 用户看到 "auto-continue" 没进度。

## 决策：runtime 改 spawn + supervise，instance 自己推进

按用户原则"应该让实例运行自动推进而不是用外部约束"，新架构：

```python
# main() 改写后
selected = _reconcile_slots(root, configured, max_active)  # 只算 selected
for instance_id in selected:
    children[instance_id] = _spawn_instance(root, instance_id, python_exe)
# Spawn 用 subprocess.Popen(..., start_new_session=True)
# 子进程是 python -m partner --instance-id 0X --workspace <root>/instances/0X

while not stopping:
    time.sleep(watchdog_seconds)
    for instance_id, proc in children.items():
        if not _is_alive(proc):
            # 子进程死了 → 带 rate-limit 重新 spawn
            ...
    new_selected = _reconcile_slots(...)
    for newly_active:
        children[id] = _spawn_instance(...)  # 新 active 的 instance 才 spawn
finally:
    _shutdown_children(children)  # SIGTERM → 10s 等待 → SIGKILL
```

每个 instance 子进程内部跑 `_run_instance_mode`（`partner/__main__.py:111`），它已经包含：
- `partner.start()` 加载 config + state
- `partner.start_mind()` 启动 event loop + **desktop inbox poller**（`partner/mind/executor.py:3710`）
- QQ bridge 后台 thread（每个 instance 自己的 bot app_id）

**Instance 自己推进业务**，runtime 只做：
1. Spawn：启动时为每个 selected instance fork 子进程
2. Supervision：watchdog 检测子进程死掉，带 60s 6 次 rate-limit 重新 spawn
3. Slot 协调：reconcile_slots 处理外部 pause/resume，spawn 新 active 的 instance
4. Shutdown：SIGTERM 优雅停 + 10s 等待 + SIGKILL 兜底

## 保留了什么

- `recover_or_start` / `handle_terminal` / `TaskTerminalReceiver` 三个函数**保留**——但 main() 不再调它们
- 原因：instance 进程自己可能还会 import 这些（arbiter 写 native_state.json 仍需要 `recover_or_start` 的状态机逻辑）；external CLI / 测试可能调用
- 旧 main 里的"TaskTerminalReceiver 监听 + handle_terminal 推进"路径**整段删除**——这是冗余的（instance 自己 event loop 已经在做）

## 验证

`tests/test_runtime_spawn_architecture.py`（7 个用例）：
- 3 个 spawn 行为（`_is_alive` true/false、`_shutdown_children` 真正终止子进程）
- 2 个 `_reconcile_slots` 契约（从空 active_slots 选满 5 个；source-level 保证不 spawn）
- 1 个 back-compat exports（handle_terminal / recover_or_start / TaskTerminalReceiver 仍导出）
- 1 个 `_shutdown_children` 幂等（Dead child 不抛 ProcessLookupError）

总测试 45/45 pass：
```
test_micro_planner_extraction.py ...............   15 passed
test_batch_planner_adapter_errors.py ...........  14 passed
test_self_drive_dedup.py .........                9 passed
test_runtime_spawn_architecture.py .......       7 passed
============================== 45 passed in 2.03s ==============================
```

**真实运行验证**（systemctl 启动 runtime 后）：
- daemon 主进程 pid 67459 + 5 instance 子进程 pid 67461-67465 全部在跑
- 5 instance heartbeat 持续更新（30s 间隔正常推进，不再卡在 20:08）
- 03 instance.out.log 显示 `Bot ready: partner03 (260012392017942262)`——QQ bridge 起来了
- desktop_inbox poller 工作（self-drive 写 5 行 → instance poller 立即消费清空）
- agent_runs.jsonl 新增真实 LLM 调用（00:51:42 batch_plan 24.6s，返回真实 JSON plan）
- event_pipeline.jsonl 新增 task_failed 记录（seq=344，错误信息 `Batch planner LLM call failed: timeout after 180s`，对比旧版误导性 `Batch planner returned invalid JSON [pos=unknown]: n`）

## 教训

1. **运行时必须真的 spawn**。一个 daemon "alive 3 小时没动业务" 比"直接 crash"更隐蔽——health check 全过但实际工作流停摆。从 ADR 0060 时代起 `recover_or_start` 就假设 instance 在外部已启动，没人补 spawn 这步。
2. **架构原则优先于历史兼容**。这次保留了 `recover_or_start` / `handle_terminal` / `TaskTerminalReceiver` 三个函数，但 main() 不再调用它们——instance 自己推进，runtime 只 supervise。如果未来 instance 真正需要这些 helper（比如 instance 自己 import state machine 工具），它们仍可用；如果没人用，下一轮 sprint 可以删。
3. **测试隔离必须考虑 process group**。`_spawn_instance` 用 `start_new_session=True` 让每个子进程独立 pgid（`os.killpg` 优雅关），单测里如果直接 Popen 不加这个 flag，pytest cleanup 会和子进程争抢 pgid 导致测试假阳性（"shutdown 0.0001s" = 子进程已被 pytest 杀掉）。测试要么同样用 `start_new_session=True`，要么用 mock 绕过真 spawn。
4. **watchdog 是反模式**。runtime 的旧 30s watchdog 在"instance 没起来 + terminals 永远阻塞"的场景下完全失效——`wait(timeout=30)` 返回后还会跑 `recover_or_start`，但 `recover_or_start` 看 native_state.json pending_message_id 非空就 return，没真做事。**架构正确的版本里，watchdog 只剩"子进程死了 respawn"这一件事**，30 秒间隔仍然有意义但范围小很多。
