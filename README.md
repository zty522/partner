<div align="center">

# 🤝 Partner

## *"Hey Partner, what have you been doing?"*

**An AI research companion that works independently in the background.
You don't give it commands. You just check in.**

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

## The Idea

```
LLM:     You ask → It answers → Done
Agent:   You command → It executes → Waits
Partner: It works on its own → You ask "what have you been doing?" → It reports
```

**Partner is proactive.** It reads papers, explores your projects, builds a knowledge base, and proposes new ideas — all without you telling it to. When you're ready, you just ask:

> **"Hey Partner, what have you been doing?"**

And it tells you everything it discovered while you were away.

---

## Quick Start

```bash
# Linux
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash

# Windows — download the installer from GitHub Releases
```

### Commands

```bash
partner setup                 Configure everything
partner status                View full status (research + bot health)
partner bot start qq          Start QQ bot + autonomous mind system (auto-starts)
partner bot stop qq           Stop QQ bot
partner update                Pull latest code + reinstall
```

---

## Core Architecture

Partner is built on a **Mind Pool** — an `asyncio.PriorityQueue` of spontaneous "thought impulses" that drives all autonomous behavior. No cron scheduler, no file-based task queue, no `active_plan.json`.

```
┌─────────────────────────────────────────────────────────────┐
│                    🤝 Partner v0.4                          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              🧠 Mind Pool + mind_loop()              │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │   │
│  │  │Curiosity │ │ Project  │ │  Report  │ │CronTick│  │   │
│  │  │(explore) │ │(long-term)│ │  (push)  │ │(pulse) │  │   │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘  │   │
│  │       │            │            │            │       │   │
│  │       ▼            ▼            ▼            ▼       │   │
│  │  ┌──────────────────────────────────────────────┐    │   │
│  │  │  Waiting Room (wake_after delayed events)    │    │   │
│  │  └──────────────────────────────────────────────┘    │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │                                  │
│  ┌──────────────────────┴──────────────────────────────┐   │
│  │  Searcher (Semantic Scholar / Crossref / ArXiv)     │   │
│  │  Knowledge Base │ Journal │ Self-Checker            │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  QQ Bridge   │  │     CLI      │  │  Agent Adapter   │  │
│  │  (Official)  │  │              │  │  (Hermes/Direct) │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### How It Works

1. **Events are spontaneous impulses** — 10 types including Curiosity, Project, Report, CronTick, UserMessage, SelfReflection, etc. They arise from internal state.
2. **Mind Pool** — `asyncio.PriorityQueue` collects all events. Priority determines execution order. Supports `wake_after` for delayed execution (events stay in a "waiting room" until their time comes).
3. **mind_loop()** — permanent async daemon that pulls the highest-priority due event and spawns an execution task.
4. **Self-pulse** — every 15 minutes the system injects a `CRON_TICK` which generates a Curiosity to explore knowledge gaps.
5. **Project events** — user research requests become Project events that persist in the pool. Each execution generates a Curiosity sub-event, then re-queues itself with a 15-minute delay (`wake_after`). No `active_plan.json`, no cron dependency.
6. **Reports push instantly** — Report events call `bot.send_message()` directly via callback. No file polling, no duplicates.
7. **Academic search** — `searcher.py` uses Python `requests` to call Semantic Scholar API directly. No shell subprocesses, no `curl | python` pipelines, no security software blocking. Falls back through Crossref → ArXiv automatically.

### Event Types

| Type | Priority | When | What Happens |
|------|----------|------|-------------|
| **WakeUp** | 1 | Bot startup / restart | Restores project state, generates recovery report |
| **UserMessage** | 1 | QQ message received | Feeds into Mind Pool + gets instant "思考中，请等待..." reply |
| **Correction** | 2 | Executor failure | Logs error for future improvement |
| **Report** | 3 | Curiosity / SelfCheck result | Pushes to QQ immediately via direct callback |
| **Curiosity** | 5 | Cron tick, user question, knowledge gap | Searches academic APIs (S2/Crossref/ArXiv) via searcher.py + knowledge base |
| **Project** | 5 | User instruction (QQ/CLI) | Self-cycling event with wake_after. First pass immediate, subsequent at 5min (QQ) / 15min (auto) |
| **SelfReflection** | 7 | Every 2 hours | Runs SelfChecker |
| **DiaryWrite** | 8 | Daily at 23:00 | Logs daily summary |
| **CronTick** | 10 | Every 15 minutes (self-pulse) | Injects periodic Curiosity. Also accelerates Project events that have waited too long |

### Search Stack

```
Curiosity event
  → searcher.search(topic)
     ├─ Semantic Scholar API (free, no auth) — 30s timeout
     ├─ Crossref API (fallback) — 30s timeout
     └─ ArXiv API (final fallback)
  → Results formatted as structured text
  → LLM generates natural language report
  → Report event pushes to QQ
