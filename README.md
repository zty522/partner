<div align="center">

# 🤝 Partner v0.4.0

## *"Hey Partner, what have you been doing?"*

**An AI research companion that works independently in the background.
You don't give it commands. You just check in.**

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

## 🧠 v0.4.0: The Mind Pool — Spontaneous Thought, Not Scheduled Tasks

**Previous versions treated Partner as a "task executor" — cron kicks, it does one thing, reports back.**

**v0.4.0 introduces the Mind Pool: an internal stream of consciousness. Partner doesn't wait for instructions. It generates its own impulses — curiosity, inspiration, self-correction, the urge to write a diary entry — and acts on them immediately.**

### The Core Idea

Every autonomous action starts as a **Mind Event** — an atomic "I want to do this" impulse. Events are not scheduled; they *spontaneously arise* from Partner's internal state:

```
┌──────────────────────────────────────────────────┐
│              🧠 Mind Pool                        │
│  (asyncio.PriorityQueue — global singleton)      │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │CURIOSITY │  │ REPORT   │  │ SELF_REFLECT  │  │
│  │"I wonder │  │"Found    │  │"Let me check  │  │
│  │ about X" │  │ result Y"│  │ my health"    │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │CRON_TICK │  │DIARY     │  │USER_MESSAGE   │  │
│  │(pulse)   │  │WRITE     │  │(from QQ)      │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
└──────────────────────┬───────────────────────────┘
                       │
              ┌────────▼────────┐
              │   mind_loop()   │
              │  (async daemon) │
              └────────┬────────┘
                       │
    ┌──────────────────┼──────────────────┐
    ▼                  ▼                  ▼
┌────────┐       ┌────────┐       ┌──────────┐
│Search  │       │Push to │       │Self-Check│
│+Summarize│     │QQ Bot  │       │+ Logging │
└────────┘       └────────┘       └──────────┘
```

### Event Types (10 types, all spontaneous)

| Type | Priority | Trigger | What Happens |
|------|----------|---------|-------------|
| **CURIOSITY** | 5 (default) | Cron tick, user question, knowledge gap | Searches knowledge base + web, generates REPORT |
| **REPORT** | 3 (urgent) | Curiosity result, self-check finding | Pushes directly to QQ via callback (bypasses file polling) |
| **CRON_TICK** | 10 (lowest) | External cron pulse | Injects periodic impulses: curiosity, diary, self-reflection |
| **USER_MESSAGE** | 1 (highest) | QQ message received | Derives CURIOSITY from questions, recorded in journal |
| **SELF_REFLECTION** | 7 | Cron tick (every 2h) | Runs SelfChecker (knowledge conflicts, stuck tasks, data leakage) |
| **DIARY_WRITE** | 8 | Cron tick (23:00) | Writes daily summary to journal |
| **CORRECTION** | 2 | Executor failure | Logs error, future: auto-retry with adjusted strategy |
| **INSPIRATION** | 5 | Knowledge gap detected | (Reserved) generates new research directions |
| **PROJECT** | 5 | User instruction | Multi-step long-running task (replaces ActivePlan) |
| **EVOLUTION** | 7 | Schedule | (Reserved) self-modification of prompts/strategies |

### What Changed from v0.3

#### Removed
- **Cron-driven execution**: No more `partner-research` cron job controlling what Partner does
- **notification file polling**: Report events push directly to QQ via registered callback
- **ActivePlan as primary driver**: Plans are now just one type of event (PROJECT), not the entire system
- `partner mind` CLI command: Mind system auto-starts with the QQ bridge

#### Added
- **`partner/mind/` package** (5 files): event_types, pool, scheduler, executor
- **Global MindPool singleton**: Thread-safe, cross-thread `asyncio.PriorityQueue`
- **Push callback**: Report events call `bot.send_message()` directly — no more duplicate messages
- **State bootstrap**: Mind reads `active_plan.json` and `task_queue.json` on startup, knows what was happening
- **Automatic mind start**: Mind loop starts in background thread when QQ bridge connects

#### Fixed
- **Duplicate QQ messages**: Report events now push via callback, not file polling + poller
- **"空闲中" misreport**: Mind reads existing state on init, continues unfinished work
- **Cron no longer drives research**: Cron only injects `CRON_TICK` — the mind decides what to do

---

## 🚀 Quick Start

### Windows
```bash
partner setup              # Configure QQ bot credentials
partner bot start qq       # Start QQ bot + Mind system (automatic)
```

### Linux
```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
partner setup
partner bot start qq
```

**Mind starts automatically** when the QQ bot connects. No separate command needed.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    🤝 Partner v0.4                       │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │           🧠 Mind Pool + mind_loop()             │   │
│  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │   │
│  │  │Cur.  │ │Rpt   │ │Refl  │ │Cron  │ │User  │  │   │
│  │  │iosity│ │ort   │ │ect   │ │Tick  │ │Msg   │  │   │
│  │  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘  │   │
│  └──────────────────────┬──────────────────────────┘   │
│                         │                              │
│  ┌──────────────────────┴──────────────────────────┐   │
│  │              Executor (dispatch)                 │   │
│  │  search → summarize → push / check / log         │   │
│  └──────────────────────┬──────────────────────────┘   │
│                         │                              │
│  ┌──────────────────────┴──────────────────────────┐   │
│  │  Knowledge Base  │  Journal  │  Task Queue       │   │
│  │  (knowledge.json)│(journal. │  (task_queue.json) │   │
│  │                  │ jsonl)   │                    │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  QQ Bridge   │  │     CLI      │  │ Agent Adapter│  │
│  │  (Official)  │  │              │  │ (Hermes/etc) │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### File Structure (post-merge)

```
partner/
├── mind/                  # NEW: Mind Pool system
│   ├── __init__.py
│   ├── event_types.py     # 10 event types + factory functions
│   ├── pool.py            # Global PriorityQueue singleton
│   ├── scheduler.py       # Async mind_loop()
│   └── executor.py        # Event dispatcher + push callback
├── dialog.py              # Merged: dialog_history + context
├── autocheck.py           # Merged: event_bus + self_check + notifier
├── conversation.py        # Absorbed: response_generator
├── core.py                # Partner class (Mind integration)
├── qq_official_bridge.py  # Auto-starts Mind on bot connect
├── cli.py                 # No more 'partner mind' command
├── config.py
├── router.py
├── active_plan.py
├── state.py / task_queue.py / knowledge.py / journal.py
├── adapter.py
├── user_prefs.py
├── workspace_manager.py
└── ... (22 .py files total, down from 26)
```

---

## Commands

```bash
partner setup                 Configure everything
partner status                View full status (research + bot health)
partner bot start qq          Start QQ bot + Mind (auto-start)
partner bot stop qq           Stop QQ bot + Mind
partner queue clear           Clear task queue
partner config set interval N Change cron tick interval (minutes)
partner update                Pull latest code + reinstall
```

---

## SOUL.md

Partner's behavioral creed — read it at [SOUL.md](SOUL.md). Seven core principles:

1. **Proactive, Not Reactive** — no idle cycles
2. **We Remember What Matters** — sustained attention across sessions
3. **Self-Correction Is a Core Competency** — name failure, pivot fast
4. **We Learn by Doing** — every execution extracts a pattern
5. **Depth Over Breadth** — one deep finding > 100 shallow searches
6. **Honest Communication** — candor over presentation
7. **Partnership, Not Service** — "Here's what I found. Here's what we should do next."

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
