<div align="center">

# 🤝 Partner

## *"Hey Partner, what have you been doing?"*

**An AI research companion that works independently in the background.
You don't give it commands. You just check in.**

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

</div>

---

## 📦 Versions

| Version | Date | Highlights |
|---------|------|------------|
| **[v0.2.0](https://github.com/zty522/partner/releases/tag/v0.2.0)** | 2026-05-26 | 🎉 **Heartbeat Plan Model** · **QQ Official Bot** · **LLM-powered chat** · **Auto-install deps** |
| **[v0.1.0](https://github.com/zty522/partner/releases/tag/v0.1.0)** | 2026-05-24 | Event system, Conversation V2, Self-Evolution Engine |

[📥 Download Latest](https://github.com/zty522/partner/releases/latest) · [📋 All Releases](https://github.com/zty522/partner/releases)

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


**How the heartbeat works:**

```
Every 30 minutes (minimum):

    1. Read active_plan.json
    2. Plan is active? ──Yes──→ Current phase done? ──Yes──→ Advance to next phase
                               │                      └─No───→ Heartbeat only, skip
                               └─No (idle/completed)──→ Create new multi-phase plan
                                                        Execute phase 1 immediately
    3. Push QQ notification with current status
```

A single plan can span hours or days. The system never interrupts an in-progress phase — it just checks in every 30 minutes.

### 🐧 QQ Official Bot
- Integrated with the **QQ Open Platform** official API
- Supports **private (C2C)** and **group @mentions**
- Runs natively on Linux — no Windows dependency
- Auto-start in background: `partner bot start qq`
- **Every heartbeat pushes a QQ notification** showing current phase, progress, and next step

### 🧠 LLM-Powered Conversation
- QQ chat now uses LLM for natural conversation — no more rigid templates
- Context-aware (remembers last 5 exchanges)
- Concise, conversational tone — no data dumps
- Pending notifications auto-delivered on first message after idle

### 📦 Auto-Install Dependencies
- `partner setup` detects missing `aiohttp` and offers automatic installation
- `partner bot start qq` also checks deps before starting
- Scripts (`send_qq_report.py`, etc.) auto-deployed to workspace during setup
- Optional deps no longer a footgun — two-tier fallback: direct pip → extra → manual

### 🎯 Streamlined CLI
```
partner setup      Configure everything (Agent + QQ bot + auto-start)
partner status     View full status (research stats + bot health)
partner bot        Start / stop bots
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

## Quick Start

```bash
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .
partner setup
```

The setup wizard detects installed agents (Hermes, Codex, Claude Code), configures a workspace, and sets up QQ bot integration.

```bash
partner setup           # First-time configuration
partner status          # Check everything
partner bot start qq    # Start QQ bot in background
```

### QQ Bot Setup

1. Go to [q.qq.com](https://q.qq.com/) and register as a developer
2. Create a bot application → get **AppID** + **AppSecret**
3. Enable **C2C messages** and **Group @mentions** in the dev console
4. Run `partner setup` and enter credentials (aiohttp auto-installed if missing)
5. Run `partner bot start qq`

Then open QQ, find your bot, and start chatting:

```
You:       Hey Partner, what have you been doing?
QQ Bot:    🔵 Pushing age prediction project — phase 2/5: implementing ComBat batch correction
           [3/5] Code implementation → modifying data_loader.py
```

---

## How It Works

### Architecture

```
┌──────────────────────────────────────────┐
│            You (the researcher)           │
│   "Hey Partner, what have you been doing?"│
└──────────────────┬───────────────────────┘
                   ↕ QQ / Agent CLI
┌──────────────────┴───────────────────────┐
│              🤝 Partner                   │
│  ┌──────────────┐ ┌──────────────────┐   │
│  │  Active Plan  │ │  Heartbeat Cycle  │   │
│  │ (multi-phase) │ │  (30-min cron)   │   │
│  └──────────────┘ └──────────────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │Knowledge │ │  Journal  │ │  State   │ │
│  │   Base   │ │  System   │ │ Manager  │ │
│  └──────────┘ └──────────┘ └──────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │QQ Bridge │ │   CLI    │ │  Agent   │ │
│  │(Official)│ │          │ │ Adapter  │ │
│  └──────────┘ └──────────┘ └──────────┘ │
└──────────────────┬───────────────────────┘
                   ↕ LLM-powered chat
┌──────────────────┴───────────────────────┐
│  QQ Bot Platform (api.sgroup.qq.com)      │
│  WebSocket · REST API · Hermes Backend    │
└──────────────────────────────────────────┘
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

## Commands

```bash
partner setup           # Configure everything (Agent + QQ + auto-start)
partner status          # View full status (research + bot health)
partner bot start qq    # Start QQ bot in background
partner bot stop qq     # Stop QQ bot
```

That's it. Partner talks to you through QQ or your agent.

---

## Supported Agents

| Agent | Status | Notes |
|-------|--------|-------|
| 🔮 [Hermes Agent](https://hermes-agent.nousresearch.com) | ✅ Full support | Skills + cron + LLM chat |
| 🦞 [OpenClaw](https://github.com/openclaw/openclaw) | ✅ Supported | Gateway API integration |
| ⚡ [OpenAI Codex](https://openai.com/codex) | ✅ Supported | CLI integration |
| 🧠 [Claude Code](https://claude.ai/code) | ✅ Supported | CLI integration |
| 📌 Direct mode | ✅ Built-in | No external agent needed |

---

## Events vs Plans (Conceptual Shift)

Partner v0.1 introduced **Events** — structured templates with predefined phases (literature_search → extraction → synthesis). Events were good for one-shot research cycles.

Partner v0.2 introduces **Plans** — the evolution of Events. A Plan is a continuous, multi-phase push on a single project. Key differences:

| Aspect | Event (v0.1) | Plan (v0.2) |
|--------|--------------|-------------|
| Scope | One complete research cycle | Continuous project drive |
| Duration | Fixed TTL (48h max) | Unlimited — runs until goal achieved |
| Phases | Predefined by template | Dynamic, LLM-created per goal |
| Heartbeat | N/A — run once and done | Every 30-min check — advance or let continue |
| Interruption | Never checked if already running | Detects active plan → no overwrite |
| QQ Notification | Only on completion | Every 30-min heartbeat with progress |

The old `task_queue.json` is still supported for backward compatibility, but the primary execution driver is now `active_plan.json` with the heartbeat model.

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
