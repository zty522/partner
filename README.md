<div align="center">

# Partner v1.0.0

### An event-driven AI companion for long-running research work

Partner is not a chatbot and not a normal agent runner.
It is a runtime for staged execution, persistent memory, habits, growth, event-by-event collaboration,
and **external agent orchestration**.

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

## What Partner Is

Most AI systems are reactive:

```text
LLM:     user asks -> model answers -> session ends
Agent:   user commands -> agent calls tools/skills -> waits for the next command
Partner: context/memory/habits -> selector chooses an event -> agent executes one step
         -> Partner analyzes the result, updates state, learns, and chooses the next event
```

Partner is built around **staged execution**. Each stage has a goal, an event, an action, a result, and a follow-up decision. A backend agent still performs concrete work, but Partner keeps continuity outside the agent call.

### v1.0.0 Key Upgrades

| Area | Change |
|---|---|
| **Agent Interface** | Standardized AgentRegistry + AgentDispatcher + AgentManifest system for pluggable external agents |
| **CLI** | Monolithic `cli.py` → modular `partner/cli/` package with subcommand modules |
| **Desktop GUI** | Refactored PySide6 GUI with modern pages (agents, settings, conversation) |
| **Harness Core** | New `robust_executor`, `task_instance`, `artifact_validator`, `remediation_handler` |
| **Skill System** | `partner/skills/` with registry, store, discovery, external agent skills |
| **LLM Layer** | Centralized `partner/llm/` module for provider-agnostic inference |
| **External Agents** | `partner/agents/` package — manifests, registry, dispatcher for any CLI/HTTP/MCP agent |
| **Project Planner** | `partner/planner/` batch planner + prompt builder |
| **World Model** | `partner/world_model/` and HTTP server |
| **QQ Bot** | Replaced old qq_official_bot/bridge with modular `partner/qq_bot/` |

---

## Agent Interface — External Agent Architecture

v1.0.0 introduces a **standardized agent interface** for calling external agents from Partner.

### How It Works

```text
Partner mind event (call_agent_skill)
    → external_agent_skills.py (two-tier dispatch)
        ├── General agents (hermes, openclaw, codex)
        │     └── adapter.chat() — subprocess-based chat
        └── Specialized agents (cytobridge, bamboo-ai, ...)
              └── AgentDispatcher._dispatch_cli()
                    ├── Load manifest from AgentRegistry
                    ├── Resolve CLI binary (PATH / conda / ~/.local/bin)
                    ├── Substitute {placeholders} in args
                    ├── Inject API credentials into subprocess env
                    └── Parse stdout JSON or plain text result
```

### Agent Manifest (`partner/agents/manifests/*.json`)

Every external agent describes itself with a manifest JSON file:

```json
{
  "name": "cytobridge",
  "version": "1.0.0",
  "description": "单细胞转录组轨迹推断专用 Agent",
  "capabilities": ["trajectory_inference", "cell_dynamics"],
  "input_formats": ["h5ad", "loom", "csv"],
  "output_formats": ["html", "md", "pdf", "png", "h5ad"],
  "endpoint_type": "cli",
  "endpoint_config": {
    "command": "cytobridge-wrapper",
    "args": ["--input", "{input}", "-q", "{question}", "-o", "{output}", "--device", "{device}"],
    "timeout": 3600
  },
  "timeout": 3600,
  "health_check_cmd": "cytobridge-agent --version"
}
```

Supported `endpoint_type` values:
- **cli**: Invoke via subprocess — command + args with {placeholder} substitution
- **http**: REST API call with JSON payload
- **python_api**: Direct Python import and function call
- **mcp**: MCP protocol (future)

### Agent Discovery

AgentRegistry searches manifests from multiple locations (in priority order):
1. **Built-in**: `partner/agents/manifests/` (shipped with Partner, e.g. hermes, openclaw, codex, cytobridge)
2. **Workspace config**: `<workspace>/config/agents/`
3. **Global config**: `global_config/agents/` at project root
4. **User-registered**: `~/.partner/agents/`

### Wrapper Pattern for External Agents

External agents like CytoBridge are **not bundled** in Partner's code. Instead:

