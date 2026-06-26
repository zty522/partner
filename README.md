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

### Key Differentiators

| Feature | What It Means |
|---------|---------------|
| **Auto-Install Agents** | When a specialized agent (e.g. cytobridge) is needed but not installed, Partner auto-downloads, installs, and configures it — no manual setup. |
| **LLM Credential Sharing** | All agent subprocesses automatically receive Partner's API key and endpoint config. Configure once, use everywhere. |
| **CLI Argument Verification** | After installing any agent, Partner runs `--help` and validates manifest args against real CLI flags — catches configuration errors at install time, not runtime. |
| **Unified Agent Interface** | Any CLI/HTTP/Python agent can be registered via a JSON manifest and called with `call_agent_skill(agent="name", ...)`. |
| **Persistent Growth** | User corrections become reusable habits. Partner learns how you work and adjusts behavior. |
| **Event-Driven Pipeline** | Tasks are broken into events with dependencies, parallel execution, and automatic retry. |
| **World Model Simulation** | Plans are simulated before execution to predict risks and suggest optimizations. |

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
partner doctor                # check local environment (Python, Git, config, Hermes, OpenClaw)
partner agent list            # list all registered external agents
partner agent register path   # register a new agent manifest
partner agent info <name>     # show manifest details for an agent
partner agent health <name>   # check agent availability
partner agent call <name> ... # call an agent with a task
partner bot start qq          # start QQ bot and the mind runtime
partner bot stop qq           # stop QQ bot
partner tui                   # enter interactive TUI mode
partner world-model status    # check world model connection status
partner world-model configure # interactive world model setup
partner world-model test      # test world model connection
partner update                # pull latest code and reinstall
partner instance list         # list all instances
partner ollama setup          # configure optional Ollama local/remote model pool
partner server add            # add a remote SSH server
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
| `atomic_ensure_agent_installed` | Auto-download + install a specialized agent if missing. |
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

### 3. External Agent Dispatch

`call_agent_skill` events are dispatched through the **external agent framework**:

```text
partner/skills/external_agent_skills.py   — two-tier dispatch logic
partner/agents/manifest.py                 — AgentManifest dataclass + validation
partner/agents/registry.py                 — AgentRegistry: discover, register, health check
partner/agents/dispatcher.py               — AgentDispatcher: cli/http/python_api dispatch
partner/agents/manifests/*.json            — Built-in agent manifests
```

```
Partner mind event (call_agent_skill)
    → external_agent_skills.py (two-tier dispatch)
        ├── General agents (hermes, openclaw, codex)
        │     └── adapter.chat() — subprocess-based chat
        └── Specialized agents (cytobridge, bamboo-ai, ...)
              └── AgentDispatcher._dispatch_cli()
                    ├── Load manifest from AgentRegistry
                    ├── Resolve CLI binary (PATH / conda / ~/.local/bin)
                    ├── Inject LLM credentials into env (auto)
                    ├── Substitute {placeholders} + {__llm_*__} in args
                    ├── Build command: [cmd] [preamble_args] [subcmd] [args]
                    └── Parse stdout JSON or plain text result
```

---

## Agent Interface — External Agent Architecture

v1.0.0 introduces a **standardized agent interface** for calling external agents from Partner.

### How It Works

Every agent describes itself with a **manifest JSON** (`partner/agents/manifests/*.json`):

```json
{
  "name": "cytobridge",
  "version": "2.0.0",
  "description": "单细胞转录组轨迹推断专用 Agent",
  "capabilities": ["trajectory_inference", "cell_dynamics"],
  "endpoint_type": "cli",
  "endpoint_config": {
    "command": "cytobridge-agent",
    "subcommand": "run",
    "preamble_args": [
      "--llm-base-url", "{__llm_base_url__}",
      "--llm-model", "{__llm_model__}"
    ],
    "args": [
      "-o", "{output}", "-q", "{question}",
      "-d", "{device}",
      "--analyses", "trajectory_fate", "drivers_genes",
      "--report-format", "md",
      "{input}"
    ]
  },
  "install_info": {
    "method": "pip",
    "package": "cytobridge-agent",
    "source": "git+https://github.com/JackkWangzh/CytoBridge-agent.git",
    "llm_config": {
      "type": "file",
      "path": "~/.agent/config.json",
      "template": {
        "llm": { "api_key": "__api_key__", "base_url": "__base_url__" }
      }
    }
  }
}
```

Supported `endpoint_type` values:
- **cli**: Invoke via subprocess — supports `preamble_args` (before subcommand), `subcommand`, and `args` (after)
- **http**: REST API call with JSON payload
- **python_api**: Direct Python import and function call
- **mcp**: MCP protocol (future)

### Parameter Substitution

