# Changelog

## [0.4.0] - 2026-05-28

### 🧠 Mind Pool — Spontaneous Thought Architecture (Major Rearchitecture)

**Previous model**: Cron-driven task execution → ActivePlan → report via file polling → QQ push.

**New model**: asyncio PriorityQueue of "Mind Events" — spontaneous impulses that arise from internal state, not external cron. Cron only injects a `CRON_TICK` pulse; the mind decides what to do.

#### New: `partner/mind/` package (5 files, ~850 lines)
- **`event_types.py`**: 10 event types (Curiosity, Report, Correction, SelfReflection, DiaryWrite, CronTick, UserMessage, Inspiration, Project, Evolution) + factory functions
- **`pool.py`**: Global `MindPool` singleton using `asyncio.PriorityQueue` + `queue.PriorityQueue` for cross-thread safe injection
- **`scheduler.py`**: `mind_loop()` — permanent async daemon that pulls highest-priority events and creates execution Tasks
- **`executor.py`**: Event dispatcher with push callback (Report events push directly to QQ via bot.send_message, bypassing file polling)
- **`__init__.py`**: Package exports

#### Key Architectural Changes
- **Mind auto-starts with QQ bridge**: No separate `partner mind` command needed — starts in background thread when the bot connects
- **Direct QQ push**: Report events call `bot.send_message()` via registered callback — eliminates duplicate messages from file polling
- **State bootstrap on init**: Reads `active_plan.json` and `task_queue.json` on startup, knows what was happening before
- **Thread-safe cross-process pool**: MindPool.put_threadsafe() allows QQ bridge (running in separate thread) to inject UserMessage events

#### Merged Files (partner/ cleanup)
- `dialog_history.py` + `context.py` → `dialog.py`
- `event_bus.py` + `proactive_notifier.py` + `self_check.py` → `autocheck.py`
- `response_generator.py` → absorbed into `conversation.py`
- Removed `scripts/install.bat` (redundant, install.ps1 covers Windows)

#### Fixed
- **Duplicate QQ messages**: Report events now push via direct callback instead of file polling + poller double-delivery
- **"空闲中" misreport**: Mind reads existing state on startup, reports actual status
- **Cron no longer drives research**: Cron only injects `CRON_TICK` pulse — the mind pool decides autonomous actions

#### Removed CLI
- `partner mind` command (replaced by auto-start on QQ bridge connect)

### 🏗️ Previous v0.4.0 Changes (retained)
- **代码瘦身 33%**: 从 13,715 行减至 9,158 行
- **Event Bus 推送系统**: `event_bus.jsonl`
- **轻量自检引擎**: 3 步自检（知识冲突、卡死、数据泄漏）
- **即时 QQ 回复**: 两步处理（placeholder → actual reply）
