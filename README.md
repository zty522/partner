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

# Windows (PowerShell 5.1+, run as Administrator)
powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"

# Or download the EXE installer from GitHub Releases
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

Partner now runs on **two explicit lines**:

1. **Lifeline**: an autonomous `mind_loop` that keeps a project moving even when the user is away.
2. **Interaction line**: a lightweight LLM orchestrator that replies to the user and decides whether the lifeline should be mutated.

```
┌──────────────────────────────────────────────────────────────┐
│                       🤝 Partner v0.5                        │
│                                                              │
│  User QQ / CLI message                                       │
│        │                                                     │
│        ▼                                                     │
│  InteractionOrchestrator                                     │
│  ├─ reply_to_user                                            │
│  └─ lifeline_action (add_task / switch_project / note / kb) │
│        │                                                     │
│        ▼                                                     │
│  task_queue / state / project log / knowledge base           │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              🧠 Mind Pool + mind_loop()               │  │
│  │  WakeUp / Project / Report / CronTick / Waiting Room  │  │
│  └────────────────────────────────────────────────────────┘  │
│                     │                                        │
│                     ▼                                        │
│            Agent Adapter (Hermes / Direct)                  │
│                     │                                        │
│                     ▼                                        │
│      Structured project result → state.md + artifact file   │
│                     │                                        │
│                     ▼                                        │
│               QQ proactive progress report                  │
└──────────────────────────────────────────────────────────────┘
```

### How It Works

1. **The lifeline never waits for chat.** `mind_loop()` keeps injecting `WakeUp`, `CronTick`, `Project`, and `Report` events.
2. **User messages do not directly control the system.** They go through `InteractionOrchestrator`, which asks an LLM for:
   - a natural-language reply
   - a structured mutation decision for the lifeline
3. **The LLM does not mutate state directly.** Code applies the decision into `task_queue`, project state, logs, and knowledge records.
4. **Project execution is step-based.** Each `Project` event asks the agent for one small structured step, then re-queues itself with `wake_after`.
5. **Reports are pushed by code, not by raw agent output.** Partner assembles short user-facing summaries and sends them through the QQ bridge callback.
6. **Each instance has its own writable agent runtime.** Hermes runs with a per-instance home under the workspace, so logs, auth, and caches stay isolated.

### Event Types

The current autonomous loop keeps only four core execution events:

| Type | Purpose |
|------|---------|
| **WakeUp** | Recover the active project after start/restart |
| **Project** | Execute one concrete step for the current project |
| **Report** | Push a concise progress update to the user |
| **CronTick** | Periodic pulse that keeps the lifeline moving |

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
│   ├── event_types.py      # Core events
│   ├── pool.py             # Priority queue + waiting room
│   ├── scheduler.py        # mind_loop() async daemon
│   └── executor.py         # Project execution + structured result handling
├── partner/
│   ├── adapter.py          # Hermes / Direct adapter
│   ├── core.py             # Partner class + mind loop bootstrap
│   ├── instance_root.py    # Runtime root resolution
│   ├── interaction_orchestrator.py  # User-message decision layer
│   ├── project_state.py    # Project folders + state/log helpers
│   ├── qq_official_bridge.py  # QQ bridge + interaction line
│   ├── router.py           # Lightweight routing
│   ├── task_queue.py       # Persistent lifeline tasks
│   └── ... 
├── scripts/                # Install scripts
├── scripts/normalize_partner_workspace.py  # Workspace normalization helper
├── installer/              # Windows installer
├── docs/                   # Documentation
├── README.md
└── CHANGELOG.md
```

---


---

## Directory Structure

Partner resolves its runtime root in this order:

1. `PARTNER_HOME`
2. `~/.partner_workspace` pointer
3. an existing `partner_workspace` directory
4. `~/.partner` fallback

Under that root, each instance has an isolated workspace:

```
partner_workspace/
├── global_config.json
├── instances/
│   └── {instance_id}/
│       ├── 00_config/
│       ├── 10_logs/
│       ├── 20_records/
│       │   ├── active_project.txt
│       │   └── projects/
│       │       └── {project_name}/
│       │           ├── state.md
│       │           ├── exploration_log.md
│       │           ├── log.md
│       │           └── generated artifacts...
│       ├── logs/               # agent call traces (e.g. hermes_chat.jsonl)
│       ├── state/              # runtime state / task queue / plan
│       ├── system/
│       │   └── hermes_home/    # per-instance writable Hermes runtime
│       └── 99_temp/
```

For a user, the most important files are usually:

- `20_records/projects/<project>/state.md`
- `20_records/projects/<project>/exploration_log.md`
- generated project artifacts such as `next_experiment.md` or `evaluation_framework_outline.md`

---

## Multi-Instance Management

Partner supports multiple independent instances, each with its own QQ bot account, workspace, runtime state, agent cache, and project history.

### Commands

```bash
partner-manager create --id age_pred --qq-config /path/to/qq_config.json
partner-manager start --id age_pred
partner-manager stop --id age_pred
partner-manager restart --id age_pred
partner-manager list
partner-manager logs --id age_pred --tail 50
partner-manager start --all
partner-manager stop --all
```

The manager starts `python -m partner --instance-id <id> --workspace <path>`.
Each instance auto-starts:

- `Partner.start()`
- `Partner.start_mind()`
- QQ official bridge

### Systemd Auto-Start

```bash
partner-manager enable --id age_pred
systemctl --user start partner@age_pred.service
```

---


## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
