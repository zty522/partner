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

# Windows (one-liner, no downloads needed)
powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
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

Partner is built on a **Mind Pool** — an `asyncio.PriorityQueue` of spontaneous "thought impulses" that drives all autonomous behavior. No cron scheduler, no file-based task queue, no `active_plan.json`.

```
┌─────────────────────────────────────────────────────────────┐
│                    🤝 Partner v0.4                          │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              🧠 Mind Pool + mind_loop()              │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │   │
│  │  │Curiosity │ │ Project  │ │  Report  │ │CronTick│  │   │
│  │  │(explore) │ │(long-term)│ │  (push)  │ │(pulse) │  │   │
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘  │   │
│  │       │            │            │            │       │   │
│  │       ▼            ▼            ▼            ▼       │   │
│  │  ┌──────────────────────────────────────────────┐    │   │
│  │  │  Waiting Room (wake_after delayed events)    │    │   │
│  │  └──────────────────────────────────────────────┘    │   │
│  └──────────────────────┬──────────────────────────────┘   │
│                         │                                  │
│  ┌──────────────────────┴──────────────────────────────┐   │
│  │  Searcher (Semantic Scholar / Crossref / ArXiv)     │   │
│  │  Knowledge Base │ Journal │ Self-Checker            │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  QQ Bridge   │  │     CLI      │  │  Agent Adapter   │  │
│  │  (Official)  │  │              │  │  (Hermes/Direct) │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### How It Works

1. **Events are spontaneous impulses** — 10 types including Curiosity, Project, Report, CronTick, UserMessage, SelfReflection, etc. They arise from internal state.
2. **Mind Pool** — `asyncio.PriorityQueue` collects all events. Priority determines execution order. Supports `wake_after` for delayed execution (events stay in a "waiting room" until their time comes).
3. **mind_loop()** — permanent async daemon that pulls the highest-priority due event and spawns an execution task.
4. **Self-pulse** — every 15 minutes the system injects a `CRON_TICK` which generates a Curiosity to explore knowledge gaps.
5. **Project events** — user research requests become Project events that persist in the pool. Each execution generates a Curiosity sub-event, then re-queues itself with a 15-minute delay (`wake_after`). No `active_plan.json`, no cron dependency.
6. **Reports push instantly** — Report events call `bot.send_message()` directly via callback. No file polling, no duplicates.
7. **Academic search** — `searcher.py` uses Python `requests` to call Semantic Scholar API directly. No shell subprocesses, no `curl | python` pipelines, no security software blocking. Falls back through Crossref → ArXiv automatically.

### Event Types

| Type | Priority | When | What Happens |
|------|----------|------|-------------|
| **WakeUp** | 1 | Bot startup / restart | Restores project state, generates recovery report |
| **UserMessage** | 1 | QQ message received | Feeds into Mind Pool + gets instant "思考中，请等待..." reply |
| **Correction** | 2 | Executor failure | Logs error for future improvement |
| **Report** | 3 | Curiosity / SelfCheck result | Pushes to QQ immediately via direct callback |
| **Curiosity** | 5 | Cron tick, user question, knowledge gap | Searches academic APIs (S2/Crossref/ArXiv) via searcher.py + knowledge base |
| **Project** | 5 | User instruction (QQ/CLI) | Self-cycling event with wake_after. First pass immediate, subsequent at 5min (QQ) / 15min (auto) |
| **SelfReflection** | 7 | Every 2 hours | Runs SelfChecker |
| **DiaryWrite** | 8 | Daily at 23:00 | Logs daily summary |
| **CronTick** | 10 | Every 15 minutes (self-pulse) | Injects periodic Curiosity. Also accelerates Project events that have waited too long |

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

## v0.4.0: Proactive Agent Behavior

Partner is now a **proactive** research companion, not a passive chatbot.

### Behavior Changes

| Before | After |
|--------|-------|
| "我回来了" (vague) | Detailed restart report: project name, progress, metrics, next steps |
| "有什么需要？" (asks back) | "根据上次对话，我建议继续推进X。已经开始搜索文献。" |
| "不知道下一步怎么做" | "我去查一下文献和代码，找到突破口。" or "没有直接找到方案，我打算尝试[方案]。" |
| Skips silently when search finds nothing | Generates tentative plan: "关于X没有直接记录，我计划从以下方向探索..." |
| Idles when queue is empty | Auto-detects idle → generates exploration from knowledge gaps → notifies user |
| Waits for user to ask | Pushes progress to QQ after every task completion |

### Restart Report Format
```
✅ 系统已重启，恢复运行。
📌 上次工作状态：age_pred_v2，已执行到：batch_correction leak fix - script written but not run（MAE=7.08）
📋 当前计划：
1. 运行泄漏修复实验
2. 测试 scGPT embedding
3. 搜索相关文献
🕒 预计下次汇报：有实质性进展时主动发送。
```

### Status Query Format (no more "有什么需要")
```
📊 当前研究：age_pred_v2
📈 最近成果：batch_correction leak fix, MAE=7.08
⏳ 正在进行：推进研究计划中
🎯 接下来计划：
  • 运行泄漏修复实验
  • 测试 scGPT embedding
