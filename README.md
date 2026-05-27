<div align="center">

# 🤝 Partner

## *"Hey Partner, what have you been doing?"*

**An AI research companion that works independently in the background.
You don't give it commands. You just check in.**

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

## 🚀 One-Click Install

### Windows (easiest)
```
1. Go to https://github.com/zty522/partner/releases/latest
2. Download Partner-v0.3.0-Setup.exe
3. Double-click → follow the wizard → done
```
The installer detects Python (downloads if missing), detects existing Hermes/Partner installations, and lets you choose an AI backend in a proper setup window.

### Linux
```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
```

During install you'll pick an AI backend:
- **1** — Hermes Agent (recommended)
- **2** — OpenClaw
- **3** — Both
- **4** — Skip, I'll configure later

---

## 📦 Versions

| Version | Date | Highlights |
|---------|------|------------|
| **[v0.4.0](https://github.com/zty522/partner/releases/tag/v0.4.0)** | 2026-05-27 | 🧬 **Instant QQ reply** · **Self-Evolution Engine** · **Cron planner upgrade** · **NapCat support** · **SOUL.md** |
| **[v0.3.0](https://github.com/zty522/partner/releases/tag/v0.3.0)** | 2026-05-26 | 🎉 **One-click install** · **Codex/OpenClaw integration** · **Windows GUI** · **Inno Setup installer** |
| **[v0.2.0](https://github.com/zty522/partner/releases/tag/v0.2.0)** | 2026-05-26 | 💓 **Heartbeat Plan Model** · **QQ Official Bot** · **LLM chat** · **Watchdog** |
| **[v0.1.0](https://github.com/zty522/partner/releases/tag/v0.1.0)** | 2026-05-24 | Event system, Conversation V2, proto Self-Evolution Engine |

[📥 Download Latest](https://github.com/zty522/partner/releases/latest) · [📋 All Releases](https://github.com/zty522/partner/releases)

---

## 🧬 The Self-Evolution Engine

Partner's core differentiator is a **Execute → Extract → Optimize** closed-loop that has been present since v0.1.0 and matures with every version:

```text
Execute:   Run research cycles — read literature, write code, run experiments
Extract:   After each successful task, capture the workflow as a reusable pattern
Optimize:  Apply learned strategies, prune stale knowledge, detect capability degradation
```

Every time Partner solves a problem — say, diagnosing a batch effect in age prediction transcriptomics — it captures the diagnostic methodology and stores it as a retrievable pattern. Next time a similar issue arises, it doesn't start from scratch.

**The three-layer system** (v0.4.0):

| Layer | Component | What it does |
|-------|-----------|-------------|
| 1 | **StrategyLearner** | Analyzes journal → profiles each task type by success rate, value, execution count → auto-adjusts priority boosts |
| 2 | **MemoryPruner** | Archives low-confidence stale entries, merges duplicates, promotes high-value entries |
| 3 | **CPEGuard** | Checks all capabilities for degradation trends → auto-increases verification frequency |

See [SOUL.md](SOUL.md) for Partner's behavioral creed, and [ARCHITECTURE.md](ARCHITECTURE.md) for the full evolution log.

---

## What's New in v0.4.0

### 🧬 Self-Evolution Engine (3-Layer)
Runs every 2 hours via dedicated cron job (`partner-self-evolution`):
- **StrategyLearner**: profiles task types by success rate, value, and execution count
- **MemoryPruner**: cleans stale/duplicate knowledge, promotes high-value entries
- **CPEGuard**: detects capability degradation before it becomes a problem

### ⚡ Instant QQ Reply
Messages are now processed in two steps:
1. **Immediately**: "请等待，正在思考..." (instant feedback)
2. **Background**: full LLM processing, then actual reply replaces the placeholder
No more long pauses or "请求超时" on QQ.

### 📡 Research Planner (Cron Upgrade)
Partner is no longer a passive task executor. The cron cycle now has 5 decision branches:
- **Active plan** → execute current phase
- **Plan completed** → auto-review results, search latest literature, create continuation plan
- **Idle + queued tasks** → create plan from queue
- **Idle + empty** → scan knowledge gaps, generate new research directions
- **Planning** → create plan from queue

### 🪟 NapCat Support + Windows GUI Rewrite
- New `napcat_bridge.py`: connects NapCat OneBot (local DLL injection) to Partner's conversation engine
- GUI rewritten with GitHub Dark theme, i18n (Chinese/English), simplified QQ Bot config
- Windows CLI: `--foreground` flag for GUI integration, proper PID management

### 🐛 Bug Fixes
- **active_plan.json dual copies**: consolidated to root; send_qq_report.py now reads the correct file
- **QQ "消息被去重"**: unique msg_seq added to every outgoing message
- **Windows path backslashes**: escaped in `-c` strings to prevent Unicode errors
- **Foreground PID**: --foreground mode now writes PID file for proper bot stop
- **GUI TimeoutExpired**: treated as success (bot running = good)
- **GnuTLS install error**: HTTPS retry + SSH fallback in install.sh

---

## What's New in v0.3.0

### 🚀 One-Click Install (Windows / Linux)

| Platform | Method |
|----------|--------|
| **Windows** | Download ZIP → unzip → double-click `install.bat` → mouse only |
| **Linux** | `curl ... install.sh | bash` — auto-detects distro, installs Python/deps |

Pick from 4 backends at install time:
- **Hermes Agent** — pip install, full feature set
- **OpenClaw** — npm install, multi-channel AI assistant
- **Both** — switch between them
- **Custom** — no forced install, configure manually

### 🤖 Codex CLI Integration
Partner can delegate coding tasks to [OpenAI Codex](https://github.com/openai/codex):
- `codex exec --full-auto 'Create a Python module'` → writes code, git commits
- Verified: created `partner_helper.py` (fibonacci / is_prime / gcd), all tests passed

### 🦞 OpenClaw Integration
Partner connects to [OpenClaw](https://github.com/openclaw/openclaw) via the [ACP protocol](https://docs.openclaw.ai/cli/acp):
- `openclaw acp` — ACP bridge mode
- `openclaw agent --agent main -m 'task'` — direct delegation
- Multi-channel support (QQ, WeChat, Telegram, Discord, 20+ platforms)

### 📦 Install Script Suite
| Script | Purpose |
|--------|---------|
| `scripts/install.sh` | Linux one-click (auto-detects distro, installs Python/Git) |
| `scripts/install.ps1` | Windows PowerShell (optional venv, desktop shortcut) |
| `scripts/install.bat` | Windows double-click (simplest, no terminal needed) |
| `scripts/uninstall.sh` | Linux uninstall |

---

## What's New in v0.2.0

### 💓 Heartbeat Plan Model (Major Rearchitecture)

v0.1.0 executed isolated tasks — one literature search OR one code edit per cycle. v0.2.0 introduces **continuous multi-phase plans**:

| Before (v0.1) | After (v0.2) |
|---------------|--------------|
| Picks one isolated task, runs it, done | Creates a complete plan: literature → code → experiment → analysis → next-plan |
| 30-min fixed execution window | 30-min **minimum heartbeat** — plans can span unlimited cycles |
| Task queue of unrelated items | `active_plan.json` tracking multi-phase progress |
| Never checks if work is ongoing | Checks: "is a plan active?" → if yes, let it continue |

**v0.2.0 update (heartbeat maintenance mode):**
- Heartbeat is maintenance only: QQ comms + health check. Research tasks run independently.
- Bot Watchdog: auto-restarts if the QQ bot process dies
- Every heartbeat pushes a QQ message (removed the 60-minute inactivity threshold)

**How the heartbeat works:**
```
Every 30 minutes (minimum):

    1. Check QQ bot status (watchdog auto-restarts if dead)
    2. Check research tasks for hangs (>2h without progress = stuck)
    3. Send heartbeat report to QQ (always, no timeout)
    4. Update heartbeat.json
```

### 🐧 QQ Official Bot
- Integrated with the **QQ Open Platform** official API
- Supports **private (C2C)** and **group @mentions**
- Native Linux — no Windows dependency needed
- Auto-start in background: `partner bot start qq`
- **Every heartbeat pushes a QQ notification** with current phase, progress, next step

### 🧠 LLM-Powered Conversation
- QQ chat uses LLM for natural conversation — no more rigid templates
- Context-aware (remembers last 5 exchanges)
- Concise, conversational tone — no data dumps
- Pending notifications auto-delivered on first message after idle

### 🛡️ Bot Watchdog
QQ bot process guardian, checks every 60 seconds:
- Process alive → skip
- Process dead → auto-restart
- Cleanup on `partner bot stop qq`

### 📦 Auto-Install Dependencies
- `partner setup` detects missing `aiohttp`, offers automatic install
- `partner bot start qq` checks deps before starting
- Scripts auto-deployed to workspace during setup

### 🎯 Streamlined CLI
```
partner setup                 Configure everything
partner status                View full status
partner bot start qq          Start QQ bot
partner bot stop qq           Stop QQ bot
partner queue clear           Clear task queue
partner config set interval N Change heartbeat interval (minutes)
partner update                Pull latest code + reinstall
```

---

## The Idea

```
LLM:     You ask → It answers → Done
Agent:   You command → It executes → Waits
Partner: It works on its own → You ask "what have you been doing?" → It reports
```

Partner is **proactive**. It reads papers, explores your projects, builds a knowledge base, and proposes new ideas — all without you telling it to. When you're ready, you just ask:

> **"Hey Partner, what have you been doing?"**

And it tells you everything it discovered while you were away.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              🤝 Partner                              │
│  ┌────────────────┐ ┌────────────────────────┐      │
│  │  Active Plan    │ │  Heartbeat (30-min)    │      │
│  │  (multi-phase)  │ │  → QQ bot check       │      │
│  └────────────────┘ │  → stuck task check    │      │
│  ┌──────────┐      │  → send QQ report      │      │
│  │Knowledge │      │  → update heartbeat     │      │
│  │   Base   │      └────────────────────────┘      │
│  └──────────┘                                      │
│  ┌────────────────────────────────────────────┐    │
│  │      SELF-EVOLUTION ENGINE (v0.4.0)        │    │
│  │  StrategyLearner · MemoryPruner · CPEGuard │    │
│  └────────────────────────────────────────────┘    │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │QQ Bridge │ │   CLI    │ │ Agent Integration│   │
│  │(Official)│ │          │ │ Codex · OpenClaw │   │
│  │ NapCat   │ │          │ │ Hermes · Direct  │   │
│  └──────────┘ └──────────┘ └──────────────────┘   │
└──────────────────┬─────────────────────────────────┘
                   ↕ LLM-powered chat
┌──────────────────┴─────────────────────────────────┐
│  QQ Bot Platform (api.sgroup.qq.com)                │
│  WebSocket · REST API · Hermes/OpenClaw Backend     │
└────────────────────────────────────────────────────┘
```

### Plan Lifecycle

A plan is a **continuous multi-phase event** that drives a project forward:

```
Phase Types:
  📚 literature_search   — Search, read, extract methods from papers
  💻 code_implementation — Modify code based on findings
  🧪 experiment          — Run experiments, capture results
  📊 analysis            — Compare, evaluate, summarize
  🗺️ planning            — Formulate next steps

Example Plan: "Push age prediction — solve batch effect"
  Phase 1 [done]       literature_search: Combat, Harmony, limma
  Phase 2 [in_progress] code_implementation: modifying data_loader.py
  Phase 3 [pending]     experiment: run ComBat correction
  Phase 4 [pending]     analysis: compare MAE before/after
  Phase 5 [pending]     planning: next steps
```

Each phase can span multiple 30-min heartbeat cycles. The system advances phases automatically when deliverables are detected, and never interrupts an in-progress phase.

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

## Events vs Plans (Conceptual Shift)

Partner v0.1 introduced **Events** — structured templates with predefined phases (literature_search → extraction → synthesis). Events were good for one-shot research cycles.

Partner v0.2+ introduces **Plans** — the evolution of Events. A Plan is a continuous, multi-phase push on a single project. Key differences:

| Aspect | Event (v0.1) | Plan (v0.2+) |
|--------|--------------|-------------|
| Scope | One complete research cycle | Continuous project drive |
| Duration | Fixed TTL (48h max) | Unlimited — runs until goal achieved |
| Phases | Predefined by template | Dynamic, LLM-created per goal |
| Heartbeat | N/A — run once and done | Every 30-min check — advance or let continue |
| Interruption | Never checked if already running | Detects active plan → no overwrite |
| QQ Notification | Only on completion | Every 30-min heartbeat with progress |

The old `task_queue.json` is still supported for backward compatibility, but the primary execution driver is now `active_plan.json` with the heartbeat model.

---

## Commands

```bash
partner setup                 Configure everything
partner status                View full status (research + bot health)
partner bot start qq          Start QQ bot in background
partner bot stop qq           Stop QQ bot
partner queue clear           Clear task queue
partner config set interval N Change heartbeat interval (minutes)
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
