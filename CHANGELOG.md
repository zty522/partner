# Changelog

## [0.4.1] - 2026-05-29

### 🔧 Bugfix Release: 消息可靠性 + 上下文桥接

#### Fixed: QQ 消息丢失（WebSocket 断连）
- **心跳间隔缩短至 15 秒**（原 41s），降低断连检测延迟
- **渐进式重连**：首次 5s，后续 10s（原固定 30s）
- **消息 ID 去重缓存**（5 分钟 TTL），防止重连后重复处理
- **详细的连接状态日志**：连接耗时、断连时长、conn_id 追踪
- 沙箱模式 `msg_id` 修复：不传 msg_id 到沙箱 API，避免 40011000 错误

#### Fixed: TASK 指令静默处理
- **PROJECT 入队后立即注入 CRON_TICK**，强制 Mind 循环立即处理，不等 15 分钟自脉冲
- **Project 唤醒延迟缩减**：QQ 用户的 PROJECT 从 15 分钟缩短至 5 分钟
- **CRON_TICK 加速等待中的 PROJECT**：将等待超过 5 分钟的 PROJECT 事件优先级提高（6→4），唤醒缩短至 60 秒
- **Mind 循环健康检查**：池大小连续 2 次无变化则告警

#### New: 对话与研究循环上下文桥接
- **`context_broker.py`** 新建模块，自动从 QQ 对话历史中提取项目关键信息
  - `extract_project_facts()`: 正则提取指标（MAE=7.08）、问题（batch_correction_leak）、文件路径
  - `save_to_knowledge()`: 将对话信息沉淀到知识库
  - `get_project_context()`: 生成 LLM 可读的项目上下文文本
- **PROJECT 事件携带对话上下文**：`payload` 中增加 `dialog_context` 和 `project_facts`
- **CURIOSITY 处理时注入对话背景**：LLM 生成报告时能看到之前的聊天记录

#### Other
- 更新 README，新增 `context_broker.py` 到项目布局
- Mind Pool 事件类型表增加 WAKE_UP

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
