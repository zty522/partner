## v0.7.0 - Event-Driven Partner Runtime

Partner v0.7.0 shifts the system from a project-loop autonomous runner toward an event-first AI companion.
The release focuses on selector-based routing, staged execution, explicit follow-up events, PDF delivery, growth-aware prompts, and clearer user-facing event updates.

### Core Changes

- Added an event-first interaction model: user messages are routed by an LLM selector that sees conversation context, pending follow-ups, active project status, mind pool state, and available events.
- Added action event types for direct tasks, literature review, data analysis, evidence audit, artifact building, PDF reporting, project thinking, curiosity exploration, habit update, and project stop.
- Added `pdf_report` as a first-class event. PDF generation is no longer hidden inside artifact building.
- Added `stop_project` as an explicit event. Long-running project execution should stop only when the selector chooses to stop, wait, or pause.
- Added event-boundary user updates. After each event, Partner can tell the user which event ran, what was done, what files were created, and what event is planned next.
- Added stronger conversation continuity for ordinary chat and pending missing-parameter questions.
- Added growth-aware action prompts: recent growth events are injected alongside shared habits so learned behavior can affect future execution.
- Reduced hard-coded conversation behavior. Duplicate suppression, missing-parameter handling, and continuation routing are now selector decisions instead of fixed text rules.
- Improved QQ inbound behavior: Partner preserves user text, avoids default parameter supplementation, and asks when required information is missing.

### Event Runtime

The mind pool now treats these events as central runtime units:

- `direct_reply`
- `direct_task`
- `literature_review`
- `data_analysis`
- `evidence_audit`
- `artifact_build`
- `pdf_report`
- `project_think`
- `curiosity_explore`
- `habit_update`
- `stop_project`
- `project`
- `report`
- `reflection`
- `cross_project`
- `memory_consolidate`
- `content_digest`
- `wake_up`
- `cron_tick`

The project is context. The event is execution.

### User Experience Changes

Partner should now behave more like a staged collaborator:

```text
user request
  -> selector chooses event
  -> event executes one verifiable action
  -> Partner reports the event result
  -> follow-up selector chooses next event or stop_project
```

This replaces vague proactive status reporting with event-boundary reporting.

### PDF Reports

PDF delivery is now modeled as a dedicated `pdf_report` event.

Use cases:

- turn existing research results into a final PDF report
- convert a completed brief into a user-facing PDF
- package a project stage into a stable report artifact

The event is expected to create a real `.pdf` file and expose the path through `FILES`.

### Growth and Habits

Growth events are now more operational.

Partner still stores growth in long-term memory, but recent growth events are also injected into action-event prompts. This lets user corrections and system learning influence future execution without turning every correction into a brittle hard-coded rule.

Example:

```text
User correction: "Do not default missing location to Guangzhou."
Growth: "When required parameters are missing, ask the user instead of inventing defaults."
Future behavior: selector asks for the location before weather/Excel execution.
```

### Installation

Linux / WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/zty522/partner/main/scripts/install.sh | bash
partner setup
```

Windows PowerShell:

```powershell
powershell -Command "& { iwr -useb https://raw.githubusercontent.com/zty522/partner/main/scripts/install.ps1 } | iex"
partner setup
```

From source:

```bash
git clone https://github.com/zty522/partner.git
cd partner
pip install -e .
partner setup
```

### Server and Ollama Setup

`partner setup` now includes optional server and Ollama pool configuration.
Users can register multiple remote servers and multiple Ollama endpoints, then let Partner choose whether Ollama should be used for lightweight routing or project execution.

Server commands:

```bash
partner server add --name server-prod-01 --host 203.0.113.10 --user ubuntu --key ~/.ssh/partner_server.pem --remote-workspace /home/ubuntu/partner_workspace/instances/01 --print-tunnel
partner server list
partner server tunnel-hint server-prod-01
partner server remove server-prod-01
```

Ollama commands:

```bash
partner ollama setup
partner ollama add --name local --base-url http://127.0.0.1:11434 --models qwen2.5:7b --mode lite --location local
partner ollama add --name local-pc --base-url http://127.0.0.1:11434 --models qwen2.5:7b --mode lite --location tunnel --server server-prod-01
partner ollama list
partner ollama test
partner ollama mode lite
partner ollama disable
```

To expose a local computer's Ollama to a server instance, run this on the local computer:

```bash
ssh -N -R 11434:127.0.0.1:11434 -i ~/.ssh/partner_server.pem -p 22 ubuntu@203.0.113.10
```

Then configure the server-side Partner instance to use `http://127.0.0.1:11434`.

### Upgrade Notes

Existing users should run:

```bash
partner update
partner setup
```

Then restart running instances.

If you use QQ bot instances, verify that:

- inbound messages are not supplemented with default parameters
- event labels appear in user-facing replies
- generated PDF reports are pushed as files when the platform allows it
- long-running projects stop through `stop_project` or enter a waiting state intentionally

### Known Limitations

- Event selection quality depends on the configured LLM backend.
- Growth events influence prompts but are not a formal policy engine.
- File pushing still depends on QQ official bot platform limits.
- Content digestion remains limited by public access and parser availability.
