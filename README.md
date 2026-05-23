<![CDATA[<div align="center">

# 🤝 Partner

### Your AI Research Companion

**LLM generates text. Agent executes tasks. Partner does research — on its own.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

</div>

---

## What is Partner?

Partner is a new kind of AI system — an **autonomous research entity** that works independently in the background. It reads papers, explores your projects, builds knowledge, and proposes new ideas. All on its own.

The best part? You can just ask:

> **"Hey Partner, what have you been doing?"**

And it tells you everything it discovered while you were away.

```
LLM:    You ask → Model answers → Done
Agent:  You command → Agent executes → Waits for next command
Partner: Partner works on its own → You check in → Partner reports
```

**Partner is proactive, not reactive.**

---

## Quick Start

```bash
pip install partner-research
```

```bash
# Initialize a workspace
partner init --workspace ~/my_research

# Start Partner (it begins working immediately)
partner start --workspace ~/my_research

# Later, talk to Partner
partner chat --workspace ~/my_research
```

```
你: 最近在研究什么？

Partner: 📊 我最近的研究进展：

  ⏱  完成了 12 个研究周期
  📋 完成了 8 个任务
  📚 积累了 15 条知识

  📖 最近活动：
  1. [2026-05-23 10:22] 搜索转录组年龄预测最新方法
     → 发现 OMICmAge (Nature Aging 2024) 多组学时钟
  2. [2026-05-23 11:15] 分析探索树实验结果
     → 最佳方案：年龄匹配+年龄感知校正，MAE=5.95

  🔑 最近重要发现：
  • [high] 单细胞基础模型在衰老建模上尚不成熟
  • [medium] 扩散模型正在取代 VAE 做分子生成
```

---

## Core Features

### 🧠 Autonomous Research
Partner doesn't wait for your commands. It generates its own research tasks, searches for literature, analyzes your projects, and builds a knowledge base — all in the background.

### 💬 Conversational Check-in
The killer feature. Just talk to Partner like a colleague:
- "最近在研究什么？" — See what it's been up to
- "关于扩散模型你知道什么？" — Search its knowledge
- "暂停A，集中做B" — Adjust its direction
- "详细说说" — Deep dive into any finding

### 📚 Growing Knowledge Base
Every research cycle adds to Partner's knowledge. Over time, it builds a comprehensive understanding of your research domain — and it never forgets.

### 🔄 Crash Recovery
Power outage? Network down? API quota exhausted? Partner detects interruptions via heartbeat signals and automatically recovers when restarted. No progress lost.

### 🔌 Pluggable Agent Backend
Partner works on top of existing agent frameworks (like how agents work on top of LLMs):
- **Hermes Agent** (default) — full-featured with web search, code execution
- **Direct mode** — lightweight, no external agent needed
- More backends coming: Claude Code, Codex, OpenClaw

### 📂 Workspace Isolation
Partner operates in its own workspace. It can read your projects but only writes within its designated area. Your data stays safe.

---

## Architecture

```
You (the researcher)
        ↕ conversation
   ┌─────────────┐
   │   Partner    │ ← autonomous research loop
   │   ┌───────┐  │
   │   │ Tasks  │  │ ← self-generated + user-injected
   │   │Knowledge│ │ ← growing knowledge base
   │   │ Journal │ │ ← activity log
   │   └───────┘  │
   └──────┬──────┘
          ↕
   Agent Backend (Hermes, Claude Code, ...)
          ↕
   Workspace (files, state, checkpoints)
```

---

## Commands

```bash
partner init                    # Initialize workspace
partner start                   # Start Partner
partner start --once            # Run one cycle and exit
partner chat                    # Interactive conversation
partner chat "最近干了什么"      # Single message
partner status                  # Quick status check
partner task list               # View task queue
partner task add "title" "desc" # Add a task
partner knowledge search "query" # Search knowledge
partner run                     # Run one research cycle
```

---

## How It Works

1. **Partner starts** and loads its state (tasks, knowledge, journal)
2. **Every N minutes**, it wakes up and picks the highest priority task
3. **Executes the task** via the agent backend (search literature, analyze code, etc.)
4. **Records findings** in the knowledge base and journal
5. **Generates new tasks** based on what it learned
6. **Repeats** — forever, until you tell it to stop

When you check in, Partner reads its journal and knowledge base to give you a clear, concise summary of everything it discovered.

---

## Use Cases

- **Research labs**: Let Partner explore related work while you do experiments
- **Graduate students**: Have a research companion that never sleeps
- **Data science teams**: Automatically monitor and improve ML pipelines
- **Literature reviews**: Partner reads papers so you don't have to
- **Cross-project insights**: Partner finds connections you'd miss

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

## License

MIT — use it however you want.

---

<div align="center">

**Partner: because research shouldn't wait for you.**

*Built with the belief that AI should be a research companion, not just a tool.*

</div>
]]>