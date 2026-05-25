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
| **[v0.2.0](https://github.com/zty522/partner/releases/tag/v0.2.0)** | 2026-05-25 | 🎉 **QQ官方机器人** · **LLM对话** · **CLI重构** · **项目化Workspace** |
| **[v0.1.0](https://github.com/zty522/partner/releases/tag/v0.1.0)** | 2026-05-24 | First stable release: Event system, Conversation V2, Self-Evolution Engine |

[📥 Download Latest](https://github.com/zty522/partner/releases/latest) · [📋 All Releases](https://github.com/zty522/partner/releases)

---

## What's New in v0.2.0

### 🐧 QQ官方机器人
- 接入 **QQ开放平台** 官方 API（不再是 NapCat 方案）
- 支持 **私聊(C2C)** 和 **群聊@** 
- 无需 Windows，纯 Linux 运行
- 自动后台启动：`partner bot start qq`

### 🧠 LLM 驱动对话
- QQ 对话不再用模板回复，而是通过 **Hermes LLM** 生成自然语言
- 上下文记忆（最近5轮对话）
- 简短口语化，不罗列格式化数据

### 🎯 CLI 精简为三条命令
```
partner setup      配置一切（Agent + QQ机器人 + 自动后台）
partner status     查看全部状态（含机器人运行状态）
partner bot        启动/停止机器人
```

### 📁 项目化 Workspace
```
workspace/
├── projects/
│   ├── age_prediction/  code/ ideas/ notes/ dialogue/ data/
│   ├── cytobridge/
│   ├── ligand_design/
│   └── partner/
├── dialogue/           每日对话记录 (.log)
├── journal/            每日总结日志 (.log)
├── knowledge/          共享知识库
└── state/              Partner 运行时状态
```
- 每日自动整理（凌晨4点），**不删除任何内容**
- 文件命名规范：`类型_主题_序号_日期.ext`
- 旧版本自动归档，历史可追溯

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

The setup wizard detects your installed agents (Hermes, Codex, Claude Code), configures a workspace, and sets up QQ bot integration.

```bash
partner setup           # First-time configuration
partner status          # Check everything
partner bot start qq    # Start QQ bot in background
```

### QQ Bot Setup

1. Go to [q.qq.com](https://q.qq.com/) and register as a developer
2. Create a bot application → get **AppID** + **AppSecret**
3. Run `partner setup` and enter credentials
4. Run `partner bot start qq`

Then open QQ, find your bot, and start chatting:

```
You:       Hey Partner, what have you been doing?
QQ Bot:    嘿，刚把大版本收了个尾——加了QQ官方机器人支持，顺手把setup流程重构了一遍。
```

---

## How It Works

```
┌──────────────────────────────────────────┐
│            You (the researcher)           │
│   "Hey Partner, what have you been doing?"│
└──────────────────┬───────────────────────┘
                   ↕ QQ / WeChat / Agent CLI
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
                   ↕ LLM-powered conversation
┌──────────────────┴───────────────────────┐
│  QQ Bot Platform (api.sgroup.qq.com)      │
│  WebSocket · REST API · Hermes Backend    │
└──────────────────────────────────────────┘
```

---

## Commands

```bash
partner setup           # Configure everything (Agent + QQ + auto-start)
partner status          # View full status (research + bot status)
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

## License

[Apache 2.0](LICENSE) — use it however you want.

---

<div align="center">

***Partner: because research shouldn't wait for you.***

</div>
