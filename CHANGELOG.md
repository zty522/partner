# Changelog

All notable changes to Partner will be documented in this file.

## [v0.3.0] - 2026-05-26
### 🔧 Changed
- **心跳重构**: 心跳只做维护（QQ通信+健康检查），研究任务独立运行不受心跳约束
- **每轮必推QQ**: 移除60分钟阈值，每轮心跳自动推送消息到QQ
- **Bot Watchdog**: 启动QQ机器人时自动启动进程守护，挂了自动重启
- **Cron prompt精简**: 从144行精简为67行，只包含维护检查逻辑
- **版本更新**: 0.2.0 → 0.3.0

## [v0.2.0] - 2026-05-26

### ✨ Features

#### Heartbeat Plan Model (Major Rearchitecture)
- **Continuous event-based execution**: Plans are now multi-phase "push the project forward" events (plan → literature → code → experiment → analysis → next-plan), not isolated single tasks
- **30-min heartbeat minimum**: Every cycle checks if a plan is active → if yes, let it continue; if idle, create a new complete plan and start
- **Long-running plans**: Events can span unlimited cycles; no artificial TTL bound
- **`active_plan.json`**: New state file tracking multi-phase plans with progress, current phase, and heartbeat summary
- **5 types of phases**: literature_search, code_implementation, experiment, analysis, planning

#### Heartbeat QQ Notifications
- Every heartbeat cycle pushes QQ notification via `send_qq_report.py`
- Report includes: plan status (idle/active/completed), current phase, progress [2/5], current step
- `send_qq_report.py` now reads `active_plan.json` for richer heartbeat context
- Automatic user-activity detection: pushes directly if user messaged within 60min, queues otherwise

#### QQ Official Bot Full Support
- QQ Official Bot integration (NapCat + q.qq.com official bot)
- QQ bot auto start/stop via `partner bot start qq` / `partner bot stop qq`
- Auto-reconnect on connection loss
- Per-user conversation context with LLM-powered responses
- Pending notification delivery on next user message

#### One-Click Setup
- `partner setup` interactive wizard with arrow-key selection
- Auto-detect installed agents (Hermes, Claude Code, OpenClaw, etc.)
- Auto-register Partner Hermes skill
- Auto-configure Hermes Gateway for cron
- Auto-create research cron job

#### Dependency Auto-Install
- `partner setup` now automatically checks and installs `aiohttp` when QQ bot is configured
- `partner bot start qq` also checks dependencies before starting
- Two-tier fallback: direct pip install → pip install partner-research[qq-official] → manual instructions

#### CLI Simplification
- `partner setup` — first-time configuration wizard
- `partner status` — full status overview
- `partner bot start qq` / `partner bot stop qq` — manage QQ bot

#### WSL Bridge
- Windows filesystem access from WSL
- Auto-detect Windows user directories
- Read-only mount support

### 🐛 Bug Fixes
- Fix "No module named 'aiohttp'" error on QQ bot startup — deps now auto-installed during setup
- Fix CLI working directory resolution in `_bot_start`
- Fix QQ sandbox API endpoint configuration

### 📦 Infrastructure
- Version bumped to 0.2.0
- Release tag v0.2.0
- Updated README with QQ setup instructions

---

## [v0.1.0] - 2026-05-24

### ✨ Features

#### Event System (Autonomous Research)
- Autonomous research cycles: runs tasks every 15-30 minutes
- Event templates: 8 predefined research workflows
- Knowledge gap detection: automatically identifies research gaps
- Event statistics: tracks completed, spawned, and executed phases

#### Conversation Engine V2
- Multi-turn context: remembers conversation history
- Response generator: supports "show me the 3rd one", "next page"
- Proactive notifications: alerts about important findings
- User preference learning: adapts to research style

#### Self-Evolution Engine
- Strategy learner: learns which task types succeed
- Memory pruner: cleans outdated knowledge automatically
- CPE guard: monitors core capabilities, alerts on degradation

#### Strategy Map
- DAG structure: visual research roadmap
- Fork discovery: finds new research directions automatically
- Policy selection: 5-factor scoring for next action

#### Quality Assurance
- 100+ unit tests
- Knowledge base automatic audit
- End-to-end integration tests

### 🐛 Bug Fixes
- Fix task_queue string handling (bare strings instead of dicts)
- Fix task detail printing (don't show internal info)
- Add tool restriction hook for partner skill
- Auto-configure Hermes Gateway in setup

### 📦 Infrastructure
- pyproject.toml: support `pip install partner`
- CHANGELOG.md: version history tracking
- release.sh: automated release script
- Auto Gateway setup: automatically installs and starts Hermes Gateway

### 🤖 Supported Agents
- 🔮 Hermes Agent (Full support)
- 🦞 OpenClaw (Supported)
- ⚡ OpenAI Codex (Supported)
- 👥 CrewAI (Supported)
- 💻 gptme (Supported)

---

## Planned

### v0.3.0
- [ ] WeChat integration (WeChatPad iPad protocol)
- [ ] OpenClaw bridge improvements
- [ ] Research agent adaptation (CytoBridge, etc.)
- [ ] Partner auto-upgrade mechanism
- [ ] Multi-bot simultaneous support (QQ + WeChat)
