# Changelog

All notable changes to Partner will be documented in this file.

## [v0.2.0] - 2026-05-26

### 🔧 Changed (v0.2.0 update)
- **心跳重构**: 心跳只做维护（QQ通信+健康检查），研究任务独立运行不受心跳约束
- **每轮必推QQ**: 移除60分钟阈值，每轮心跳自动推送消息到QQ
- **Bot Watchdog**: 启动QQ机器人时自动启动进程守护，挂了自动重启
- **Cron prompt精简**: 从144行精简为67行，只包含维护检查逻辑

### ✨ Features (original v0.2.0)

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

#### Autonomous Research Cycle
- Hermes cron-driven research execution loop
- Task queue system with priority-based scheduling
- Knowledge base with structured entries
- Journal for tracking research history
- Stats tracking for cycles and completions

#### Research Skills
- Literature search via arXiv API
- Code implementation with automated verification
- Experiment execution and logging
- Analysis with result comparison
- Planning for next steps

#### Governance & Safety
- Skill health monitoring (4D scoring)
- Hoeffding-based retirement system (later replaced)
- Environment health tracking
- Capability registry

#### Skill System
- Dynamic skill loading with health checks
- Skill retirement when performance degrades
- Knowledge-integrated skill execution
- Multi-cycle research support

### 🐛 Bug Fixes
- Fixed Unicode decode errors in JSON parsing
- Fixed task queue file locking issues
- Fixed knowledge base entry deduplication

### 📦 Infrastructure
- Initial version 0.1.0
- Basic CLI setup wizard
- Workspace initialization
- Hermes cron integration
