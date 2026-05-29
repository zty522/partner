## v0.4.1 — Execution Pipeline Overhaul

Research loop stalling, tasks queued but never consumed, promises becoming empty — all fixed.

### 🛡️ Research Loop Auto-Recovery
- **Heartbeat file**: scheduler.py writes /tmp/partner_research_heartbeat.txt every 30s for external monitoring
- **watchdog.sh**: cron-based script checks heartbeat every 1min — auto-restarts if stale for 2+ min
- **Task timeout**: _run_event_safely has a 5-min timeout; stuck tasks are cancelled
- **Consumer health check**: checks heartbeat + MindPool liveness before queuing tasks
- **TASK instant recovery**: auto-restarts consumer if dead, notifies user

### ⚡ "Just Do It" Intent + Immediate Execution
- **New EXECUTE_DIRECT intent** in router.py — matches direct execution keywords
- **direct_executor.py**: new module that skips search entirely, locates project, selects script, executes, pushes results to QQ

### 🧠 Dialog Context Takes Priority
- **context_broker.py v2**: real-time extraction on every QQ message (paths, line numbers, metrics, issues)
- **searcher.py dialog-first**: checks dialog context before academic APIs
- **executor._handle_project()**: fetches latest dialog context on every cycle

### 📤 Task Result Push
- direct_executor.py: auto-pushes result summary to QQ after execution
- executor._handle_project(): pushes to QQ after each cycle

**Full Changelog**: https://github.com/zty522/partner/compare/v0.4.0...v0.4.1