```

No shell subprocesses. No `curl`. No `hermes` CLI for search. Just Python `requests`.

---

## What's New in v0.4.1 — Execution Pipeline Overhaul

Research loop stalling, tasks queued but never consumed, "I'll let you know when there's progress" becoming an empty promise — all fixed.

### 🛡️ Research Loop Auto-Recovery
- **Heartbeat file**: `scheduler.py` writes `/tmp/partner_research_heartbeat.txt` every 30s for external monitoring
- **watchdog.sh**: cron-based script (every 1min) checks heartbeat file — if stale for 2+ minutes, auto-restarts `partner.service`
- **Task timeout**: `_run_event_safely` has a 5-min timeout; stuck tasks are cancelled and logged
- **Consumer health**: `_check_consumer_healthy()` checks heartbeat + MindPool instance liveness
- **TASK instant recovery**: `_queue_task` checks consumer health before queuing; if dead, auto-restarts and notifies the user

### ⚡ "Just Do It" Intent + Immediate Execution
- **New `EXECUTE_DIRECT` intent** in `router.py` — matches "直接动手", "别调研了", "自己去跑", "先跑一下", etc.
- **`direct_executor.py`** — new module that skips search entirely:
  - Locates project directory (from context_broker)
  - Selects script based on user instruction (fix leak, run experiment, try cross-features)
  - Executes in isolated subprocess with output capture
  - Pushes result summary to QQ on completion
- Integrated into QQ bridge — EXECUTE_DIRECT messages trigger DirectExecutor directly

### 🧠 Dialog Context Takes Priority
- **`context_broker.py` v2 (enhanced)**:
  - `on_user_message()` — real-time extraction on every QQ message: project paths (`/mnt/e/work/...`), line numbers (`第239行`), metrics (`MAE=7.08`), issues (`batch_correction_leak`)
  - Dual storage: `knowledge_context.json` (dedicated tracking) + `knowledge.json` (standard KB)
  - `get_context_for_search()` — retrieves relevant context from last 1 hour
- **`searcher.py` dialog-first strategy**:
  - New `set_context_provider()` registers the dialog context provider
  - `search()` checks dialog context first (if paths/line numbers/metrics exist, returns those as results) before hitting academic APIs
  - EXECUTE_DIRECT skips search entirely
- **`executor._handle_project()`**: fetches latest dialog context from context_broker on every cycle, passes to Curiosity sub-events

### 📤 Task Result Push
- **`direct_executor.py`**: auto-calls QQ push callback after execution with result summary (MAE change, leak fix, etc.)
- **`executor._handle_project()`**: pushes to QQ after each cycle with step count, issues being tracked, known metrics
- **`executor._handle_report()`**: Report events pushed via direct callback — no more file polling

---

## What's New in v0.4.0

- **Mind Pool system** — replaces old cron-driven execution with event-driven spontaneous thought
- **Project events** — replace `active_plan.json`. User research requests become self-cycling Project events with `wake_after` delay
- **Waiting room** — delayed events stay in pool until their time comes (no busy-looping)
- **`searcher.py`** — direct academic API calls (Semantic Scholar/Crossref/ArXiv). No shell subprocesses
- **Instant QQ reply** — "思考中，请等待..." sent immediately, replaced by actual response
- **No hardcoded responses** — all conversation through LLM, zero templates
- **Codebase cleanup** — 22 Python files (was 26), removed `active_plan.json`, `task_queue.json`, `send_qq_report.py` old patterns

### v0.4.0 后续修复

- 🔧 **QQ 消息丢失修复** — WebSocket 断连重连后自动拉取丢失消息；消息 ID 去重缓存（5分钟 TTL）；心跳间隔缩短至 15 秒；渐进式重连（5s → 10s）
- 💬 **TASK 指令即时回复** — 沙箱模式修复（不传 msg_id 避免 40011000）；PROJECT 入队后立即注入 CRON_TICK 强制研究循环处理；Mind 循环健康检查（池大小连续 2 次无变化则告警）
- 🧠 **对话上下文打通** — 新建 `context_broker.py`：自动从对话中提取项目关键信息（MAE、泄漏问题等）并沉淀到知识库；研究循环从 PROJECT 事件获取完整对话上下文；LLM 生成报告时使用对话背景
- 📚 **更新 README**，完善说明

---

## Supported Agents

| Agent | Status | Notes |
|-------|--------|-------|
| 🔮 [Hermes Agent](https://hermes-agent.nousresearch.com) | ✅ Full support | Skills + cron + LLM chat |
| 📌 Direct mode | ✅ Built-in | No external agent needed |

---

## SOUL.md

Partner's behavioral creed at [docs/SOUL.md](docs/SOUL.md). Seven core principles:

1. **Proactive, Not Reactive** — no idle cycles
2. **We Remember What Matters** — sustained attention across sessions
3. **Self-Correction Is a Core Competency** — name failure, pivot fast
4. **We Learn by Doing** — every execution extracts a pattern
5. **Depth Over Breadth** — one deep finding > 100 shallow searches
6. **Honest Communication** — candor over presentation
7. **Partnership, Not Service** — "Here's what I found. Here's what we should do next."

---

## Project Layout

```
partner/
├── mind/                   # Mind Pool system
│   ├── event_types.py      # 10 event types + wake_after
│   ├── pool.py             # Global PriorityQueue + waiting room
│   ├── scheduler.py        # mind_loop() async daemon
│   └── executor.py         # Event dispatcher + push callback
├── partner/
│   ├── searcher.py         # Academic search (S2/Crossref/ArXiv)
│   ├── dialog.py           # Merged: dialog_history + context
│   ├── autocheck.py        # Merged: event_bus + self_check + notifier
│   ├── conversation.py     # LLM-native conversation (no templates)
│   ├── core.py             # Partner class + Mind integration
|   ├── context_broker.py   # Dialog → Knowledge context bridge (v0.4.1)
│   ├── direct_executor.py # "Just do it" execution (v0.4.1)
│   ├── qq_official_bridge.py  # QQ bot + auto-start Mind + instant reply
│   ├── router.py           # Intent classification only (no hardcoded responses)
│   └── ... (23 files total)
├── scripts/                # Install scripts
├── installer/              # Windows installer
├── docs/                   # Documentation
├── README.md
└── CHANGELOG.md
```

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
