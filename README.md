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

Three models of AI interaction, and Partner lives in the third.

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
partner bot start qq          Start QQ bot + autonomous mind system
partner bot stop qq           Stop QQ bot
partner queue clear           Clear task queue
partner update                Pull latest code + reinstall
```

---

## Core Architecture

Partner is built on a **Mind Pool** — an internal stream of consciousness that drives all autonomous behavior.

```
┌─────────────────────────────────────────────────────────┐
│                    🤝 Partner                             │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │         🧠 Mind Pool + mind_loop()               │   │
│  │  PriorityQueue of spontaneous "thought impulses" │   │
│  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │   │
│  │  │Cur.  │ │Rpt   │ │Refl  │ │Diary │ │User  │  │   │
│  │  │iosity│ │ort   │ │ect   │ │Write │ │Msg   │  │   │
│  │  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘  │   │
│  └──────────────────────┬──────────────────────────┘   │
│                         │                              │
│  ┌──────────────────────┴──────────────────────────┐   │
│  │   Knowledge Base  │  Journal  │  Task Queue      │   │
│  │   Self-Checker    │  Event Bus│  Active Plan     │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  QQ Bridge   │  │     CLI      │  │ Agent Adapter│  │
│  │  (Official)  │  │              │  │ (Hermes/etc) │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### How It Works

1. **Events are spontaneous impulses** — Curiosity, Report, Self-Reflection, Diary Write, and more. They arise from internal state, not external cron.
2. **Mind Pool** — an `asyncio.PriorityQueue` that collects all events. Priority determines execution order (user messages first, background tasks last).
3. **mind_loop()** — a permanent async daemon that pulls the highest-priority event and spawns an execution task.
4. **Self-pulse** — every 15 minutes the system injects a `CRON_TICK` event, which triggers Curiosity (explore knowledge gaps), Self-Reflection (health check), and Diary Write (log summary).
5. **Reports push instantly** — when a Curiosity event finishes exploring, it creates a Report event that pushes directly to QQ via callback — no file polling, no duplicates.

### Event Types

| Type | Priority | When | What Happens |
|------|----------|------|-------------|
| **Curiosity** | 5 | Cron tick, user question, knowledge gap | Searches knowledge base, generates Report |
| **Report** | 3 | Curiosity result, self-check finding | Pushes to QQ immediately via direct callback |
| **Cron Tick** | 10 | Every 15 minutes (self-pulse) | Injects periodic impulses |
| **User Message** | 1 | QQ message received | Derives Curiosity from questions |
| **Self-Reflection** | 7 | Every 2 hours | Runs SelfChecker (conflicts, stuck tasks, data leaks) |
| **Diary Write** | 8 | Daily at 23:00 | Logs daily summary |
| **Correction** | 2 | Executor failure | Logs error for future improvement |
| **Inspiration** | 5 | Knowledge gap detected | Generates new research directions |
| **Project** | 5 | User instruction | Long-running multi-step task |

---

## Supported Agents

| Agent | Status | Notes |
|-------|--------|-------|
| 🔮 [Hermes Agent](https://hermes-agent.nousresearch.com) | ✅ Full support | Skills + cron + LLM chat |
| 🦞 [OpenClaw](https://github.com/openclaw/openclaw) | ✅ Supported | ACP bridge + agent delegation |
| ⚡ [OpenAI Codex](https://openai.com/codex) | ✅ Supported | `codex exec --full-auto` |
| 🧠 [Claude Code](https://claude.ai/code) | ✅ Supported | CLI integration |
| 📌 Direct mode | ✅ Built-in | No external agent needed |

---

## SOUL.md

Partner's behavioral creed — read it at [docs/SOUL.md](docs/SOUL.md). Seven core principles:

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
├── mind/                  # Mind Pool system (event_types, pool, scheduler, executor)
├── partner/               # Core modules (22 Python files)
│   ├── dialog.py          # Merged: dialog_history + context
│   ├── autocheck.py       # Merged: event_bus + self_check + notifier
│   ├── conversation.py    # Absorbed: response_generator
│   ├── core.py            # Partner class + Mind integration
│   ├── qq_official_bridge.py  # QQ bot + auto-starts Mind
│   ├── events/            # Event template definitions (archived)
│   └── ...
├── scripts/               # Install/uninstall scripts
│   ├── install.sh / install.ps1 / uninstall.sh
│   ├── send_qq_report.py  # State data collector (no notification writes)
│   └── release.sh         # Release script
├── installer/             # Windows Inno Setup installer
│   ├── installer.iss
│   └── post_install.bat
├── docs/                  # Documentation
│   ├── ARCHITECTURE.md    # Architecture evolution log
│   ├── SOUL.md            # Behavioral creed
│   └── ...
├── README.md
├── CHANGELOG.md
├── pyproject.toml
└── LICENSE
```

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
