# Changelog

## [0.4.0] - 2026-05-29

### 🧠 主动型智能体全面升级 — 不再被动等待指令

#### Behavior: 从"被动问答"到"主动汇报"

- **重启后结构化汇报**：现在 Partner 重启时会发送包含项目名、进度、指标、计划的详细汇报，取代模糊的"我回来了"。格式：
  ✅ 系统已重启，恢复运行。
  📌 上次工作状态：[项目名称]，已执行到：[具体进度]
  📋 当前计划：[1-3 步计划]
  🕒 预计下次汇报：有实质性进展时主动发送。
- **状态查询结构化回复**：被问"在做什么"时，回复格式为：
  📊 当前研究：[项目名称]
  📈 最近成果：[指标变化]
  ⏳ 正在进行：[具体操作]
  🎯 下一步计划：[自主规划]
- **禁止反问用户**：不再说"有什么需要"或"你想让我怎么做"。缺乏信息时主动搜索知识库、对话历史或网络，然后给出提议。

#### State: `last_state.json` 持久化与自动恢复

- **新增 `state_persistence.py`**：每次研究循环处理后保存当前状态到 `last_state.json`
  - 包含：active_project、last_action、last_metrics、pending_tasks、last_dialog_summary
  - 重启时自动读取并生成结构化汇报
- **`_handle_wake_up`**：启动后读取 last_state，生成详细复工简报
- **`_handle_project`**：每轮执行后保存状态并主动推送到 QQ

#### Proactive: 空闲自动探索

- **`_handle_cron_tick` 空闲检测**：如果 Mind Pool 中没有 PROJECT 或 Curiosity 事件，自动从 knowledge.json 找出知识覆盖最弱的领域生成探索任务
- **主动推送**：空闲时向用户发送"没有进行中的项目，已自动开始探索以下方向：..."
- **`_handle_curiosity` 永不空手而归**：搜索无结果时不再跳过，而是生成试探性方案告诉用户计划做什么

#### Prompt: LLM 系统提示词升级

- **qq_official_bridge.py `_llm_chat`**：系统提示词重写为主动型
  - 强制回复包含具体项目名称、进度、指标
  - 禁止反问"有什么需要"
  - 要求用结构化格式回答状态查询
  - 不知道时必须提议下一步方案，而非说"不知道"

#### Install: Windows 一键安装

- **`scripts/install.ps1`**：支持一行命令安装
  ```powershell
  powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
  ```
  - 自动检测/下载 Python 3.10+
  - 自动检测/下载 Git
  - 克隆 Partner 仓库并安装
  - 创建桌面快捷方式
  - 添加 PATH

#### Other

- README 更新，强调主动型智能体特性
- CHANGELOG 更新

## [0.4.0-arch] - 2026-05-28

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
- **QQ 消息丢失修复** — WebSocket 断连重连后自动拉取丢失消息；消息 ID 去重缓存（5分钟 TTL）
- **TASK 指令即时回复** — PROJECT 入队后立即注入 CRON_TICK 强制研究循环处理
- **对话上下文打通** — context_broker.py：自动从对话中提取项目关键信息

#### Removed CLI
- `partner mind` command (replaced by auto-start on QQ bridge connect)
