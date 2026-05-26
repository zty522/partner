<div align="center">

# 🤝 Partner

## *"Hey Partner, what have you been doing?"*

**An AI research companion that works independently in the background.
You don't give it commands. You just check in.**

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

## 🚀 一键安装

### Windows（最简单）
```
1. 打开 https://github.com/zty522/partner/releases/tag/v0.3.0
2. 点击下方的 Source code (zip)
3. 解压 → 双击 install.bat → 选 1 → 搞定
```

### Linux
```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
```

安装过程中会问你要哪个 AI 后端：
- **1** — Hermes Agent（推荐）
- **2** — OpenClaw（小龙蝦）
- **3** — 两者都装
- **4** — 暂不安装，自己配

---

## 📦 Versions

| Version | Date | Highlights |
|---------|------|------------|
| **[v0.3.0](https://github.com/zty522/partner/releases/tag/v0.3.0)** | 2026-05-26 | 🎉 **一键安装** · **Codex/OpenClaw集成** · **多后端选择** · **Windows原生安装** |
| **[v0.2.0](https://github.com/zty522/partner/releases/tag/v0.2.0)** | 2026-05-26 | 💓 **Heartbeat Plan Model** · **QQ Official Bot** · **LLM-powered chat** · **Watchdog守护** |
| **[v0.1.0](https://github.com/zty522/partner/releases/tag/v0.1.0)** | 2026-05-24 | Event system, Conversation V2, Self-Evolution Engine |

[📥 Download Latest](https://github.com/zty522/partner/releases/latest) · [📋 All Releases](https://github.com/zty522/partner/releases)

---

## What's New in v0.3.0

### 🚀 一键安装 (Windows / Linux)

| 平台 | 安装方式 |
|------|----------|
| **Windows** | 下载 ZIP → 解压 → 双击 `install.bat` → 全程鼠标操作 |
| **Linux** | `curl ... install.sh \| bash` — 自动检测系统、装Python、装依赖 |

安装时可选 4 种后端：
- **Hermes Agent** — pip 安装，功能最完整
- **OpenClaw** — npm 安装，多渠道 AI 助手
- **两者都装** — 可切换使用
- **自己配** — 不强制安装，灵活配置

### 🤖 Codex CLI 集成
Partner 可以直接委托编码任务给 [OpenAI Codex](https://github.com/openai/codex)：
- `codex exec --full-auto '创建Python模块'` → 自动写代码、git commit
- 已验证：创建 `partner_helper.py`（fibonacci / is_prime / gcd），代码正确，功能通过测试

### 🦞 OpenClaw 集成
Partner 通过 [ACP 协议](https://docs.openclaw.ai/cli/acp) 对接 [OpenClaw (小龙蝦)](https://github.com/openclaw/openclaw)：
- `openclaw acp` — ACP 桥接模式
- `openclaw agent --agent main -m '任务描述'` — 直接委托
- 支持多渠道（QQ、微信、Telegram、Discord 等 20+ 平台）

### 📦 安装脚本体系
| 脚本 | 用途 |
|------|------|
| `scripts/install.sh` | Linux 一键安装（自动检测发行版、装Python/Git） |
| `scripts/install.ps1` | Windows PowerShell 安装（可选创建虚拟环境） |
| `scripts/install.bat` | Windows 双击安装（最简单，免输命令） |
| `scripts/uninstall.sh` | Linux 卸载 |

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

**v0.2.0 update (心跳维护模式):**
- 心跳只做维护 + QQ 通信 + 健康检查，研究任务独立运行不受心跳约束
- Bot Watchdog 进程守护：挂了自动重启
- 每轮心跳必推 QQ 消息（移除 60 分钟阈值）

**How the heartbeat works:**
```
Every 30 minutes (minimum):

    1. Check QQ bot status (watchdog auto-restarts if dead)
    2. Check research tasks for hangs (>2h = stuck)
    3. Send heartbeat report to QQ (always, no timeout)
    4. Update heartbeat.json
```

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

### 🛡️ Bot Watchdog
QQ 机器人进程守护，每 60 秒检查一次：
- 进程活着 → 跳过
- 进程死了 → 自动重启
- `partner bot stop qq` 时自动清理

### 📦 Auto-Install Dependencies
- `partner setup` detects missing `aiohttp` and offers automatic installation
- `partner bot start qq` also checks deps before starting
- Scripts (`send_qq_report.py`, etc.) auto-deployed to workspace during setup

### 🎯 Streamlined CLI
```
partner setup              Configure everything
partner status             View full status
partner bot start qq       Start QQ bot
partner bot stop qq        Stop QQ bot
partner queue clear        Clear task queue
partner config set interval N  Change heartbeat interval
partner update             Pull latest code + reinstall
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
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐   │
│  │QQ Bridge │ │   CLI    │ │ Agent Integration│   │
│  │(Official)│ │          │ │ Codex · OpenClaw │   │
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
partner setup              # Configure everything
partner status             # View full status (research + bot health)
partner bot start qq       # Start QQ bot in background
partner bot stop qq        # Stop QQ bot
partner queue clear        # Clear task queue
partner config set interval N  # Change heartbeat interval (minutes)
partner update             # Pull latest code + reinstall
```

---

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
