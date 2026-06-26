     1|     1|<div align="center">
     2|     2|
     3|     3|# Partner v1.0.0
     4|     4|
     5|     5|### An event-driven AI companion for long-running research work
     6|     6|
     7|     7|Partner is not a chatbot and not a normal agent runner.
     8|     8|It is a runtime for staged execution, persistent memory, habits, growth, event-by-event collaboration,
     9|     9|and **external agent orchestration**.
    10|    10|
    11|    11|[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
    12|    12|[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
    13|    13|
    14|    14|</div>
    15|    15|
    16|    16|---
    17|    17|
    18|    18|## What Partner Is
    19|    19|
    20|    20|Most AI systems are reactive:
    21|    21|
    22|    22|```text
    23|    23|LLM:     user asks -> model answers -> session ends
    24|    24|Agent:   user commands -> agent calls tools/skills -> waits for the next command
    25|    25|Partner: context/memory/habits -> selector chooses an event -> agent executes one step
    26|    26|         -> Partner analyzes the result, updates state, learns, and chooses the next event
    27|    27|```
    28|    28|
    29|    29|Partner is built around **staged execution**. Each stage has a goal, an event, an action, a result, and a follow-up decision. A backend agent still performs concrete work, but Partner keeps continuity outside the agent call.
    30|    30|
    31|    31|### Key Differentiators
    32|    32|
    33|    33|| Feature | What It Means |
    34|    34||---------|---------------|
    35|    35|| **Auto-Install Agents** | When a specialized agent (e.g. xxx) is needed but not installed, Partner auto-downloads, installs, and configures it — no manual setup. |
    36|    36|| **LLM Credential Sharing** | All agent subprocesses automatically receive Partner's API key and endpoint config. Configure once, use everywhere. |
    37|    37|| **CLI Argument Verification** | After installing any agent, Partner runs `--help` and validates manifest args against real CLI flags — catches configuration errors at install time, not runtime. |
    38|    38|| **Unified Agent Interface** | Any CLI/HTTP/Python agent can be registered via a JSON manifest and called with `call_agent_skill(agent="name", ...)`. |
    39|    39|| **Persistent Growth** | User corrections become reusable habits. Partner learns how you work and adjusts behavior. |
    40|    40|| **Event-Driven Pipeline** | Tasks are broken into events with dependencies, parallel execution, and automatic retry. |
    41|    41|| **World Model Simulation** | Plans are simulated before execution to predict risks and suggest optimizations. |
    42|    42|
    43|    43|---
    44|    44|
    45|    45|## Quick Start
    46|    46|
    47|    47|### Linux / WSL
    48|    48|
    49|    49|```bash
    50|    50|curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
    51|    51|partner setup
    52|    52|```
    53|    53|
    54|    54|### Windows
    55|    55|
    56|    56|PowerShell 5.1+:
    57|    57|
    58|    58|```powershell
    59|    59|powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
    60|    60|partner setup
    61|    61|```
    62|    62|
    63|    63|You can also download the Windows installer from
    64|    64|[GitHub Releases](https://github.com/zty522/partner/releases).
    65|    65|
    66|    66|### From Source
    67|    67|
    68|    68|```bash
    69|    69|git clone https://github.com/zty522/partner.git
    70|    70|cd partner
    71|    71|pip install -e .
    72|    72|partner setup
    73|    73|```
    74|    74|
    75|    75|---
    76|    76|
    77|    77|## Common Commands
    78|    78|
    79|    79|```bash
    80|    80|partner setup                 # configure workspace, backend, QQ bot, and instances
    81|    81|partner status                # show runtime and bot status
    82|    82|partner doctor                # check local environment (Python, Git, config, Hermes, OpenClaw)
    83|    83|partner agent list            # list all registered external agents
    84|    84|partner agent register path   # register a new agent manifest
    85|    85|partner agent info <name>     # show manifest details for an agent
    86|    86|partner agent health <name>   # check agent availability
    87|    87|partner agent call <name> ... # call an agent with a task
    88|    88|partner bot start qq          # start QQ bot and the mind runtime
    89|    89|partner bot stop qq           # stop QQ bot
    90|    90|partner tui                   # enter interactive TUI mode
    91|    91|partner world-model status    # check world model connection status
    92|    92|partner world-model configure # interactive world model setup
    93|    93|partner world-model test      # test world model connection
    94|    94|partner update                # pull latest code and reinstall
    95|    95|partner instance list         # list all instances
    96|    96|partner ollama setup          # configure optional Ollama local/remote model pool
    97|    97|partner server add            # add a remote SSH server
    98|    98|```
    99|    99|
   100|   100|---
   101|   101|
   102|   102|## Architecture
   103|   103|
   104|   104|### 1. Interaction Line
   105|   105|
   106|   106|User messages from QQ, CLI, or GUI enter the **interaction orchestrator**. The orchestrator builds selector context from conversation, pending questions, project status, mind pool, habits, and available events. The selector returns a structured route:
   107|   107|
   108|   108|```json
   109|   109|{
   110|   110|  "route": "direct_reply|mind_event|pause_project|none",
   111|   111|  "event_type": "direct_task|literature_review|evidence_audit|pdf_report|...",
   112|   112|  "objective": "A concrete objective for the next event",
   113|   113|  "reply_to_user": "...",
   114|   114|  "priority": 1
   115|   115|}
   116|   116|```
   117|   117|
   118|   118|### 2. Mind Event Line
   119|   119|
   120|   120|The mind runtime consumes persistent events from `state/mind_pool.json`.
   121|   121|
   122|   122|| Event | Purpose |
   123|   123||---|---|
   124|   124|| `direct_task` | Complete a one-shot user deliverable. |
   125|   125|| `call_agent_skill` | Forward task to an external agent. |
   126|   126|| `atomic_ensure_agent_installed` | Auto-download + install a specialized agent if missing. |
   127|   127|| `literature_review` | Gather and summarize sources. |
   128|   128|| `data_analysis` | Analyze data, metrics, or experiment output. |
   129|   129|| `evidence_audit` | Check claims, files, sources. |
   130|   130|| `artifact_build` | Build a visible artifact. |
   131|   131|| `pdf_report` | Generate a real PDF report. |
   132|   132|| `project_think` | Choose the next minimal project action. |
   133|   133|| `curiosity_explore` | Explore a meaningful follow-up question. |
   134|   134|| `habit_update` | Record a reusable behavior change. |
   135|   135|| `stop_project` | Explicitly stop or pause project execution. |
   136|   136|| `project` | Continue a long-running project lifecycle. |
   137|   137|| `reflection` | Re-evaluate progress and strategy. |
   138|   138|| `cross_project` | Transfer lessons across projects. |
   139|   139|| `memory_consolidate` | Compact memory into usable context. |
   140|   140|| `content_digest` | Turn user-shared content into hypotheses. |
   141|   141|| `wake_up` / `cron_tick` | Recover and pulse the runtime. |
   142|   142|
   143|   143|### 3. External Agent Dispatch
   144|   144|
   145|   145|`call_agent_skill` events are dispatched through the **external agent framework**:
   146|   146|
   147|   147|```text
   148|   148|partner/skills/external_agent_skills.py   — two-tier dispatch logic
   149|   149|partner/agents/manifest.py                 — AgentManifest dataclass + validation
   150|   150|partner/agents/registry.py                 — AgentRegistry: discover, register, health check
   151|   151|partner/agents/dispatcher.py               — AgentDispatcher: cli/http/python_api dispatch
   152|   152|partner/agents/manifests/*.json            — Built-in agent manifests
   153|   153|```
   154|   154|
   155|   155|```
   156|   156|Partner mind event (call_agent_skill)
   157|   157|    → external_agent_skills.py (two-tier dispatch)
   158|   158|        ├── General agents (hermes, openclaw, codex)
   159|   159|        │     └── adapter.chat() — subprocess-based chat
   160|   160|        └── Specialized agents (xxx, bamboo-ai, ...)
   161|   161|              └── AgentDispatcher._dispatch_cli()
   162|   162|                    ├── Load manifest from AgentRegistry
   163|   163|                    ├── Resolve CLI binary (PATH / conda / ~/.local/bin)
   164|   164|                    ├── Inject LLM credentials into env (auto)
   165|   165|                    ├── Substitute {placeholders} + {__llm_*__} in args
   166|   166|                    ├── Build command: [cmd] [preamble_args] [subcmd] [args]
   167|   167|                    └── Parse stdout JSON or plain text result
   168|   168|```
   169|   169|
   170|   170|---
   171|   171|
   172|   172|## Agent Interface — External Agent Architecture
   173|   173|
   174|   174|v1.0.0 introduces a **standardized agent interface** for calling external agents from Partner.
   175|   175|
   176|   176|### How It Works
   177|   177|
   178|   178|Every agent describes itself with a **manifest JSON** (`partner/agents/manifests/*.json`):
   179|   179|
   180|   180|```json
   181|   181|{
   182|   182|  "name": "xxx",
   183|   183|  "version": "2.0.0",
   184|   184|  "description": "专用数据分析专用 Agent",
   185|   185|  "capabilities": ["trajectory_inference", "cell_dynamics"],
   186|   186|  "endpoint_type": "cli",
   187|   187|  "endpoint_config": {
   188|   188|    "command": "xxx-agent",
   189|   189|    "subcommand": "run",
   190|   190|    "preamble_args": [
   191|   191|      "--llm-base-url", "{__llm_base_url__}",
   192|   192|      "--llm-model", "{__llm_model__}"
   193|   193|    ],
   194|   194|    "args": [
   195|   195|      "-o", "{output}", "-q", "{question}",
   196|   196|      "-d", "{device}",
   197|   197|      "--analyses", "custom_task", "analysis_modules",
   198|   198|      "--report-format", "md",
   199|   199|      "{input}"
   200|   200|    ]
   201|   201|  },
   202|   202|  "install_info": {
   203|   203|    "method": "pip",
   204|   204|    "package": "xxx-agent",
   205|   205|    "source": "git+#.git",
   206|   206|    "llm_config": {
   207|   207|      "type": "file",
   208|   208|      "path": "~/.agent/config.json",
   209|   209|      "template": {
   210|   210|        "llm": { "api_key": "__api_key__", "base_url": "__base_url__" }
   211|   211|      }
   212|   212|    }
   213|   213|  }
   214|   214|}
   215|   215|```
   216|   216|
   217|   217|Supported `endpoint_type` values:
   218|   218|- **cli**: Invoke via subprocess — supports `preamble_args` (before subcommand), `subcommand`, and `args` (after)
   219|   219|- **http**: REST API call with JSON payload
   220|   220|- **python_api**: Direct Python import and function call
   221|   221|- **mcp**: MCP protocol (future)
   222|   222|
   223|   223|### Parameter Substitution
   224|   224|
   225|   225|| Placeholder | Source | Example |
   226|   226||---|---|---|
   227|   227|| `{input}`, `{output}`, `{question}` | Planner task parameters | `/data/pancreas.h5ad` |
   228|   228|| `{device}` | Task parameter, defaults to `cpu` | `cpu` or `cuda` |
   229|   229|| `{__llm_api_key__}` | Auto-injected from Partner env | `sk-d...` |
   230|   230|| `{__llm_base_url__}` | Auto-injected from Partner env | `https://api.deepseek.com` |
   231|   231|| `{__llm_model__}` | Auto-injected from Partner env | `deepseek-v4-flash` |
   232|   232|| `{__llm_provider__}` | Auto-injected from Partner env | `deepseek` |
   233|   233|
   234|   234|### Agent Discovery
   235|   235|
   236|   236|AgentRegistry searches manifests from multiple locations (in priority order):
   237|   237|1. **Built-in**: `partner/agents/manifests/` (shipped with Partner)
   238|   238|2. **Workspace config**: `<workspace>/config/agents/`
   239|   239|3. **Global config**: `global_config/agents/` at project root
   240|   240|4. **User-registered**: `~/.partner/agents/`
   241|   241|
   242|   242|### Adding a New External Agent
   243|   243|
   244|   244|```bash
   245|   245|# 1. Create a manifest JSON with endpoint_config + optional install_info
   246|   246|partner/agents/manifests/my-agent.json
   247|   247|
   248|   248|# 2. Register it
   249|   249|partner agent register --manifest my-agent.json
   250|   250|
   251|   251|# 3. Call it
   252|   252|call_agent_skill(agent="my-agent", task="analyze this data")
   253|   253|```
   254|   254|
   255|   255|If `install_info` is defined, Partner will auto-download and install the agent
   256|   256|on first use via `atomic_ensure_agent_installed`.
   257|   257|
   258|   258|---
   259|   259|
   260|   260|## Auto-Install of Specialized Agents
   261|   261|
   262|   262|Partner can automatically download, install, and configure specialized agents
   263|   263|without any manual steps. This is the **two-event pattern**:
   264|   264|
   265|   265|```text
   266|   266|step1: atomic_ensure_agent_installed(agent="xxx")
   267|   267|       → health check: already installed? skip.
   268|   268|       → not installed? pip install / git clone / npm install
   269|   269|       → write LLM config file (auto-inherits Partner's API key)
   270|   270|       → verify CLI: run --help, validate manifest args
   271|   271|       → register manifest
   272|   272|step2: call_agent_skill(agent="xxx", task="...")
   273|   273|       → dispatch to the real CLI with full credential injection
   274|   274|```
   275|   275|
   276|   276|### Supported Install Methods
   277|   277|
   278|   278|| Method | What It Runs | Use Case |
   279|   279||--------|-------------|----------|
   280|   280|| `pip` | `pip install <package or source>` | Python packages on PyPI/GitHub |
   281|   281|| `git` | `git clone` + optional `pip install -e` | Development repos |
   282|   282|| `npm` | `npm install -g <package>` | Node.js CLIs |
   283|   283|| `go` | `go install <package>@latest` | Go binaries |
   284|   284|| `cargo` | `cargo install <package>` | Rust tools |
   285|   285|| `script` | Custom shell script | Complex multi-step installs |
   286|   286|
   287|   287|### Post-Install Verification
   288|   288|
   289|   289|After every install, Partner automatically:
   290|   290|1. Runs the agent's `--help` command
   291|   291|2. Extracts all expected flags from the manifest's `args` + `preamble_args`
   292|   292|3. Verifies each flag exists in the help output
   293|   293|4. Logs a warning with the exact file path if flags mismatch
   294|   294|5. Builds a dry-run dispatch command and logs it for debugging
   295|   295|
   296|   296|---
   297|   297|
   298|   298|## LLM Credential Auto-Injection
   299|   299|
   300|   300|All agent subprocesses automatically receive Partner's LLM credentials.
   301|   301|**Three layers** ensure the agent can call its LLM without manual configuration:
   302|   302|
   303|   303|### Layer 1 — Environment Variables (always on)
   304|   304|
   305|   305|Every subprocess gets:
   306|   306|- `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` — from Partner's config
   307|   307|- `OPENAI_BASE_URL` — auto-set to DeepSeek endpoint when key starts with `sk-d`
   308|   308|- `PARTNER_PROVIDER`, `HERMES_MODEL` — model selection
   309|   309|
   310|   310|### Layer 2 — CLI Args via `{__llm_*__}` Placeholders
   311|   311|
   312|   312|Manifests use reserved placeholders that auto-resolve:
   313|   313|```json
   314|   314|"preamble_args": [
   315|   315|  "--llm-base-url", "{__llm_base_url__}",
   316|   316|  "--llm-model", "{__llm_model__}"
   317|   317|]
   318|   318|```
   319|   319|
   320|   320|### Layer 3 — Config File via `install_info.llm_config`
   321|   321|
   322|   322|After install, Partner writes the agent's LLM config file:
   323|   323|```json
   324|   324|"llm_config": {
   325|   325|  "type": "file",
   326|   326|  "path": "~/.cellcompass/config.json",
   327|   327|  "template": {
   328|   328|    "llm": { "api_key": "__api_key__", "base_url": "__base_url__" }
   329|   329|  }
   330|   330|}
   331|   331|```
   332|   332|
   333|   333|The `__api_key__`, `__base_url__`, `__model__`, `__provider__` tokens
   334|   334|are replaced with Partner's actual credentials at install time.
   335|   335|
   336|   336|---
   337|   337|
   338|   338|## External Code Integration
   339|   339|
   340|   340|### xxx Agent
   341|   341|
   342|   342|Partner v1.0.0 supports **xxx Agent** ((placeholder) — a specialized CLI agent registered via auto-discovery.
   343|   343|
   344|   344|- **No wrapper needed**: Partner calls `xxx-agent` directly — the real CLI.
   345|   345|- **LLM credentials auto-injected**: `~/.cellcompass/config.json` is written with Partner's API key at install time.
   346|   346|- **Dispatch**: `call_agent_skill(agent="xxx", ...)` → `xxx-agent --llm-* run -o OUTPUT -q QUESTION -d DEVICE --analyses custom_task analysis_modules INPUT.h5ad`
   347|   347|- **Auto-install**: First use triggers `atomic_ensure_agent_installed` which runs `pip install xxx-agent`.
   348|   348|- **CLI argument verification**: After install, Partner runs `xxx-agent run --help` and validates all manifest flags match.
   349|   349|
   350|   350|### Adding Other External Agents
   351|   351|
   352|   352|The same pattern applies to any CLI agent:
   353|   353|
   354|   354|1. Create a manifest JSON in `partner/agents/manifests/`
   355|   355|2. Add `install_info` to enable auto-download on first use
   356|   356|3. Add `llm_config` to auto-configure LLM credentials
   357|   357|4. Call it via `call_agent_skill(agent="<name>", task="...")`
   358|   358|
   359|   359|You do **not** need to clone the external agent's repository into Partner's workspace. Partner only needs:
   360|   360|- The manifest describing how to call it
   361|   361|- `install_info` describing how to install it
   362|   362|- `llm_config` describing where to write credentials
   363|   363|
   364|   364|---
   365|   365|
   366|   366|## v1.0.0 Key Upgrades
   367|   367|
   368|   368|| Area | Change |
   369|   369||---|---|
   370|   370|| **Auto-Install Agents** | `atomic_ensure_agent_installed` event + `install_info` in manifests — agents install themselves on first use |
   371|   371|| **LLM Credential Sharing** | Three-layer injection (env vars + CLI placeholders + config file) — configure once, all agents inherit |
   372|   372|| **CLI Argument Verification** | Post-install `--help` parsing validates manifest args against real CLI — catch errors at install time |
   373|   373|| **Agent Interface** | Standardized AgentRegistry + AgentDispatcher + AgentManifest system for pluggable external agents |
   374|   374|| **CLI** | Monolithic `cli.py` → modular `partner/cli/` package with subcommand modules |
   375|   375|| **Desktop GUI** | Refactored PySide6 GUI with modern pages (agents, settings, conversation) |
   376|   376|| **Harness Core** | New `robust_executor`, `task_instance`, `artifact_validator`, `remediation_handler` |
   377|   377|| **Skill System** | `partner/skills/` with registry, store, discovery, external agent skills |
   378|   378|| **LLM Layer** | Centralized `partner/llm/` module for provider-agnostic inference |
   379|   379|| **External Agents** | `partner/agents/` package — manifests, registry, dispatcher for any CLI/HTTP/MCP agent |
   380|   380|| **Project Planner** | `partner/planner/` batch planner + prompt builder |
   381|   381|| **World Model** | `partner/world_model/` and HTTP server |
   382|   382|| **QQ Bot** | Replaced old qq_official_bot/bridge with modular `partner/qq_bot/` |
   383|   383|
   384|   384|---
   385|   385|
   386|   386|## Workspace Layout
   387|   387|
   388|   388|```text
   389|   389|partner_workspace/
   390|   390|├── config/                          # All unified config files
   391|   391|│   ├── partner_config.json          # Main runtime config per workspace
   392|   392|│   ├── qq_config.json               # QQ bot credentials and settings
   393|   393|│   ├── global_config.json           # Global (cross-instance) settings
   394|   394|│   ├── gui_bridge.json              # Desktop GUI bridge config
   395|   395|│   ├── external_calls.yaml          # External API call definitions
   396|   396|│   └── routing_rules.yaml           # Event routing rules
   397|   397|├── instances/                       # Per-instance workspaces (03/, 05/, ...)
   398|   398|│   └── <id>/
   399|   399|│       ├── dialogue/                # Daily QQ logs (YYYY-MM-DD.log), chat history
   400|   400|│       ├── state/                   # Runtime state
   401|   401|│       │   ├── desktop_inbox.jsonl  # Messages from desktop GUI
   402|   402|│       │   ├── harness_runs.jsonl   # Harness execution records
   403|   403|│       │   ├── mind_pool.json       # Event queue
   404|   404|│       │   ├── event_decisions.jsonl
   405|   405|│       │   ├── tasks/               # Task execution state
   406|   406|│       │   ├── logs/                # Per-instance log files
   407|   407|│       │   └── user/                # User-facing output
   408|   408|│       ├── system/                  # Internal runtime
   409|   409|│       │   ├── hermes_work/         # Hermes agent working directory
   410|   410|│       │   ├── hermes_home/         # Hermes agent home
   411|   411|│       │   ├── habits/              # Reusable behavior habits
   412|   412|│       │   ├── mind/                # Mind runtime state
   413|   413|│       │   ├── reflections/         # Agent reflections
   414|   414|│       │   ├── growth/              # Growth events
   415|   415|│       │   ├── content_feed/        # Content exploration state
   416|   416|│       │   └── ...                  # (hippocampus, synapses, strategy, ideas, checks)
   417|   417|│       ├── projects/                # (legacy — now centralized to shared_projects/)
   418|   418|│       └── partner_config.json      # Per-instance agent/LLM config
   419|   419|├── shared_projects/                 # Centralized project pool with .lock mechanism
   420|   420|│   ├── registry.json                # Project ownership and metadata registry
   421|   421|│   └── <safe_project_name>/
   422|   422|│       ├── state.md                 # Current project state summary
   423|   423|│       ├── project_brief.md         # Project brief and objectives
   424|   424|│       ├── project_contract.json    # (optional) Execution contract
   425|   425|│       ├── exploration_log.md       # Exploration history
   426|   426|│       ├── files/                   # Project-specific working files
   427|   427|│       ├── outputs/                 # Generated outputs and artifacts
   428|   428|│       └── reports/                 # PDF reports and papers
   429|   429|├── files/                           # Shared uploaded / incoming / outgoing / working files
   430|   430|│   ├── uploads/
   431|   431|│   ├── incoming/
   432|   432|│   ├── outgoing/
   433|   433|│   └── working/
   434|   434|├── shared_mind/                     # Cross-instance shared memory
   435|   435|│   ├── habits.json
   436|   436|│   ├── growth_events.jsonl
   437|   437|│   ├── episodes.jsonl
   438|   438|│   └── semantic_memory.md
   439|   439|├── conversations/                   # Per-conversation-round pipeline snapshots
   440|   440|│   └── <timestamp>/
   441|   441|└── logs/                            # Runtime logs (agent_runs.jsonl, hermes_chat.jsonl)
   442|   442|```
   443|   443|
   444|   444|---
   445|   445|
   446|   446|## Agent Backends
   447|   447|
   448|   448|| Backend | Notes |
   449|   449||---|---|
   450|   450|| `hermes` | Main backend for tool-using staged execution. |
   451|   451|| `codex` | Useful for code-heavy tasks and repository edits. |
   452|   452|| `openclaw` | Optional OpenClaw Gateway backend. |
   453|   453|| `xxx` | Single-cell trajectory inference (auto-installed on first use). |
   454|   454|| `direct` | Minimal built-in fallback. |
   455|   455|
   456|   456|Each backend is described by an `AgentManifest` and dispatched through the unified interface.
   457|   457|
   458|   458|---
   459|   459|
   460|   460|## Design Principles
   461|   461|
   462|   462|- Event first: always decide the next event before execution.
   463|   463|- LLM selector first: semantic routing belongs to the selector, not hard-coded rules.
   464|   464|- Code as guardrail: code persists, validates, queues, filters, and sends.
   465|   465|- One event, one small closure: each backend call should complete one verifiable action.
   466|   466|- Evidence before claims: files, paths, sources, and metrics must be real before becoming evidence.
   467|   467|- Growth over patching: user corrections become reusable habits or growth, not one-off if statements.
   468|   468|- **External agents by manifest, not by fork**: external agent code lives in its own repo; Partner only ships a manifest.
   469|   469|- **Auto-configure, never ask**: Partner's LLM credentials are automatically shared with every agent subprocess.
   470|   470|- **Verify at install, not at runtime**: CLI args are checked against `--help` during install, not during dispatch.
   471|   471|- PDF as a real event: final PDF reports should be generated by `pdf_report`.
   472|   472|- Stop explicitly: project execution should stop through `stop_project`.
   473|   473|
   474|   474|---
   475|   475|
   476|   476|## 第三方代码声明 / Third-Party Notices
   477|   477|
   478|   478|This project incorporates design patterns and code inspired by the following open-source projects:
   479|   479|
   480|   480|- **Hermes Agent** (MIT) — https://github.com/nousresearch/hermes-agent
   481|   481|- **Hermes Desktop** (MIT) — https://github.com/fathah/hermes-desktop
   482|   482|- **OpenClaw** (MIT) — https://github.com/openclaw/openclaw
   483|   483|- **OpenClaw Windows Hub** (MIT) — https://github.com/openclaw/openclaw-windows-node
   484|   484|- **xxx Agent** (MIT) — #
   485|   485|
   486|   486|See `NOTICE.md` for full license texts and attribution details.
   487|   487|
   488|   488|**Important**: The xxx agent is an external dependency. Partner's repo only contains a manifest JSON describing how to call it. The actual xxx source code is maintained at its own repository and installed via `pip install xxx-agent`. The MIT license attribution in NOTICE.md covers the manifest integration layer only.
   489|   489|
   490|   490|---
   491|   491|
   492|   492|## Current Limitations
   493|   493|
   494|   494|- Event selection quality depends on the selector backend.
   495|   495|- Long-term memory needs consolidation to stay useful.
   496|   496|- Growth events influence execution but are not a formal policy engine.
   497|   497|- Evidence audit cannot replace human scientific judgment.
   498|   498|- Different agent backends behave differently.
   499|   499|- QQ file delivery depends on bot platform constraints.
   500|   500|- External agents must be installable via one of the supported methods (pip/git/npm/go/cargo/script).
   501|