<div align="center">

# 🤝 Partner

## *"Hey Partner, what have you been doing?"*

**An AI research companion that works independently in the background.
You don't give it commands. You just check in.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

</div>

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

The setup wizard detects your installed agents (Hermes, Codex, Claude Code), configures a workspace, and registers Partner as a skill. Then just talk naturally:

```
You:       Hey Partner, what have you been doing?

Partner:   📊 Here's what I've been up to:

             ⏱  Completed 12 research cycles
             📋 Finished 8 tasks
             📚 Built up 15 knowledge entries

           📖 Recent activity:
             1. Searched for latest age prediction methods
                → Found OMICmAge (Nature Aging 2024), MAE < 5 years
             2. Analyzed exploration tree results
                → Best method: age-matched + age-aware correction

           🔑 Key findings:
             • scGPT needs domain fine-tuning for aging tasks
             • Diffusion models are replacing VAEs for molecule generation

           🎯 71 tasks still queued for exploration
```

---

## How It Works

```
┌──────────────────────────────────────────┐
│            You (the researcher)           │
│   "What have you been doing?"             │
└──────────────────┬───────────────────────┘
                   ↕ natural language
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
                   ↕
┌──────────────────┴───────────────────────┐
│  Agent Backend (Hermes / Codex / Claude)  │
│  Web Search · Code Execution · File Ops   │
└──────────────────────────────────────────┘
```

Partner generates its own tasks, executes them through your agent, and accumulates knowledge over time. It recovers from crashes via heartbeat + checkpoint system.



---

## Events: The Heart of Partner

An **Event** is one complete research cycle. Like how Agents have Skills, Partner has Events.

```
┌─────────────────────────────────────────┐
│              One Event                   │
│                                          │
│  📖 Literature    → Search papers        │
│  🔬 Project Scan  → Analyze your code    │
│  💡 Idea Generate → Propose improvements │
│  🧭 Exploration   → Try new directions   │
│  📝 Knowledge     → Record findings      │
│  🌱 Spawn         → Create new Events    │
└─────────────────────────────────────────┘
```

Events **grow on their own** — one Event's findings automatically spawn new Events. The research never stops.

### Event Templates

| Template | What it does |
|----------|-------------|
| `literature-deep-dive` | Search, read, and synthesize papers on a topic |
| `project-audit` | Analyze a codebase, find improvements |
| `idea-brainstorm` | Generate research ideas from accumulated knowledge |
| `cross-pollination` | Find connections between different projects |

Users can define custom Event templates — share them with the community, like Agent skills.

---

## Commands

```bash
partner          # Guide to start talking
partner setup    # First-time configuration (or reconfigure)
partner status   # Check research progress
```

That's it. All conversation happens through your agent.

---

## Supported Agents

| Agent | Status |
|-------|--------|
| 🔮 [Hermes Agent](https://hermes-agent.nousresearch.com) | ✅ Supported |
| ⚡ [OpenAI Codex](https://openai.com/codex) | 🔜 Coming soon |
| 🧠 [Claude Code](https://claude.ai/code) | 🔜 Coming soon |

---

## Why Partner?

| | LLM | Agent | **Partner** |
|---|-----|-------|-------------|
| Needs your input | ✅ Every time | ✅ Every task | ❌ Works on its own |
| Learns over time | ❌ | ❌ | ✅ Growing knowledge base |
| Survives restarts | ❌ | ❌ | ✅ Crash recovery |
| "What have you done?" | ❌ | ❌ | ✅ **Core feature** |

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