| Placeholder | Source | Example |
|---|---|---|
| `{input}`, `{output}`, `{question}` | Planner task parameters | `/data/pancreas.h5ad` |
| `{device}` | Task parameter, defaults to `cpu` | `cpu` or `cuda` |
| `{__llm_api_key__}` | Auto-injected from Partner env | `sk-d...` |
| `{__llm_base_url__}` | Auto-injected from Partner env | `https://api.deepseek.com` |
| `{__llm_model__}` | Auto-injected from Partner env | `deepseek-v4-flash` |
| `{__llm_provider__}` | Auto-injected from Partner env | `deepseek` |

### Agent Discovery

AgentRegistry searches manifests from multiple locations (in priority order):
1. **Built-in**: `partner/agents/manifests/` (shipped with Partner)
2. **Workspace config**: `<workspace>/config/agents/`
3. **Global config**: `global_config/agents/` at project root
4. **User-registered**: `~/.partner/agents/`

### Adding a New External Agent

```bash
# 1. Create a manifest JSON with endpoint_config + optional install_info
partner/agents/manifests/my-agent.json

# 2. Register it
partner agent register --manifest my-agent.json

# 3. Call it
call_agent_skill(agent="my-agent", task="analyze this data")
```

If `install_info` is defined, Partner will auto-download and install the agent
on first use via `atomic_ensure_agent_installed`.

---

## Auto-Install of Specialized Agents

Partner can automatically download, install, and configure specialized agents
without any manual steps. This is the **two-event pattern**:

```text
step1: atomic_ensure_agent_installed(agent="cytobridge")
       → health check: already installed? skip.
       → not installed? pip install / git clone / npm install
       → write LLM config file (auto-inherits Partner's API key)
       → verify CLI: run --help, validate manifest args
       → register manifest
step2: call_agent_skill(agent="cytobridge", task="...")
       → dispatch to the real CLI with full credential injection
```

### Supported Install Methods

| Method | What It Runs | Use Case |
|--------|-------------|----------|
| `pip` | `pip install <package or source>` | Python packages on PyPI/GitHub |
| `git` | `git clone` + optional `pip install -e` | Development repos |
| `npm` | `npm install -g <package>` | Node.js CLIs |
| `go` | `go install <package>@latest` | Go binaries |
| `cargo` | `cargo install <package>` | Rust tools |
| `script` | Custom shell script | Complex multi-step installs |

### Post-Install Verification

After every install, Partner automatically:
1. Runs the agent's `--help` command
2. Extracts all expected flags from the manifest's `args` + `preamble_args`
3. Verifies each flag exists in the help output
4. Logs a warning with the exact file path if flags mismatch
5. Builds a dry-run dispatch command and logs it for debugging

---

## LLM Credential Auto-Injection

All agent subprocesses automatically receive Partner's LLM credentials.
**Three layers** ensure the agent can call its LLM without manual configuration:

### Layer 1 — Environment Variables (always on)

Every subprocess gets:
- `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` — from Partner's config
- `OPENAI_BASE_URL` — auto-set to DeepSeek endpoint when key starts with `sk-d`
- `PARTNER_PROVIDER`, `HERMES_MODEL` — model selection

### Layer 2 — CLI Args via `{__llm_*__}` Placeholders

Manifests use reserved placeholders that auto-resolve:
```json
"preamble_args": [
  "--llm-base-url", "{__llm_base_url__}",
  "--llm-model", "{__llm_model__}"
]
```

### Layer 3 — Config File via `install_info.llm_config`

After install, Partner writes the agent's LLM config file:
```json
"llm_config": {
  "type": "file",
  "path": "~/.cellcompass/config.json",
  "template": {
    "llm": { "api_key": "__api_key__", "base_url": "__base_url__" }
  }
}
```

The `__api_key__`, `__base_url__`, `__model__`, `__provider__` tokens
are replaced with Partner's actual credentials at install time.

---

## External Code Integration

### CytoBridge Agent

