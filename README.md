<div align="center">

# Partner

### A long-running AI research companion

Partner is not a one-shot chatbot and not a normal agent runner.
It is designed to stay beside your research projects, remember what happened,
generate its own next steps, and report back when there is something worth reading.

[![Latest Release](https://img.shields.io/github/v/release/zty522/partner?label=Latest&style=flat-square)](https://github.com/zty522/partner/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

</div>

---

## What Partner Is

Most AI tools are reactive:

```text
LLM:     user asks -> model answers -> session ends
Agent:   user commands -> agent executes -> waits for the next command
Partner: background lifeline keeps working -> user can intervene anytime -> Partner reports and adapts
```

Partner's core breakthrough is that it does not only complete the user's current instruction.
After the user gives an initial direction, Partner can create follow-up instructions by itself:

```text
initial project direction
  -> inspect current state
  -> choose one small next action
  -> execute it
  -> write evidence
  -> update memory
  -> reflect on failure or progress
  -> generate the next internal instruction
```

This makes Partner closer to a long-term research student or collaborator:
it accumulates context, failed attempts, method boundaries, advisor suggestions,
user inspirations, and stage results across many rounds.

Partner is not implemented as an LLM that streams forever.
The stable design is:

```text
external long-term memory + fixed research habits + small execution loops + periodic reflection
= long-term research companion
```

Each LLM or agent call may finish, but Partner writes the important state to files,
queues, and memory stores so the next round can continue the same project.

---

## What Partner Is Good For

Partner is intended for long, iterative, uncertain work:

- literature review and research trend synthesis
- machine learning experiments, tuning, and result analysis
- bioinformatics, molecular generation, docking, and scientific pipelines
- benchmark design and agent evaluation framework development
- code debugging and experiment recovery
- project logs, reports, slides, and stage summaries
- cross-project transfer of methods and failed lessons
- absorbing user-shared articles, posts, videos, and social-media ideas as hypotheses

It is especially useful when progress happens slowly:
one day a method fails, the next day an advisor mentions a paper,
or a method from another project suddenly becomes relevant.

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
partner setup                 # configure workspace, backend, QQ bot, instances
partner status                # show runtime and bot status
partner bot start qq          # start QQ bot and autonomous mind system
partner bot stop qq           # stop QQ bot
partner update                # pull latest code and reinstall
```

Run an instance directly:

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

## v0.6.0 Highlights

Partner v0.6.0 focuses on turning Partner from a simple autonomous runtime into a
long-term research companion.

Major changes:

- **Continuous project lifeline**: active projects keep moving by re-queuing project work after each round instead of relying only on fixed cron intervals.
- **Interaction orchestrator**: user messages are first interpreted by a lightweight LLM layer, which decides whether to reply only, add a note, switch project, correct direction, or mutate the lifeline.
- **Long-term research memory**: project cards, lessons, user ideas, risk events, growth events, and cross-project transfer candidates are stored outside the prompt.
- **Research habits**: Partner now has explicit habits for minimal execution loops, evidence writing, leakage checks, report deduplication, memory consolidation, and reflection.
- **Brain-inspired design**: hippocampus-like episodes, synapse-like method memory, prefrontal project contracts, amygdala-like risk checks, cerebellum-like habits, and default-mode reflection.
- **Stage reports**: when a project accumulates enough work, Partner can generate Markdown, PPTX, and PDF stage reports in the user-facing project folder.
- **Better user-facing reports**: internal errors, file bookkeeping, repeated messages, and low-value "I will continue" messages are filtered.
- **Multi-modal/content feed groundwork**: user-shared public content from articles, posts, videos, or links is recorded as hypotheses and can be digested into project actions.
- **Codex adapter support**: Codex can be selected as an agent backend in addition to Hermes and Direct mode.
- **Desktop entry updates**: Windows batch/VBS launchers and GUI assets were updated for a cleaner desktop experience.

---

## Architecture

Partner has two connected lines.

### 1. Interaction Line

User messages from QQ or CLI do not directly rewrite the project.
They go through `InteractionOrchestrator`, which uses a lightweight LLM decision:

```text
user message
  -> understand intent
  -> reply to user
  -> decide whether the lifeline needs a mutation
  -> code applies the mutation to state, memory, or queue
```

Possible lifeline actions include:

- `none`
- `add_task`
- `switch_project`
- `add_note`
- `add_knowledge`
- `correct_direction`
- `process_shared_content`

The LLM interprets the message.
Code writes the result.
This avoids brittle hard-coded patches while also avoiding raw LLM state mutation.

### 2. Background Lifeline

The background lifeline is the autonomous mind loop.
It uses persistent events:

| Event | Purpose |
| --- | --- |
| `WAKE_UP` | Recover the active project after startup or restart. |
| `CRON_TICK` | Self-pulse for health checks, recovery, reflection, and report checks. |
| `PROJECT` | Execute one concrete project step. |
| `REPORT` | Send a concise user-facing progress update. |
| `REFLECTION` | Run independent reflection on progress, failure, and strategy. |
| `CROSS_PROJECT` | Search for transferable methods between projects. |
| `MEMORY_CONSOLIDATE` | Compact hot files and long-term memory. |
| `CONTENT_DIGEST` | Digest user-shared or self-collected content into hypotheses. |
| `CONTENT_PATROL` | Optional public-content patrol for dedicated content-learning instances. |

The event queue is stored in:

```text
state/mind_pool.json
```

The pool deduplicates events so the same project does not get queued repeatedly
and produce duplicate local + LLM replies.

---

## One Project Round

A normal active project round looks like this:

```text
1. WAKE_UP or CRON_TICK finds active_project.txt
2. MindPool checks whether the project is already queued or running
3. PROJECT reads project_brief.md, project_contract.json, state.md, and compact memory
4. Executor builds a small project prompt
5. Adapter calls Hermes, Codex, or another backend
6. Agent returns a structured result
7. Partner parses ACTION / DONE / FINDINGS / EVIDENCE / NEXT / FILES / STATE_DELTA
8. Code writes state, logs, memory, and artifact files
9. Evidence and progress-quality checks run
10. A user-facing report is generated only if there is real content
11. The project is queued again for the next minimal step
```

Partner should not ask:

```text
Do you want me to continue?
Which direction should I choose?
```

By default, it should continue:

```text
stage completed -> summarize evidence -> find next bottleneck -> choose one minimal action -> keep going
```

Users can always interrupt, correct direction, switch project, or stop.

---

## Research Habits

Partner's behavior is driven by habits, not by a giant personality prompt.

### Execution Habits

| Habit | Behavior |
| --- | --- |
| Minimal closed loop | One `PROJECT` event should do one small useful action. |
| Execute before summarizing | If a task requires code, data, download, or analysis, the backend should actually run it first. |
| Evidence required | A result needs evidence; otherwise it is marked as `hypothesis`. |
| No user blocking | Partner does not ask the user what to do next unless the user explicitly requests a choice. |
| No file-operation progress | "Updated a file" or "directory is complete" is not a research result. |
| No internal-error exposure | Backend errors and structured-output failures should not be sent as raw user messages. |

### Periodic Habits

These time/round-based behaviors are also habits:

| Habit | Default Trigger | Output |
| --- | --- | --- |
| Active project work | Re-queued after each project round | `state.md`, `exploration_log.md`, artifacts |
| Short reflection | About every 8 project steps | `reflection_note.md`, long-term memory |
| Stage report | About every 24 project steps and at least 12 hours after the previous one | Markdown + PPTX + PDF |
| User report | On meaningful progress or user check-in | QQ / runtime log |
| Memory consolidation | Periodic or after restart | compact project brief and memory files |
| Cross-project thinking | Periodic | `system/reflections/cross_project_thinking_*.md` |
| Content digestion | User-shared content or content patrol | `system/content_feed/`, idea memory |
| Startup recovery | Process start / machine restart | restored `mind_pool.json` and active project |

`CRON_TICK` is a safety pulse, not the only driver.
Active projects should continue through `PROJECT -> PROJECT -> PROJECT`.

---

## Brain-Inspired Design

The brain metaphor is not used to make Partner talk like a person.
It makes the source of the next internal instruction explainable.

| Brain-like module | Partner mechanism | Role |
| --- | --- | --- |
| Hippocampus | `system/hippocampus/episodes.jsonl` | Remember user corrections, advisor suggestions, key project events. |
| Synapses | `method_memory.jsonl`, `cross_project_lessons.md` | Store methods, failures, boundaries, and transfer candidates. |
| Prefrontal cortex | `project_contract.json`, `active_plan.json`, `project_brief.md` | Maintain goals, constraints, current mainline, and project boundaries. |
| Cerebellum | habits + mind loop action set | Enforce stable research habits such as minimal loops and evidence writing. |
| Amygdala | risk events + evidence audit | Detect hallucination, leakage, missing paths, direction drift, and repeated pseudo-progress. |
| Default mode network | `REFLECTION`, `CROSS_PROJECT` | Reflect, reinterpret failures, and find cross-project opportunities. |

Example flow:

```text
user correction
  -> episode memory records it
  -> project contract updates the boundary
  -> habits keep the next step small
  -> method memory supplies failed lessons
  -> risk checks stop suspicious results
  -> reflection creates the next internal instruction
```

---

## Evidence, Risk, and Growth

Partner should be skeptical of itself.

Common guardrails:

- abnormal metrics trigger leakage / overfitting / split-audit checks
- failed methods are stored as local method boundaries, not global truths
- social-media content is only a hypothesis until verified
- simulated benchmark results are not treated as real API/system results
- file paths mentioned by the agent must exist before they become evidence
- repeated version/file-count growth triggers progress-quality audit

Growth should be visible to the user.
For example, if the user says:

```text
This result may have data leakage.
```

Partner should not only reply once.
It should learn a reusable habit:

```text
future unusually good results -> run leakage / overfitting / validation-split audit first
```

User-visible growth surfaces:

- `user/current_project/exploration_log.md`
- `user/research_memory_summary.md`
- `user/partner_growth.md`
- `user/reports/<project>/`
- better QQ progress reports over time

---

## Stage Reports: Markdown, PPTX, PDF

After enough project work has accumulated, Partner can generate a stage report.

Default trigger:

```text
PARTNER_STAGE_REPORT_EVERY_ROUNDS=24
PARTNER_STAGE_REPORT_MIN_INTERVAL_HOURS=12
```

The agent first writes a structured Markdown report.
Partner then turns it into PPTX and PDF:

```text
user/reports/<project>/
user/current_project/reports/
```

Typical files:

```text
latest_stage_report.md
<project>_stage_report_YYYYMMDD_HHMM.pptx
<project>_stage_report_YYYYMMDD_HHMM.pdf
```

Reports are designed like research group updates:

- core conclusion first
- verified progress
- key evidence
- risk and audit
- failed methods and method boundaries
- next-stage plan

The report generator uses `python-pptx` and `reportlab`.
If LibreOffice is available, PPTX-to-PDF conversion may be used; otherwise
Partner writes a PDF directly from the Markdown report.

---

## Content Feed and Multi-Modal Groundwork

Partner can record user-shared external content:

- articles
- public posts
- videos
- links
- screenshots or attachments as placeholders
- user notes from social platforms such as Xiaohongshu, Bilibili, Zhihu, and WeChat public accounts

The rule is strict:

```text
external content -> hypothesis -> minimal verification action -> project memory
```

Partner should not treat a social-media post as evidence.
It should ask:

- What is the possible research signal?
- How does it relate to the current project?
- What is uncertain or promotional?
- What is the smallest verification step?

A dedicated instance, such as `05`, can be configured as a content-learning
Partner that patrols public sources and turns signals into hypotheses.

---

## Workspace Layout

Recommended workspace:

```text
partner_workspace/
  global_config.json
  instances/
    01/
      00_config/          # instance config
      10_logs/            # runtime logs
      20_records/         # project records
        active_project.txt
        projects/
          <project>/
            project_brief.md
            project_contract.json
            state.md
            exploration_log.md
            reports/
      state/              # mind pool, active plan, runtime state
      system/             # internal memory, habits, reflections, content feed
      logs/               # backend call traces
      user/               # user-facing view
        README.md
        current_project/
        reports/
      99_temp/
    03/
    04/
```

For users, the most important folder is:

```text
user/
```

Start with:

```text
user/current_project/summary.md
user/current_project/exploration_log.md
user/research_memory_summary.md
user/partner_growth.md
user/reports/<project>/
```

Internal runtime files live under `system/`, `state/`, `logs/`, and `20_records/`.

---

## Agent Backends

Partner uses an adapter layer.
It is not tied to one backend.

| Backend | Status | Notes |
| --- | --- | --- |
| `hermes` | Full support | Best for long-running research work; supports session resume and tool use. |
| `codex` | Supported | Runs `codex exec` inside the instance workspace; useful for code-heavy tasks. |
| `direct` | Built in | Minimal fallback without an external agent. |

Other agents can be added by implementing an adapter.

Important files:

```text
partner/adapter.py
partner/mind/executor.py
partner/interaction_orchestrator.py
```

Agent runtime metrics are written to:

```text
logs/agent_runs.jsonl
```

Recorded fields include backend, purpose, elapsed time, model, provider,
estimated tokens, return code, and output preview.

---

## Setup Behavior

`partner setup` should reuse previous configuration whenever possible:

- workspace path
- selected backend
- model/provider
- QQ official bot AppID/AppSecret
- instance list
- WSL Bridge choices
- research/report intervals

If an existing workspace is found, setup should show old values as defaults
instead of forcing the user to re-enter them.

---

## QQ Official Bot

Partner can connect to QQ official bots.
Each instance can have its own QQ bot account and project.

Example:

```bash
partner bot start qq
partner bot stop qq
```

Multi-instance example:

```bash
python3 -m partner --instance-id 01 --workspace /path/to/instances/01
python3 -m partner --instance-id 03 --workspace /path/to/instances/03
python3 -m partner --instance-id 04 --workspace /path/to/instances/04
```

The QQ bridge should:

- send a short "thinking" message only when useful
- not duplicate local and LLM replies
- not expose backend exceptions
- not ask the user for next direction by default
- forward user corrections into the interaction orchestrator
- push concise progress reports only when there is meaningful change

---

## Desktop App and Windows Launchers

Partner includes Windows-friendly launchers and GUI assets:

- `PartnerGUI.bat`
- `Partner.vbs`
- `partner/gui_qt.py`
- `partner/assets/icons/`
- `installer/installer.iss`

The Windows installer is built by GitHub Actions when a `v*` tag is pushed.
The release workflow uploads the generated `.exe` to the GitHub Release.

---

## Troubleshooting

### Partner has not messaged for a long time

Check:

```bash
pgrep -af "python3 -m partner|partner bot|QQ"
tail -80 partner_workspace/instances/01/10_logs/instance.log
cat partner_workspace/instances/01/state/mind_pool.json
```

Possible causes:

- process stopped
- QQ bridge disconnected
- active project missing
- all events are waiting
- backend call is still running
- report deduplication filtered repeated messages

### Partner reports internal errors

Check:

```text
partner/qq_official_bridge.py
partner/interaction_orchestrator.py
partner/mind/executor.py
logs/agent_runs.jsonl
```

Raw messages such as backend unavailable, max iterations, structured-output
failure, or timeout should be filtered before reaching the user.

### Partner drifts away from the project

Check:

```text
project_contract.json
project_brief.md
active_plan.json
system/hippocampus/episodes.jsonl
```

User corrections should be interpreted by the LLM and persisted into project
state/memory so future rounds can follow them.

### Partner only reports but does not make progress

Check whether:

- `PROJECT` events are actually running
- the backend returns only summaries
- evidence files are created
- `EVIDENCE` points to real files or outputs
- progress-quality audit is blocking pseudo-progress

### Prompts become too heavy

Check whether:

- hot files contain file lists or old logs
- memory consolidation is running
- `project_brief.md` is clean
- Hermes skills/memory inject too much context
- full history is being sent every round

---

## Current Limitations

Partner is still an early prototype.

Known limitations:

- long-term memory quality depends on cleanup and consolidation
- reflection can become generic if not audited
- evidence audit cannot replace human scientific judgment
- different agent backends behave very differently
- QQ proactive reports still require strict filtering
- content patrol is limited by public access, platform restrictions, and anti-bot rules
- multi-modal attachments are currently recorded as content signals unless a dedicated parser is available

---

## Design Principles

- Do not simulate personality with a giant prompt; build stable habits.
- Do not make users re-explain their projects; preserve long-term memory.
- Do not hard-code one user's correction; generalize it into project state and memory.
- Do not count file operations as progress; require evidence and judgment.
- Do not wait for users to assign every next step; generate internal follow-up instructions.
- Do not expose internal runtime errors as user messages.
- Do not try to finish everything in one run; make continuous small progress.

---

## One-Sentence Summary

Partner is an AI research companion that keeps working after the initial user instruction:
it remembers projects, records failures, absorbs user ideas, reflects periodically,
transfers methods across projects, creates its own next steps, and turns mature progress
into readable reports.
