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
| **[v0.2.0](https://github.com/zty522/partner/releases/tag/v0.2.0)** | 2026-05-25 | 🎉 **QQ Official Bot** · **LLM-powered chat** · **CLI revamp** · **Project-based workspace** |
| **[v0.1.0](https://github.com/zty522/partner/releases/tag/v0.1.0)** | 2026-05-24 | Event system, Conversation V2, Self-Evolution Engine |

[📥 Download Latest](https://github.com/zty522/partner/releases/latest) · [📋 All Releases](https://github.com/zty522/partner/releases)

---

## What's New in v0.2.0

### 🐧 QQ Official Bot
- Integrated with the **QQ Open Platform** official API (replaces the old NapCat hack)
- Supports **private (C2C)** and **group @mentions**
- Runs natively on Linux — no Windows dependency
- Auto-start in background: `partner bot start qq`

### 🧠 LLM-Powered Conversation
- QQ chat now uses **Hermes LLM** for natural conversation — no more rigid templates
- Context-aware (remembers last 5 exchanges)
- Concise, conversational tone — no data dumps

### 🎯 Streamlined CLI
```
partner setup      Configure everything (Agent + QQ bot + auto-start)
partner status     View full status (research stats + bot health)
partner bot        Start / stop bots
```

### 📁 Project-Based Workspace
```
workspace/
├── projects/
│   ├── age_prediction/  code/ ideas/ notes/ dialogue/ data/
│   ├── cytobridge/
│   ├── ligand_design/
│   └── partner/
├── dialogue/           Daily conversation logs (.log)
├── journal/            Daily summary & reflection logs (.log)
├── knowledge/          Shared knowledge base
└── state/              Partner runtime state
```
- **Non-destructive** daily auto-organization (4 AM cron)
- Standardized file naming: `type_topic_serial_YYYYMMDD.ext`
- Old versions auto-archived — full history traceable

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
4. Run `partner setup` and enter credentials
5. Run `partner bot start qq`

Then open QQ, find your bot, and start chatting:

```
You:       Hey Partner, what have you been doing?
QQ Bot:    Just wrapped up a big release — added official QQ bot support, 
           revamped the CLI, and reorganized the workspace. What's up?
```

---

## How It Works

```
┌──────────────────────────────────────────┐
│            You (the researcher)           │
│   "Hey Partner, what have you been doing?"│
└──────────────────┬───────────────────────┘
                   ↕ QQ / Agent CLI
┌──────────────────┴───────────────────────┐
│              🤝 Partner                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │   Task   │ │Knowledge │ │  Journal  │ │
│  │  Queue   │ │   Base   │ │  System   │ │
│  └──────────┘ └──────────┘ └──────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │Scheduler │ │  State   │ │  Agent    │ │
│  │ (Cron)   │ │ Manager  │ │ Adapter   │ │
│  └──────────┘ └──────────┘ └──────────┘ │
└──────────────────┬───────────────────────┘
                   ↕ LLM-powered chat
┌──────────────────┴───────────────────────┐
│  QQ Bot Platform (api.sgroup.qq.com)      │
│  WebSocket · REST API · Hermes Backend    │
└──────────────────────────────────────────┘
```

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

## Events: The Heart of Partner

An **Event** is one complete research cycle. Events grow on their own — one Event's findings automatically spawn new Events.

| Template | What it does |
|----------|-------------|
| `literature-deep-dive` | Search, read, and synthesize papers on a topic |
| `project-audit` | Analyze a codebase, find improvements |
| `idea-brainstorm` | Generate research ideas from accumulated knowledge |
| `cross-pollination` | Find connections between different projects |

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