```

### Idle Auto-Exploration
When the Mind Pool is empty (no active projects, no pending curiosity), the cron tick automatically:
1. Checks `knowledge.json` for poorly-covered topics
2. Generates new Curiosity events for those gaps
3. Notifies the user: "当前没有进行中的项目，已自动开始探索以下方向：..."

### Never Skip, Never Say "I Don't Know"
- Search returns nothing → generates a tentative exploration plan
- LLM unavailable → provides structured fallback with whatever data exists
- Completely stuck → suggests a specific approach: "没有直接找到方案，我打算尝试..."

---

## What's New in v0.4.0 (Architecture)

- **Mind Pool system** — replaces old cron-driven execution with event-driven spontaneous thought
- **Project events** — replace `active_plan.json`. User research requests become self-cycling Project events with `wake_after` delay
- **Waiting room** — delayed events stay in pool until their time comes (no busy-looping)
- **`searcher.py`** — direct academic API calls (Semantic Scholar/Crossref/ArXiv). No shell subprocesses
- **Instant QQ reply** — "思考中，请等待..." sent immediately, replaced by actual response
- **No hardcoded responses** — all conversation through LLM, zero templates
- **Codebase cleanup** — 22 Python files (was 26), removed `active_plan.json`, `task_queue.json`, `send_qq_report.py` old patterns

### v0.4.0 Hotfixes

- 🔧 **QQ message loss fix** — WebSocket reconnection pulls missed messages; message ID dedup cache (5min TTL); heartbeat interval reduced to 15s; progressive reconnection (5s → 10s)
- 💬 **TASK instant reply** — Sandbox mode fix (skip msg_id to avoid 40011000); PROJECT events inject immediate CRON_TICK; Mind loop health check (alerts on 2 consecutive stale pool size checks)
- 🧠 **Dialog context bridge** — New `context_broker.py`: auto-extracts project facts (MAE, leak issues, file paths) from dialog history; PROJECT events carry full dialog context; LLM reports use conversation background
- 📚 **README updated** — Expanded documentation

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
│   ├── event_types.py      # 10 event types + wake_after
│   ├── pool.py             # Global PriorityQueue + waiting room
│   ├── scheduler.py        # mind_loop() async daemon
│   └── executor.py         # Event dispatcher + push callback
├── partner/
│   ├── searcher.py         # Academic search (S2/Crossref/ArXiv)
│   ├── dialog.py           # Merged: dialog_history + context
│   ├── autocheck.py        # Merged: event_bus + self_check + notifier
│   ├── conversation.py     # LLM-native conversation (no templates)
│   ├── core.py             # Partner class + Mind integration
│   ├── context_broker.py   # Dialog → Knowledge context bridge (v0.4.0)
│   ├── state_persistence.py # last_state.json save/load/format (v0.4.0)
│   ├── qq_official_bridge.py  # QQ bot + auto-start Mind + instant reply
│   ├── router.py           # Intent classification only (no hardcoded responses)
│   └── ... (23 files total)
├── scripts/                # Install scripts
├── installer/              # Windows installer
├── docs/                   # Documentation
├── README.md
└── CHANGELOG.md
```

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
