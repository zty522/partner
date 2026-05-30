# Changelog

## [0.5.0] - 2026-05-30

### 🚀 新增功能

#### 🧠 Mind Pool 自动续命与健康检查修复
- **自脉冲缩至 5 分钟**: `SELF_PULSE_INTERVAL` 从 900s 改为 300s，空闲实例不再长时间无任务
- **WAKE_UP 续命**: `_handle_wake_up` 末尾检查队列为空时自动注入延迟 5 分钟的 CRON_TICK
- **健康检查误报修复**: `_ensure_mind_loop_healthy` 仅当队列 `>0` 且连续 3 次不变才报警；队列为 0 视为正常空闲
- **空闲心跳**: `mind_loop` 空闲时写入 `/tmp/partner_idle_heartbeat.txt`

#### 📂 Workspace 彻底重构（Timeline 标准记录）
- **`recorder.py` 完全重写**: timeline 中心式记录，每项目包含 `timeline.jsonl`（action/hypothesis/result/reflection/next）、`experiments.csv`、`knowledge.json`
- **`partner log` CLI 命令**: 查看项目研究时间线（`--project`、`--limit`、`--list`）
- **`partner migrate-records` CLI 命令**: 一键迁移旧版文件到 `20_records/` 标准化结构，生成 `MIGRATION.md`
- **清理冗余文件**: `active_plan.json`、`plan_archive_*.json` 等归档到 `archived_plans/`；中间状态文件移到 `10_logs/state/`

#### 🎯 智能聚焦探索（禁止泛关键词）
- **删除所有泛关键词**: 移除"最新研究进展"、"当前项目优化方向"等硬编码字符串
- **基于项目瓶颈生成搜索**: 自动从 `20_records/projects/` 读取最新项目，提取 knowledge.json 和 timeline.jsonl 中的瓶颈生成 2-3 个具体搜索查询
- **2 小时去重**: 短时记忆 `last_search_queries.json` 避免同一 query 重复搜索
- **LLM 相关性过滤**: 搜索结果经 LLM 评分（阈值 0.3），低分丢弃
- **无项目时静候**: 不搜索泛关键词，仅记录日志等待用户指令

#### 📊 Token 用量可观测性
- **`token_tracker.py`**: CSV 记录所有 LLM 调用（prompt_tokens、completion_tokens、model、project、instance）
- **`/usage` QQ 命令**: 支持 `/usage [day|week|month] [project=xxx] [instance=xxx]`
- **`partner usage` CLI 命令**: 终端查询 Token 用量统计
- **自动打点**: `adapter.py` 中所有 LLM 调用完成后自动记录 token 用量

#### 🛡️ 多实例稳定性增强
- **`core.py` 主循环健壮性**: 全局异常捕获 + 自动重启（最多 3 次/小时，退避重试 2min→4min→5min）
- **崩溃日志**: 写入 `10_logs/crash.log`，含完整 traceback
- **进程守护**: `manager.py` 的 watchdog 每 30 秒检查子进程，异常退出时自动重启
- **资源监控**: `partner-manager status --watch` 显示 CPU%、内存 MB、最后活跃时间
- **空闲检测修正**: 无任务时进入空闲等待（保持进程存活），连续空闲超过 1 小时才退出

#### 🖥️ Windows 安装体验优化
- **`install.ps1`**: pip install 成功后自动将 Python Scripts 目录添加到用户 PATH
- **PATH 刷新**: 当前会话立即生效 + 提示重启终端

### ⚙️ 改进

- Exe 安装器文件名为 `Partner-0.5.0-Setup.exe`，版本号与代码同步
- **install.ps1**: Python 版本检测放宽到 3.10+（不再限制 3.10-3.12，3.14 也能直接用）
- **install.ps1**: 修复 embeddable Python 安装 `setuptools wheel` 时警告导致安装中断的问题（`--no-warn-script-location` + `$null` 赋值替代管道）
- **多实例管理**: `partner-manager` CLI 命令 + 实例目录 + `__main__.py` 路由 + systemd 模板 + 迁移工具

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
