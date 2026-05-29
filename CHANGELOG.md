# Changelog

## [0.4.1] - 2026-05-29

### 🚀 执行链路全面修复 — 告别空头支票

研究循环停滞、任务入队不消费、"有进展了跟你说"成为空头支票——全部修复。

#### Fixed: 研究循环停滞（自动恢复）

- **心跳文件**：`scheduler.py` 每 30 秒写入 `/tmp/partner_research_heartbeat.txt`，供外部监控。
- **watchdog 脚本**：`scripts/watchdog.sh`，由 cron 每分钟执行，心跳超过 2 分钟未更新则自动 `systemctl restart partner`。
- **任务超时保护**：`_run_event_safely` 中增加 5 分钟超时检测，超时强制取消并记录错误。
- **消费者健康检查**：`qq_official_bridge._check_consumer_healthy()` 检查心跳 + MindPool 实例存活。
- **TASK 指令即时恢复**：`_queue_task` 入队前检查消费者状态，未运行则自动重启并告知用户。

#### New: "直接动手"意图 + 立即执行

- **`router.py`**：新增 `EXECUTE_DIRECT` 意图，匹配"直接动手"、"别调研了"、"自己去跑"、"先跑一下"等关键词。
- **`direct_executor.py`**：新模块，跳过搜索直接：
  - 定位项目目录（从 context_broker 获取）
  - 根据用户指令选择脚本（修泄漏、跑实验、试交叉特征等）
  - 隔离执行 + 输出捕获
  - 完成后自动推送到 QQ
- **QQ bridge 集成**：EXECUTE_DIRECT 消息直接触发 DirectExecutor。

#### Fixed: 对话上下文→研究循环优先

- **`context_broker.py` 强化版 v2**：
  - 实时监听：`on_user_message()` 在每条 QQ 消息到达时提取项目路径、行号、指标、问题
  - 正则增强：`/mnt/e/work/...` 路径、`第239行` 行号、`MAE=7.08` 指标、`batch_correction_leak` 问题
  - 双通道保存：同时写入 `knowledge_context.json`（独立跟踪）和 `knowledge.json`（标准知识库）
  - 上下文检索：`get_context_for_search()` 检索最近 1 小时的相关知识
- **`searcher.py` 对话优先**：
  - 新增 `set_context_provider()` 注册对话上下文提供者
  - `search()` 策略：先检查对话上下文（有路径/行号/指标则直接返回），再走学术 API
  - EXECUTE_DIRECT 完全跳过搜索
- **`executor._handle_project()`**：每次执行前从 context_broker 获取最新对话上下文，传递给 Curiosity 子事件

#### New: 执行结果主动推送

- **`direct_executor.py`**：执行完成后自动调用 QQ push callback，发送结果摘要（含 MAE 变化、泄漏修复等）
- **`executor._handle_project()`**：每轮执行完成后主动推送到 QQ，包含当前步骤、关注问题、已知指标
- **`executor._handle_report()`**：Report 事件通过直接回调推送，不再依赖文件轮询

#### Other

- 新增 `scripts/watchdog.sh` 安装说明到 README
- CHANGELOG 本次更新详解

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
