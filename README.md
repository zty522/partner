<div align="center">

# Partner

### An event-driven AI companion for long-running work

Partner is not a chatbot and not a normal agent runner.
It is a runtime for staged execution, persistent memory, habits, growth, and event-by-event collaboration.

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

Partner is built around staged execution. Each stage has a goal, an event, an action, a result, and a follow-up decision. A backend agent still performs concrete work, but Partner keeps continuity outside the agent call.

This makes Partner closer to a long-term collaborator:

- it remembers project context and ordinary conversation context
- it can ask for missing parameters instead of guessing
- it can reconnect a user's answer to the previous unfinished request
- it can choose whether the next event should be search, audit, artifact creation, PDF reporting, curiosity exploration, or project stop
- it records habits, growth, risks, failures, and cross-project lessons
- it reports at event boundaries instead of sending vague background updates

Partner does not reject tools or skills. Tools and skills answer "how should this step be executed?" Partner also asks "is this the right next step now?"

---

## v0.7.0 Highlights

Partner v0.7.0 moves the runtime from project-loop autonomy toward an event-first companion architecture.

- **Event-first selector**: user messages are routed through an LLM selector that sees context, pending follow-ups, project state, mind pool status, and available events.
- **Action events**: direct tasks, literature review, data analysis, evidence audit, artifact build, PDF report, project thinking, curiosity exploration, habit update, and stop project are explicit event types.
- **PDF report event**: PDF generation is no longer hidden inside artifact building. It is a first-class `pdf_report` event.
- **Explicit project stop**: long-running execution should stop through `stop_project`, not accidental silence.
- **Event-boundary user updates**: after each event, Partner can tell the user what event ran, what changed, what files were created, and what event is planned next.
- **Conversation continuity**: ordinary chat context and pending parameter questions are tracked separately from project memory.
- **Growth influences execution**: recent growth events are injected into action-event prompts alongside shared habits.
- **Less hard-coded conversation logic**: duplicate suppression, missing-parameter handling, and follow-up routing are handled by selector context rather than fixed user-message rules.
- **QQ inbound safety**: inbound messages are preserved; Partner should ask for missing information instead of inventing defaults.

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
partner bot start qq          # start QQ bot and the mind runtime
partner bot stop qq           # stop QQ bot
partner update                # pull latest code and reinstall
```

Configure servers used by remote instances, GUI operations, and Ollama tunnel hints:

```bash
partner server add \
  --name server-prod-01 \
  --host 203.0.113.10 \
  --user ubuntu \
  --port 22 \
  --key ~/.ssh/partner_server.pem \
  --remote-workspace /home/ubuntu/partner_workspace/instances/01 \
  --print-tunnel

partner server list
partner server tunnel-hint server-prod-01
partner server remove server-prod-01
```

Configure one or more Ollama endpoints:

```bash
partner ollama setup
partner ollama add --name local --base-url http://127.0.0.1:11434 --models qwen2.5:7b --mode lite --location local
partner ollama add --name server-gpu --base-url http://127.0.0.1:11434 --models qwen2.5:14b,qwen2.5:7b --mode project --location server --server server-prod-01
partner ollama list
partner ollama test
partner ollama mode lite
partner ollama disable
```

Ollama mode controls where Partner may use local models:

| Mode | Behavior |
| --- | --- |
| `off` | Never use Ollama. |
| `lite` | Use Ollama only for lightweight classification, short replies, and short status text. Project execution still uses the main backend. |
| `project` | Project execution may try Ollama first. Lightweight replies still use the main backend. |
| `all` | Lightweight routing, replies, reports, and project execution may try Ollama first. |

If an endpoint or model is unavailable, Partner falls back to the main backend.

Expose your local computer's Ollama to a server instance with an SSH reverse tunnel:

```bash
# Run on your local Windows PowerShell, Linux shell, or WSL shell.
ssh -N -R 11434:127.0.0.1:11434 -i ~/.ssh/partner_server.pem -p 22 ubuntu@203.0.113.10

# Run in the Partner workspace that the server instance uses.
partner ollama add --name local-pc --base-url http://127.0.0.1:11434 --models qwen2.5:7b --mode lite --location tunnel --server server-prod-01
partner ollama test
```

This only requires the server instance to reach `http://127.0.0.1:11434` on itself. The model actually runs on your local computer.

Run one instance directly:

```bash
python3 -m partner --instance-id 01 --workspace /path/to/partner_workspace/instances/01
```

Manage multiple instances:

```bash
partner-manager list
partner-manager start --id 01
partner-manager stop --id 01
partner-manager restart --id 01
partner-manager logs --id 01 --tail 80
partner-manager start --all
partner-manager stop --all
```

---

## Architecture

Partner has two connected lines.

### 1. Interaction Line

User messages from QQ, CLI, or GUI enter the interaction orchestrator first.
The orchestrator builds selector context from:

