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

### Linux
```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
```
安装过程中会问你：选哪个 AI 后端（Hermes / OpenClaw / 都装 / 自己配）。

### Windows
```powershell
# 下载并运行安装脚本
powershell -ExecutionPolicy Bypass -File install.ps1
```
同样会问你选哪个后端，Python/Node.js 没有的话会自动下载。

> **无需 WSL** — Hermes、OpenClaw、Codex 都能在 Windows 上原生运行。

---

## 📦 Versions

| Version | Date | Highlights |
|---------|------|------------|
| **[v0.2.0](https://github.com/zty522/partner/releases/tag/v0.2.0)** | 2026-05-26 | 🎉 **一键安装脚本** · **Watchdog守护** · **每轮必推QQ** · **心跳维护模式** · **Codex/OpenClaw集成** |
| **[v0.1.0](https://github.com/zty522/partner/releases/tag/v0.1.0)** | 2026-05-24 | Heartbeat Plan Model, QQ Official Bot, CLI setup, Auto-install deps |

[📥 Download Latest](https://github.com/zty522/partner/releases/latest) · [📋 All Releases](https://github.com/zty522/partner/releases)

---

## Quick Start

```bash
# 1. 安装（任选其一）
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash

# 2. 配置
partner setup

# 3. 启动 QQ 机器人
partner bot start qq

# 4. 查看状态
partner status
```

### Commands

```
partner setup        First-time configuration wizard
partner status       View full status + research stats
partner bot start qq Start QQ bot (background)
partner bot stop qq  Stop QQ bot
partner queue clear  Clear task queue / reset plan
partner config set interval N  Change heartbeat interval
partner update       Pull latest code + reinstall
```

---

## 🔧 Integration with Other Agents

### Codex CLI
Partner can delegate coding tasks to [Codex](https://github.com/openai/codex):
```python
codex exec --full-auto 'Create a Python module with utility functions'
```

### OpenClaw (小龙虾)
Partner integrates with [OpenClaw](https://github.com/openclaw/openclaw) via ACP protocol:
```bash
openclaw agent --agent main -m 'Research task description'
```

---

## Architecture

```
cron (every 30min)
  │
  ├── Check QQ bot status (watchdog auto-restarts if dead)
  ├── Check research tasks for hangs (>2h = stuck)
  ├── Send heartbeat report to QQ (always, no timeout)
  └── Update heartbeat.json

Research tasks run independently, NOT constrained by heartbeat.
```

---

## 📁 Project Structure

```
partner/
├── partner/              # Python package
│   ├── cli.py           # CLI commands
│   ├── setup.py         # Interactive setup wizard
│   ├── qq_official_bridge.py  # QQ Bot integration
│   └── conversation.py  # LLM conversation engine
├── scripts/
│   ├── install.sh       # Linux one-click install
│   ├── install.ps1      # Windows installer
│   └── uninstall.sh     # Linux uninstall
├── CHANGELOG.md
└── README.md
```

---

## License

Apache 2.0