1. The agent is installed separately (e.g. `pip install cytobridge-agent`)
2. A **wrapper script** at `~/.local/bin/<agent>-wrapper` provides a stable CLI interface
3. Partner's manifest describes how to call the wrapper

The wrapper pattern solves version conflicts, multiprocessing issues, and keeps Partner decoupled from the external agent's dependency tree.

### Adding a New External Agent

```bash
# 1. Create a manifest JSON
partner/agents/manifests/my-agent.json

# 2. Register it (optional — built-in manifests are auto-discovered)
partner agent register --manifest my-agent.json

# 3. Call it from a skill or plan
call_agent_skill(agent="my-agent", task="analyze this data")
```

---

## Quick Start

### Linux / WSL

```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
partner setup
```

### Windows

PowerShell 5.1+:

```powershell
powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
partner setup
```

You can also download the Windows installer from
[GitHub Releases](https://github.com/zty522/partner/releases).

### From Source

```bash
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .
partner setup
```

---

## Common Commands

```bash
partner setup                 # configure workspace, backend, QQ bot, and instances
partner status                # show runtime and bot status
partner agent list            # list all registered external agents
partner agent register path   # register a new agent manifest
partner bot start qq          # start QQ bot and the mind runtime
partner bot stop qq           # stop QQ bot
partner update                # pull latest code and reinstall
```

Configure servers used by remote instances:

```bash
partner server add --name server-prod-01 --host 203.0.113.10 --user ubuntu ...
partner server list
```

Ollama endpoints:

```bash
partner ollama setup
partner ollama add --name local --base-url http://127.0.0.1:11434 ...
```

---

## Workspace Layout

```text
partner_workspace/
  global_config.json
  instances/
    01/
      00_config/
      10_logs/
      20_records/
        active_project.txt
        projects/<project>/
      state/
        mind_pool.json
        event_decisions.jsonl
      system/
        habits/
        growth/
        content_feed/
        reflections/
      logs/
        agent_runs.jsonl
      user/
        current_project/
        reports/
```

---

## Architecture

### 1. Interaction Line

User messages from QQ, CLI, or GUI enter the **interaction orchestrator**. The orchestrator builds selector context from conversation, pending questions, project status, mind pool, habits, and available events. The selector returns a structured route:

```json
{
  "route": "direct_reply|mind_event|pause_project|none",
  "event_type": "direct_task|literature_review|evidence_audit|pdf_report|...",
  "objective": "A concrete objective for the next event",
  "reply_to_user": "...",
  "priority": 1
}
```

### 2. Mind Event Line

The mind runtime consumes persistent events from `state/mind_pool.json`.

| Event | Purpose |
|---|---|
| `direct_task` | Complete a one-shot user deliverable. |
| `call_agent_skill` | Forward task to an external agent. |
| `literature_review` | Gather and summarize sources. |
| `data_analysis` | Analyze data, metrics, or experiment output. |
| `evidence_audit` | Check claims, files, sources. |
| `artifact_build` | Build a visible artifact. |
| `pdf_report` | Generate a real PDF report. |
| `project_think` | Choose the next minimal project action. |
| `curiosity_explore` | Explore a meaningful follow-up question. |
| `habit_update` | Record a reusable behavior change. |
| `stop_project` | Explicitly stop or pause project execution. |
| `project` | Continue a long-running project lifecycle. |
| `reflection` | Re-evaluate progress and strategy. |
| `cross_project` | Transfer lessons across projects. |
| `memory_consolidate` | Compact memory into usable context. |
| `content_digest` | Turn user-shared content into hypotheses. |
| `wake_up` / `cron_tick` | Recover and pulse the runtime. |

### 3. External Agent Line

`call_agent_skill` events are dispatched through the **external agent framework**:

```text
partner/skills/external_agent_skills.py   — two-tier dispatch logic
partner/agents/manifest.py                 — AgentManifest dataclass + validation
partner/agents/registry.py                 — AgentRegistry: discover, register, health check
partner/agents/dispatcher.py               — AgentDispatcher: cli/http/python_api dispatch
partner/agents/manifests/*.json            — Built-in agent manifests
```

---

## Agent Backends

| Backend | Notes |
|---|---|
| `hermes` | Main backend for tool-using staged execution. |
| `codex` | Useful for code-heavy tasks and repository edits. |
| `openclaw` | Optional OpenClaw Gateway backend. |
| `cytobridge` | Single-cell trajectory inference (installed separately). |
| `direct` | Minimal built-in fallback. |

Each backend is described by an `AgentManifest` and dispatched through the unified interface.

---

## External Code Integration

### CytoBridge Agent

Partner v1.0.0 supports **CytoBridge Agent** (https://github.com/JackkWangzh/CytoBridge-agent) — a specialized agent for single-cell trajectory inference, cell dynamics analysis, and differentiation studies.

- **Integration pattern**: Partner ships:
  1. A **manifest** (`partner/agents/manifests/cytobridge.json`) describing how to call CytoBridge
  2. A **wrapper** (`partner/agents/wrappers/cytobridge_wrapper.py`) — Partner-owned code that sets multiprocessing start_method='spawn' and runs the cytobridge-agent pipeline
  3. A **console_scripts entry point** (`cytobridge-wrapper`) installed via `pip install -e .`
- **The actual CytoBridge source code** lives in its own repository. Partner only ships the integration layer.
- **Installation**: `pip install partner-research[cytobridge]` — installs both the wrapper entry point and `cytobridge-agent`.
- **Wrapper ownership**: The wrapper is **Partner's code**, stored in `partner/agents/wrappers/` and registered as a `console_scripts` entry point in `pyproject.toml`. This means `pip install -e .` or `pip install partner-research` automatically makes `cytobridge-wrapper` available on PATH.
- **How Partner calls it**: `call_agent_skill(agent="cytobridge", task="...")` → AgentDispatcher reads the manifest → runs `cytobridge-wrapper --input {file} -q "{task}" -o {output_dir}`.

### Adding Other External Agents

The same pattern applies to any CLI agent:

1. Create a manifest JSON in `partner/agents/manifests/`
2. Ensure the CLI is installed and on PATH
3. Call it via `call_agent_skill(agent="<name>", task="...")`

You do **not** need to clone the external agent's repository into Partner's workspace. Partner only needs:
- The CLI command on PATH
- The manifest describing how to call it

---

## Design Principles

- Event first: always decide the next event before execution.
- LLM selector first: semantic routing belongs to the selector, not hard-coded rules.
- Code as guardrail: code persists, validates, queues, filters, and sends.
- One event, one small closure: each backend call should complete one verifiable action.
- Evidence before claims: files, paths, sources, and metrics must be real before becoming evidence.
- Growth over patching: user corrections become reusable habits or growth, not one-off if statements.
- **External agents by manifest, not by fork**: external agent code lives in its own repo; Partner only ships a manifest.
- PDF as a real event: final PDF reports should be generated by `pdf_report`.
- Stop explicitly: project execution should stop through `stop_project`.

---

## 第三方代码声明 / Third-Party Notices

This project incorporates design patterns and code inspired by the following open-source projects:

- **Hermes Agent** (MIT) — https://github.com/nousresearch/hermes-agent
- **Hermes Desktop** (MIT) — https://github.com/fathah/hermes-desktop
- **OpenClaw** (MIT) — https://github.com/openclaw/openclaw
- **OpenClaw Windows Hub** (MIT) — https://github.com/openclaw/openclaw-windows-node
- **CytoBridge Agent** (MIT) — https://github.com/JackkWangzh/CytoBridge-agent

See `NOTICE.md` for full license texts and attribution details.

**Important**: The CytoBridge agent is an external dependency. Partner's repo only contains a manifest JSON describing how to call it. The actual CytoBridge source code is maintained at its own repository and installed via `pip install cytobridge-agent`. The MIT license attribution in NOTICE.md covers the manifest integration layer only.

---

## Current Limitations

- Event selection quality depends on the selector backend.
- Long-term memory needs consolidation to stay useful.
- Growth events influence execution but are not a formal policy engine.
- Evidence audit cannot replace human scientific judgment.
- Different agent backends behave differently.
- QQ file delivery depends on bot platform constraints.
- External agents must be installed separately and available on PATH.
