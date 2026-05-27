<div align="center">

# Partner — An AI Research Companion That Evolves

*"Hey Partner, what have you been doing?"*

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

## The Core Idea

Most AI agents are tools you command. You ask, they answer. You instruct, they execute. Then they wait.

Partner is different. It's a **self-evolving research companion** — it works independently, learns from every cycle, and gets better at research the longer it runs. The core differentiator, present since v0.1.0, is a **self-evolution engine** that operates on a closed loop:

**Execute → Extract → Optimize**

1. **Execute**: Partner runs research cycles — reading literature, writing code, running experiments, analyzing results.
2. **Extract**: After every successful task, it captures the workflow pattern — the methodology, toolchain, and reasoning — and stores it as a reusable skill.
3. **Optimize**: It applies learned strategies, prunes stale knowledge, and detects capability degradation before it becomes a problem.

Conventional agents follow instructions. RAG pipelines fetch pre-indexed data. Partner does neither — it **grows** a personalized research methodology over time.

**Concrete example**: When Partner first encountered a batch effect problem in age prediction transcriptomics, it diagnosed the issue, applied ComBat correction, and evaluated the results. That diagnostic methodology — problem identification → correction strategy → evaluation → next steps — was extracted as a reusable pattern. The next time a similar batch issue arises, Partner doesn't start from scratch. It recalls the pattern, adapts it to the new context, and executes faster.

---

## Architecture

Partner schedules work across three agent backends, dispatching tasks based on affinity:

| Task Type | Dispatched To |
|-----------|--------------|
| Literature search, web queries | Hermes Agent (web / skill system) |
| Code implementation, experiments | Codex CLI / Direct Python |
| Windows integration, multi-channel QQ | NapCat / Official QQ Bot |

When a research goal arrives — say, "advance the age prediction project" — the orchestrator decomposes it into subtasks, routes each to the right tool, and tracks progress across heartbeat cycles.

```
User (QQ/CLI) → Task Queue → Cron Heartbeat (30min)
                                   │
                          ┌────────┴────────┐
                          │   Task Router   │
                          └────────┬────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        Literature           Code/Exp            Self-Evolution
        Search              Implementation      (every 2h)
        (Hermes/web)        (Codex/Python)      │
              │                    │             ├─ StrategyLearner
              ▼                    ▼             ├─ MemoryPruner
        Knowledge Base ◄──── Results ◄──────────└─ CPEGuard
              │
              ▼
        Notification → QQ/User
```

The system is **cron-driven** — a heartbeat fires every 30 minutes minimum, checks whether a plan is active, advances it if possible, and pushes a status report to QQ. This architecture means Partner survives crashes naturally (the next cron tick recovers) and never interrupts in-progress work.

---

## Features

- **Self-Evolving**: Every execution feeds back into strategy profiles. Partner gets better at prioritizing, pruning, and planning with each cycle.
- **Autonomous**: Set a direction once — Partner plans and executes multi-phase research programs independently. Literature survey → code → experiment → analysis → next plan, all without human intervention.
- **Multi-Tool Orchestration**: Integrates Hermes Agent, Codex CLI, and OpenClaw — each dispatched based on task type. A single research plan can span all three in sequence.
- **Always On**: Cron-driven heartbeat with auto-recovery. Shutdown and restart mid-plan? Partner picks up exactly where it left off.
- **QQ Native**: Talks to you via QQ (Official Bot or NapCat local proxy). Every heartbeat pushes a status report. You can check in anytime: "What have you been doing?"
- **Cross-Platform**: Native Windows GUI (tkinter, GitHub Dark theme) + WSL/headless Linux. Same state files, same behavior.
- **Transparent**: Every cycle produces a heartbeat report with current phase, progress, and next step. You always know what's happening.

---

## Quick Start

```bash
git clone https://github.com/zty522/partner
cd partner
pip install -e .
partner setup        # Interactive wizard — pick your AI backend
partner bot start qq # On Windows with NapCat, or Linux with Official Bot
partner status       # Check in anytime
```

**Windows one-click**: Download `Partner-v0.4.0-Setup.exe` from [Releases](https://github.com/zty522/partner/releases/latest), double-click, follow the wizard.

**Linux one-line**:
```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
```

During setup, choose your backend(s):
- **1** — Hermes Agent (recommended, full feature set)
- **2** — OpenClaw (multi-channel AI assistant)
- **3** — Both (switch between them)
- **4** — Skip, configure later

---

## How It Works

Partner runs on a **cron-driven heartbeat loop**. Every 30 minutes, the heartbeat fires and checks: is there an active plan? If yes, let it continue. If the current phase has deliverables, advance to the next phase. If idle, generate a new research plan based on the project's context and the knowledge base.

Plans are **multi-phase research programs** stored in `active_plan.json`. A plan can have five phase types:

| Phase | Purpose |
|-------|---------|
| 📚 literature_search | Search, read, extract methods from papers |
| 💻 code_implementation | Modify or write code based on findings |
| 🧪 experiment | Run experiments, capture results |
| 📊 analysis | Compare, evaluate, summarize |
| 🗺️ planning | Formulate next steps |

Each phase can span multiple heartbeat cycles. The system never interrupts running work — it checks for completion signals (new files, updated results) and advances only when ready.

**Real example**: When asked to advance the age prediction project, Partner automatically:
1. Surveys literature on batch correction methods (ComBat, Harmony, limma)
2. Implements GSVA pathway features in the data loader
3. Runs cross-dataset experiments with corrected vs. uncorrected data
4. Analyzes MAE improvement before and after correction
5. Proposes the next research round — all autonomously, without a single command.

### The Self-Evolution Engine (3-Layer)

Running alongside the research loop, a full evolution cycle fires every 2 hours:

1. **StrategyLearner** — Analyzes the last 50 journal entries, builds task-type profiles with success rates and value scores. High-success, high-value task types get priority boosts. Low-success types get demoted.

2. **MemoryPruner** — Scans the knowledge base for stale entries (archive after 30 days), duplicate titles (merge), high-frequency references (promote), and orphaned entries (demote). The knowledge base stays lean and relevant.

3. **CPEGuard** (Capability Preservation through Evaluation) — Registers core capabilities with baseline success rates. Each cycle re-evaluates current rates against baselines. Degradation beyond a 15% threshold triggers protective escalation — doubling verification frequency, adding redundant checks.

This three-layer system means Partner doesn't just accumulate knowledge — it **curates** its own methodology, forgets what's no longer useful, and protects against skill regression. It's the difference between a filing cabinet and a working scientist.

---

## Evolution

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full evolution log. Key milestones:

| Version | Date | What Changed |
|---------|------|-------------|
| v0.1.0 | 2026-05-24 | Core foundation: cron-driven task queue, knowledge base, journal, event engine, self-evolution prototype |
| v0.2.0 | 2026-05-26 | Heartbeat Plan Model (continuous multi-phase plans), QQ Official Bot, Conversation Engine V2 |
| v0.3.0 | 2026-05-26 | Windows GUI, Codex/OpenClaw integration, one-click installers, GitHub Actions auto-build |
| v0.4.0 | 2026-05-27 | Self-evolution engine matured to 3-layer production: StrategyLearner + MemoryPruner + CPEGuard |

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
