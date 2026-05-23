---
title: "Partner: An AI That Does Research While You Sleep"
published: false
description: "LLM generates text. Agent executes tasks. Partner does research — on its own. An autonomous research entity that reads papers, explores codebases, and proposes ideas independently."
tags: ai, research, opensource, python
cover_image: https://raw.githubusercontent.com/zty522/partner/main/docs/conversation.png
---

**LLM generates text. Agent executes tasks. Partner does research — on its own.**

## The Problem

As a researcher, I have more ideas than time. Papers to read, experiments to run, code to review, and connections to find between projects. No matter how fast I work, the backlog grows.

What if I had a research companion that worked independently — reading papers, exploring my codebase, building a knowledge base, and proposing new ideas — while I focused on the hard problems?

## The Three Layers of AI

We've seen two layers of AI tools:

```
LLM:     You ask → It answers → Done
Agent:   You command → It executes → Waits
```

**LLMs** are passive — they need a prompt. **Agents** are reactive — they need a command.

I wanted something **proactive**: an AI that starts working when you turn it on, accumulates knowledge over time, and reports back when you ask.

```
Partner: It works on its own → You ask "what have you been doing?" → It reports
```

## Introducing Partner 🤝

Partner is an **autonomous research entity**. It sits on top of existing agent frameworks (like how agents sit on top of LLMs) and conducts research independently.

The core interaction is beautifully simple:

> **"Hey Partner, what have you been doing?"**

And it tells you everything it discovered while you were away.

![Partner conversation](https://raw.githubusercontent.com/zty522/partner/main/docs/conversation.png)

## How It Works

Partner runs in the background, executing a research cycle every 30 minutes (configurable). Each cycle:

1. **Picks a task** from its queue (self-generated or user-injected)
2. **Executes it** via the agent backend (web search, code analysis, etc.)
3. **Records findings** in its knowledge base
4. **Generates new tasks** based on what it learned
5. **Repeats** — forever

```
┌──────────────────────────────────────────┐
│              🤝 Partner                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │   Task   │ │Knowledge │ │  Journal  │ │
│  │  Queue   │ │   Base   │ │  System   │ │
│  └──────────┘ └──────────┘ └──────────┘ │
└──────────────────┬───────────────────────┘
                   ↕
   Agent Backend (Hermes, OpenClaw, Codex, ...)
```

## Events: The Heart of Partner

An **Event** is one complete research cycle — like how Agents have Skills, Partner has Events.

Each Event follows a structured flow:

```
📖 Literature    → Search and read papers
🔬 Project Scan  → Analyze your codebase
💡 Idea Generate → Propose improvements
🧭 Exploration   → Try new directions
📝 Knowledge     → Record findings
🌱 Spawn         → Create new Events
```

Events **grow on their own** — one Event's findings automatically spawn new Events. The research never stops.

## Real Results

I ran Partner overnight on my bioinformatics research projects. By morning:

- **29 research cycles** completed autonomously
- **34 tasks** finished
- **48 knowledge entries** accumulated
- **94 tasks** queued for future exploration

Key discoveries Partner made on its own:

| Finding | Impact |
|---------|--------|
| scGPT needs domain fine-tuning for aging tasks | Avoided a dead-end approach |
| Diffusion models replacing VAEs for molecule generation | Found 25+ new papers |
| Batch correction improved cross-dataset generalization by 52.8% | Quantified the improvement |
| ProDCARL RL alignment boosted AMP hit rate from 2% to 6.3% | New method for antimicrobial design |

![Partner status](https://raw.githubusercontent.com/zty522/partner/main/docs/status.png)

## Multi-Agent Support

Partner works on top of existing agent frameworks — it doesn't reinvent the wheel.

| Agent | Status |
|-------|--------|
| 🔮 Hermes Agent | ✅ Full support |
| 🦞 OpenClaw | ✅ Supported |
| ⚡ OpenAI Codex | ✅ Supported |
| 👥 CrewAI | ✅ Supported |
| 💻 gptme | ✅ Supported |
| 🤖 AutoGPT, 👐 OpenHands, 🧠 Claude Code | 🔜 Coming |

Run `partner setup` to auto-detect installed agents.

![Partner setup](https://raw.githubusercontent.com/zty522/partner/main/docs/setup.png)

## Cross-Platform

Partner runs on **Linux, macOS, Windows, and WSL**. On WSL, it can automatically access your Windows files through the WSL Bridge.

## Getting Started

```bash
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .
partner setup
```

Then open your agent (Hermes, OpenClaw, etc.) and say:

> **"Hey Partner, what have you been doing?"**

## What's Next

- **WeChat/QQ integration** — ask Partner via voice message
- **Community Events** — share and install Event templates
- **Multi-Partner collaboration** — multiple Partners working together
- **More agent backends** — Claude Code, Cursor, and more

---

**Partner: because research shouldn't wait for you.**

GitHub: [github.com/zty522/partner](https://github.com/zty522/partner)
