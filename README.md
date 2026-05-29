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

# Windows (one-liner, silent install)
powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
```

### First Run

On first launch, Partner will ask you to choose a language:

```
Welcome to Partner! Please choose your language:
1. English (default)
2. 中文
```

You can also change the language at any time by sending `/lang en` or `/lang zh` in QQ.

### Commands

```bash
partner setup                 Configure everything
partner status                View full status (research + bot health)
partner bot start qq          Start QQ bot + autonomous mind system (auto-starts)
partner bot stop qq           Stop QQ bot
partner update                Pull latest code + reinstall
---

## Checking Records

During research, Partner automatically saves findings to `~/.partner/20_records/`:

| File | Format | Description |
|------|--------|-------------|
| `projects/{name}/exploration_log.md` | Markdown | Human-readable exploration history with timestamps |
| `projects/{name}/knowledge.json` | JSON | Structured knowledge entries with confidence scores |
| `projects/{name}/experiments.csv` | CSV | Experiment parameters and metric results |
| `global_knowledge.json` | JSON | Cross-project knowledge |
| `session_history.jsonl` | JSONL | Session summaries |

You can view the latest records by sending `/summary` in QQ. The bot will reply with the 5 most recent exploration entries.

---

## Directory Structure

Partner organizes all data under `~/.partner/`:

```
~/.partner/
├── 00_config/          # Configuration files (partner_config.json, qq_config.json)
├── 10_logs/            # Log files (research_loop.log, qq_bridge.log)
├── 20_records/         # 🔥 CORE — user supervision and traceability entry point
│   ├── projects/       # Organized by project
│   │   └── age_pred_v2/
│   │       ├── exploration_log.md  # Exploration history (Markdown)
│   │       ├── knowledge.json      # Structured knowledge entries
│   │       ├── experiments.csv     # Experiment parameters and results
│   │       └── artifacts/          # Output files
│   ├── global_knowledge.json
│   └── session_history.jsonl
└── 99_temp/            # Temporary files (safe to clean)
```

The `20_records/` directory is the single entry point for supervision and traceability. All exploration, knowledge, and experiment results are stored there in human-readable formats.

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
│   ├── context_broker.py   # Dialog → Knowledge context bridge (v0.4.0)
│   ├── state_persistence.py # last_state.json save/load/format (v0.4.0)
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

## Multi-Instance Management (v0.5.0)

Partner supports running multiple independent instances, each with:
- Its own QQ bot account
- Its own research direction and workspace
- Its own knowledge base, logs, and records
- Its own cron schedule

### Directory Structure

```
~/.partner/
├── global_config.json           # Global configuration
├── instances/
│   ├── default/                 # Default instance (migrated from single-instance)
│   │   ├── 00_config/           # Instance-specific config
│   │   ├── 10_logs/             # Instance logs
│   │   ├── 20_records/          # Core records
│   │   └── 99_temp/
│   ├── age_pred/                # Another instance
│   │   └── ...
│   └── drug_discovery/
│       └── ...
└── audit.log                    # Global audit log
```

### Commands

```bash
# Create a new instance
partner-manager create --id age_pred --qq-config /path/to/qq_config.json

# Start/stop/restart
partner-manager start --id age_pred
partner-manager stop --id age_pred
partner-manager restart --id age_pred

# List all instances
partner-manager list

# View logs
partner-manager logs --id age_pred --tail 50

# Systemd auto-start
partner-manager enable --id age_pred
partner-manager disable --id age_pred

# Global operations
partner-manager start --all
partner-manager stop --all
partner-manager status --watch
```

### Systemd Service Template

```bash
# Enable auto-start for an instance
partner-manager enable --id age_pred

# The service file is at ~/.config/systemd/user/partner@.service
systemctl --user start partner@age_pred.service
```

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
