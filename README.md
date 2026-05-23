<div align="center">

# 🤝 Partner

### Your AI Research Companion

**LLM generates text. Agent executes tasks. Partner does research — on its own.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

</div>

---

## What is Partner?

Partner is a new kind of AI system — an **autonomous research entity** that works independently in the background. It reads papers, explores your projects, builds knowledge, and proposes new ideas. All on its own.

The best part? You can just ask:

> **"Hey Partner, what have you been doing?"**

And it tells you everything it discovered while you were away.

```
LLM:     You ask → Model answers → Done
Agent:   You command → Agent executes → Waits for next command
Partner: Partner works on its own → You check in → Partner reports
```

**Partner is proactive, not reactive.**

---

## Quick Start

```bash
# Install
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .

# First-time setup (interactive wizard)
partner setup

# That's it! Talk to Partner through your agent.
# Just say: "partner, what have you been doing?"
```

### Setup Wizard

```
  🤝 Partner v0.1.0
  Your AI Research Companion
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔍 Detecting installed Agents
────────────────────────────────────────────────
    ✓ 🔮 Hermes Agent  /home/you/.local/bin/hermes
    ✓ ⚡ OpenAI Codex  /usr/bin/codex
    ✗ 🧠 Claude Code   not installed

  ⚙️  Select Agent Backend
────────────────────────────────────────────────
    ▶ 1. 🔮 Hermes Agent
     · 2. ⚡ OpenAI Codex

  📂 Creating Workspace
────────────────────────────────────────────────
    ✓ Workspace: ~/partner_workspace
    ✓ State files initialized

  🧩 Registering Partner Skill
────────────────────────────────────────────────
    ✓ Skill registered

  🎉 Partner is ready!
```

---

## How to Use

Partner talks through your existing agent. No new UI to learn.

| You say | Partner does |
|---------|-------------|
| "partner, what have you been doing?" | Reports its recent research activity |
| "partner, what do you know about X?" | Searches its knowledge base |
| "partner, go research X" | Adds a new research task |
| "pause X, partner should focus on Y" | Adjusts research direction |
| `partner status` | Quick status check via CLI |

---

## How It Works

```
You (the researcher)
        ↕ natural language through your agent
   ┌─────────────┐
   │   Partner    │ ← autonomous research loop
   │   ┌───────┐  │
   │   │ Tasks  │  │ ← self-generated + user-injected
   │   │Knowledge│ │ ← growing knowledge base
   │   │ Journal │ │ ← activity log
   │   └───────┘  │
   └──────┬──────┘
          ↕
   Agent Backend (Hermes, Claude Code, Codex)
          ↕
   Workspace (files, state, checkpoints)
```

1. **Partner starts** and loads its state
2. **Every N minutes**, it wakes up and picks the highest priority task
3. **Executes the task** via the agent backend (search literature, analyze code, etc.)
4. **Records findings** in the knowledge base and journal
5. **Generates new tasks** based on what it learned
6. **Repeats** — forever, until you tell it to stop

---

## Architecture

Partner sits **on top of** existing agent frameworks, like how agents sit on top of LLMs:

```
┌─────────────────────────────────────────┐
│  Partner (research entity layer)         │
│  Task Queue · Knowledge · Journal        │
├─────────────────────────────────────────┤
│  Agent Backend (Hermes / Claude / Codex) │
│  Web Search · File Ops · Code Execution  │
├─────────────────────────────────────────┤
│  LLM (GPT / Claude / DeepSeek / etc.)   │
└─────────────────────────────────────────┘
```

You configure which agent to use during `partner setup`. Partner then uses that agent's capabilities (web search, code execution, file operations) to conduct research autonomously.

---

## Comparison

| Feature | LLM | Agent | **Partner** |
|---------|-----|-------|-------------|
| Needs user input | ✅ Every time | ✅ Every task | ❌ Works on its own |
| Accumulates knowledge | ❌ | ❌ | ✅ Growing KB |
| Persistent | ❌ Per session | ❌ Per task | ✅ Continuous |
| Crash recovery | ❌ | ❌ | ✅ Auto-recover |
| "What have you done?" | ❌ | ❌ | ✅ Core feature |

---

## Commands

```bash
partner              # Guide to start talking
partner setup        # First-time configuration (or reconfigure)
partner status       # Check Partner status and research progress
```

---

## Use Cases

- **Research labs** — Let Partner explore related work while you do experiments
- **Graduate students** — Have a research companion that never sleeps
- **Data science teams** — Automatically monitor and improve ML pipelines
- **Literature reviews** — Partner reads papers so you don't have to
- **Cross-project insights** — Partner finds connections you'd miss

---

## Supported Agents

| Agent | Status | Notes |
|-------|--------|-------|
| 🔮 [Hermes Agent](https://hermes-agent.nousresearch.com) | ✅ Supported | Full integration via skills |
| ⚡ [OpenAI Codex](https://openai.com/codex) | 🔜 Coming soon | |
| 🧠 [Claude Code](https://claude.ai/code) | 🔜 Coming soon | |

---

## License

Apache 2.0 — use it however you want.

---

<div align="center">

**Partner: because research shouldn't wait for you.**

*Built with the belief that AI should be a research companion, not just a tool.*

</div>