- recent conversation
- pending missing-parameter questions
- active project status
- current progress
- mind pool statistics
- shared habits
- available events

The selector returns a structured route:

```json
{
  "route": "direct_reply|mind_event|pause_project|none",
  "event_type": "direct_task|literature_review|evidence_audit|pdf_report|project|...",
  "event_kind": "make_excel",
  "objective": "A concrete objective for the next event",
  "reply_to_user": "A natural user-facing reply if needed",
  "stop_after_completion": true,
  "priority": 1
}
```

Code then persists the decision, queues events, sends replies, or updates memory.

### 2. Mind Event Line

The mind runtime consumes persistent events from `state/mind_pool.json`.

| Event | Purpose |
| --- | --- |
| `direct_reply` | Reply without backend execution. |
| `direct_task` | Complete a one-shot user deliverable. |
| `literature_review` | Gather and summarize sources. |
| `data_analysis` | Analyze data, metrics, or experiment output. |
| `evidence_audit` | Check claims, files, sources, and result trustworthiness. |
| `artifact_build` | Build a visible artifact. |
| `pdf_report` | Generate a real PDF report from available results. |
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

After an action event completes, Partner runs a follow-up selector. The next event may be `pdf_report`, `evidence_audit`, `curiosity_explore`, `artifact_build`, `stop_project`, or another project action.

---

## Memory and Growth

Partner separates ordinary conversation from project memory.

| Layer | Role |
| --- | --- |
| Conversation context | Recent dialogue and pending follow-ups. |
| Project state | Goals, constraints, active plan, artifacts, evidence, and logs. |
| Experience memory | Episodes, user signals, failed methods, useful methods, and risks. |
| Habits | Stable behavior tendencies injected into execution prompts. |
| Growth events | User-visible behavior changes learned from feedback or evidence. |
| Cross-project memory | Transfer candidates and reusable lessons. |

Habits and recent growth events are injected into action-event prompts. They influence execution, but they do not override explicit user requirements.

Example:

```text
User: "Do not default missing weather location to Guangzhou."
Partner growth: "When a task lacks required parameters, ask instead of inventing defaults."
Future event: selector chooses direct_reply to ask for the location.
```

---

## Workspace Layout

Recommended workspace:

```text
partner_workspace/
  global_config.json
  instances/
    01/
      00_config/
      10_logs/
      20_records/
        active_project.txt
        projects/
          <project>/
            project_brief.md
            project_contract.json
            state.md
            exploration_log.md
            trace_detail.md
            memory_index.json
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

The most important user-facing folder is `user/`.
Internal runtime state lives under `system/`, `state/`, `logs/`, and `20_records/`.

---

## Agent Backends

Partner uses an adapter layer and is not tied to one backend.

| Backend | Notes |
| --- | --- |
| `hermes` | Main backend for tool-using staged execution. |
| `codex` | Useful for code-heavy tasks and repository edits. |
| `openclaw` | Optional OpenClaw Gateway backend. |
| `direct` | Minimal built-in fallback. |

Important files:

```text
partner/interaction_orchestrator.py
partner/mind/event_types.py
partner/mind/pool.py
partner/mind/executor.py
partner/research_memory.py
partner/project_state.py
partner/outbound_policy.py
```

---

## QQ Official Bot

Partner can connect to QQ official bots. Each instance can use its own QQ bot account and workspace.

```bash
partner bot start qq
partner bot stop qq
```

The QQ bridge should:

- send a short thinking message when a user message enters processing
- preserve inbound user text
- avoid default parameter supplementation
- avoid duplicate local and LLM replies
- hide raw backend exceptions
- push files when an event creates user-visible artifacts
- show event labels in user-facing replies

---

## Design Principles

- Event first: always decide the next event before execution.
- LLM selector first: semantic routing belongs to the selector, not hard-coded message rules.
- Code as guardrail: code persists, validates, queues, filters, and sends.
- One event, one small closure: each backend call should complete one verifiable action.
- Evidence before claims: files, paths, sources, and metrics must be real before becoming evidence.
- Growth over patching: user corrections should become reusable habits or growth, not one-off if statements.
- Projects are containers: they provide context but should not become the default route for every message.
- PDF as a real event: final PDF reports should be generated by `pdf_report`.
- Stop explicitly: project execution should stop through `stop_project`.
- User can intervene naturally: the user can correct, add parameters, change direction, or stop at any time.

---

## Current Limitations

Partner is still an early prototype.

- event selection quality depends on the selector backend
- long-term memory needs consolidation to stay useful
- growth events influence execution, but they are not a formal policy engine
- evidence audit cannot replace human scientific judgment
- different agent backends behave differently
- QQ file delivery depends on bot platform constraints
- content digestion is limited by public access and available parsers

---

## One-Sentence Summary

Partner is an event-driven AI companion that works in stages, executes one verifiable action at a time, analyzes the result, updates memory and habits, and uses that growth to choose the next event like a long-term collaborator.