Partner v1.0.0 supports **CytoBridge Agent** (https://github.com/JackkWangzh/CytoBridge-agent) — a specialized agent for single-cell trajectory inference, cell dynamics analysis, and differentiation studies.

- **No wrapper needed**: Partner calls `cytobridge-agent` directly — the real CLI.
- **LLM credentials auto-injected**: `~/.cellcompass/config.json` is written with Partner's API key at install time.
- **Dispatch**: `call_agent_skill(agent="cytobridge", ...)` → `cytobridge-agent --llm-* run -o OUTPUT -q QUESTION -d DEVICE --analyses trajectory_fate drivers_genes INPUT.h5ad`
- **Auto-install**: First use triggers `atomic_ensure_agent_installed` which runs `pip install cytobridge-agent`.
- **CLI argument verification**: After install, Partner runs `cytobridge-agent run --help` and validates all manifest flags match.

### Adding Other External Agents

The same pattern applies to any CLI agent:

1. Create a manifest JSON in `partner/agents/manifests/`
2. Add `install_info` to enable auto-download on first use
3. Add `llm_config` to auto-configure LLM credentials
4. Call it via `call_agent_skill(agent="<name>", task="...")`

You do **not** need to clone the external agent's repository into Partner's workspace. Partner only needs:
- The manifest describing how to call it
- `install_info` describing how to install it
- `llm_config` describing where to write credentials

---

## v1.0.0 Key Upgrades

| Area | Change |
|---|---|
| **Auto-Install Agents** | `atomic_ensure_agent_installed` event + `install_info` in manifests — agents install themselves on first use |
| **LLM Credential Sharing** | Three-layer injection (env vars + CLI placeholders + config file) — configure once, all agents inherit |
| **CLI Argument Verification** | Post-install `--help` parsing validates manifest args against real CLI — catch errors at install time |
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

## Workspace Layout

```text
partner_workspace/
├── config/                          # All unified config files
│   ├── partner_config.json          # Main runtime config per workspace
│   ├── qq_config.json               # QQ bot credentials and settings
│   ├── global_config.json           # Global (cross-instance) settings
│   ├── gui_bridge.json              # Desktop GUI bridge config
│   ├── external_calls.yaml          # External API call definitions
│   └── routing_rules.yaml           # Event routing rules
├── instances/                       # Per-instance workspaces (03/, 05/, ...)
│   └── <id>/
│       ├── dialogue/                # Daily QQ logs (YYYY-MM-DD.log), chat history
│       ├── state/                   # Runtime state
│       │   ├── desktop_inbox.jsonl  # Messages from desktop GUI
│       │   ├── harness_runs.jsonl   # Harness execution records
│       │   ├── mind_pool.json       # Event queue
│       │   ├── event_decisions.jsonl
│       │   ├── tasks/               # Task execution state
│       │   ├── logs/                # Per-instance log files
│       │   └── user/                # User-facing output
│       ├── system/                  # Internal runtime
│       │   ├── hermes_work/         # Hermes agent working directory
│       │   ├── hermes_home/         # Hermes agent home
│       │   ├── habits/              # Reusable behavior habits
│       │   ├── mind/                # Mind runtime state
│       │   ├── reflections/         # Agent reflections
│       │   ├── growth/              # Growth events
│       │   ├── content_feed/        # Content exploration state
│       │   └── ...                  # (hippocampus, synapses, strategy, ideas, checks)
│       ├── projects/                # (legacy — now centralized to shared_projects/)
│       └── partner_config.json      # Per-instance agent/LLM config
├── shared_projects/                 # Centralized project pool with .lock mechanism
│   ├── registry.json                # Project ownership and metadata registry
│   └── <safe_project_name>/
│       ├── state.md                 # Current project state summary
│       ├── project_brief.md         # Project brief and objectives
│       ├── project_contract.json    # (optional) Execution contract
│       ├── exploration_log.md       # Exploration history
│       ├── files/                   # Project-specific working files
│       ├── outputs/                 # Generated outputs and artifacts
│       └── reports/                 # PDF reports and papers
├── files/                           # Shared uploaded / incoming / outgoing / working files
│   ├── uploads/
│   ├── incoming/
│   ├── outgoing/
│   └── working/
├── shared_mind/                     # Cross-instance shared memory
│   ├── habits.json
│   ├── growth_events.jsonl
│   ├── episodes.jsonl
│   └── semantic_memory.md
├── conversations/                   # Per-conversation-round pipeline snapshots
│   └── <timestamp>/
└── logs/                            # Runtime logs (agent_runs.jsonl, hermes_chat.jsonl)
```

---

## Agent Backends

| Backend | Notes |
|---|---|
| `hermes` | Main backend for tool-using staged execution. |
| `codex` | Useful for code-heavy tasks and repository edits. |
| `openclaw` | Optional OpenClaw Gateway backend. |
| `cytobridge` | Single-cell trajectory inference (auto-installed on first use). |
| `direct` | Minimal built-in fallback. |

Each backend is described by an `AgentManifest` and dispatched through the unified interface.

---

## Design Principles

- Event first: always decide the next event before execution.
- LLM selector first: semantic routing belongs to the selector, not hard-coded rules.
- Code as guardrail: code persists, validates, queues, filters, and sends.
- One event, one small closure: each backend call should complete one verifiable action.
- Evidence before claims: files, paths, sources, and metrics must be real before becoming evidence.
- Growth over patching: user corrections become reusable habits or growth, not one-off if statements.
- **External agents by manifest, not by fork**: external agent code lives in its own repo; Partner only ships a manifest.
- **Auto-configure, never ask**: Partner's LLM credentials are automatically shared with every agent subprocess.
- **Verify at install, not at runtime**: CLI args are checked against `--help` during install, not during dispatch.
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
- External agents must be installable via one of the supported methods (pip/git/npm/go/cargo/script).
