# ARCHITECTURE.md — Partner Architecture Evolution Log

## Section 1: Architecture Overview (Current State)

Partner is an autonomous AI research companion that works independently in the background. The user never gives it commands — they just check in and ask "What have you been doing?" The architecture is designed around proactive, continuous research driven by a cron-based heartbeat.

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        🤝 PARTNER SYSTEM                          │
│                                                                  │
│  ┌──────────── CRON HEARTBEAT LOOP ──────────────────────────┐   │
│  │  Every 30min: Research Cycle (Event → Task → Self-Evolve) │   │
│  │  Every 2h:     Full Self-Evolution cycle                  │   │
│  │  Every cycle:  QQ heartbeat report push                   │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌───────────────────┐ ┌──────────────────┐ ┌────────────────┐  │
│  │   TASK QUEUE       │ │  EVENT ENGINE    │ │  KNOWLEDGE     │  │
│  │   (priority-based) │ │  (multi-phase)   │ │  BASE          │  │
│  │   task_queue.json  │ │  active_plan.json│ │  knowledge.json│  │
│  └─────────┬─────────┘ └────────┬─────────┘ └───────┬────────┘  │
│            │                    │                    │           │
│  ┌─────────▼────────────────────▼────────────────────▼────────┐  │
│  │                    EXECUTION ENGINE                         │  │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐  │  │
│  │  │  Hermes   │ │  Codex    │ │ OpenClaw  │ │  Direct   │  │  │
│  │  │  Adapter  │ │  Adapter  │ │ Adapter   │ │  Adapter  │  │  │
│  │  └───────────┘ └───────────┘ └───────────┘ └───────────┘  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              SELF-EVOLUTION ENGINE (3-layer)                 │  │
│  │  ┌─────────────────┐ ┌──────────────┐ ┌──────────────┐    │  │
│  │  │ StrategyLearner  │ │ MemoryPruner │ │  CPEGuard    │    │  │
│  │  │ (task profiling) │ │ (KB pruning) │ │ (degradation │    │  │
│  │  └─────────────────┘ └──────────────┘ │  prevention) │    │  │
│  │                                       └──────────────┘    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────┐ ┌──────────────┐ ┌────────────────────────┐    │
│  │ QQ Official│ │  NapCat      │ │  Conversation Engine   │    │
│  │ Bot Bridge │ │  Bridge      │ │  (intent routing V2)   │    │
│  └────────────┘ └──────────────┘ └────────────────────────┘    │
│                                                                  │
│  ┌────────────┐ ┌──────────────┐                                │
│  │  CLI       │ │  Windows GUI │  (tkinter, GitHub Dark theme)  │
│  │  (partner) │ │  Partner.vbs │                                │
│  └────────────┘ └──────────────┘                                │
└──────────────────────────────────────────────────────────────────┘
```

### Three-Layer Architecture

**Layer 1 — Task Queue & Event Engine:** The system prioritizes execution as: Events (multi-phase research cycles) → Tasks (atomic operations) → Auto-generate new Events when idle. Events support 5 phase types: literature_search, code_implementation, experiment, analysis, and planning. The `active_plan.json` state file tracks multi-phase plans with progress, current phase, and heartbeat summary across unlimited 30-min cycles.

**Layer 2 — Execution Engine:** An abstract `AgentAdapter` interface unifies all agent backends. Currently supports Hermes Agent (primary, full skill system + cron + LLM chat), OpenAI Codex (code delegation via `codex exec --full-auto`), OpenClaw (via ACP protocol bridge), Claude Code, and a Direct mode. The adapter layer allows Partner to switch backends without changing any orchestration logic.

**Layer 3 — Knowledge Base & Journal:** Structured knowledge entries with confidence scoring, category tagging, and provenance tracking. The journal (`journal.jsonl`) records every research cycle, task, and event for audit and self-evolution analysis.

### QQ Integration (Dual-Bridge)

Partner connects to QQ through two parallel bridges:
- **Official Bot Bridge:** Connects to the QQ Open Platform API (`api.sgroup.qq.com`) via WebSocket + REST, supporting private (C2C) and group @mentions. Auto-start/stop via `partner bot start qq`, with a watchdog process that auto-restarts on crash.
- **NapCat Bridge:** Alternative bridge using the NapCat framework for additional QQ protocol support, added in v0.3.1 for redundancy and broader compatibility.

Every heartbeat cycle pushes a QQ status report with current phase, progress, and next step — no 60-minute threshold, every cycle pushes.

### Self-Evolution Engine (3-Layer)

Introduced in v0.1.0 as a prototype and matured through v0.4.0:

1. **StrategyLearner:** Analyzes the journal (last 50 entries) to build task-type profiles with success rates, value scores, and recommended priority boosts. High-success, high-value task types get priority +2; low-success types get -3.
2. **MemoryPruner:** Scans the knowledge base for low-confidence old entries (archive after 30 days), duplicate titles (merge), high-frequency references (promote to high confidence), and orphaned entries (demote).
3. **CPEGuard (Capability Preservation through Evaluation):** Registers core capabilities with baseline success rates. Each evolution cycle re-evaluates current rates against baselines; degradation beyond a 15% threshold triggers protective escalation (e.g., doubling verification frequency).

### Self-Check Mechanism

After each plan execution, Partner auto-analyzes results and proposes next steps. The cron-driven planner checks: is a plan active? If yes, let it continue. If idle, create a new complete plan. This prevents both overwriting in-progress work and leaving the system idle.

### User Interfaces

- **CLI:** `partner setup | status | bot start/stop qq | queue clear | config | update`
- **Windows GUI:** tkinter desktop app with GitHub Dark theme (v0.3.1 rewrite)
- **Conversation Engine V2:** Intent-based routing (GREETING, STATUS, KNOWLEDGE, DIRECTION, DETAIL, TASK_ADD/CANCEL, WORKSPACE, HELP, GENERAL) with regex classification and LLM-powered fallback

---

## Section 2: Evolution Log

| Version | Date | Key Changes | Architectural Rationale |
|---------|------|-------------|------------------------|
| v0.1.0 | 2026-05-23 | Core foundation: Hermes cron-driven task queue, knowledge base, journal, event engine with 5-phase research cycles. Skill health monitoring with 4D scoring. Basic CLI setup wizard. | The initial architecture prioritized autonomous operation. The cron-driven loop was chosen over an always-on daemon because it's simpler to manage, survives crashes naturally (next cron tick recovers), and integrates cleanly with Hermes Agent's existing scheduling. The 3-layer task→knowledge→adapter separation was designed from day one to allow independent evolution of each layer. |
| v0.2.0 | 2026-05-24 | Major rearchitecture from isolated tasks to continuous multi-phase plans. QQ Official Bot integration with watchdog. Conversation Engine V2 with intent routing. Heartbeat Plan Model: 30-min minimum cycles, `active_plan.json` tracking. One-click `partner setup` wizard. | The shift from Events (fixed TTL, run-once) to Plans (continuous, unlimited cycles) was driven by a fundamental insight: research is not a batch job. It needs sustained attention across cycles. The heartbeat model decoupled maintenance (QQ comms, health checks) from research execution — the heartbeat only checks if a plan is active, it never interrupts running work. The QQ integration was the first "proactive notification" channel: instead of waiting for the user to ask, Partner now pushes findings. |
| v0.3.0 | 2026-05-25 | Windows desktop app (tkinter GUI), Partner CLI (`python -m partner`), OpenClaw/Codex agent integrations, dead-letter recovery, install scripts (install.bat, install.sh, install.ps1), Inno Setup Windows installer, GitHub Actions auto-build. | Agent backend diversity became an architectural requirement. The `AgentAdapter` abstract interface was the key decision: it allowed OpenClaw (ACP protocol), Codex (exec --full-auto), Claude Code, and Direct mode to coexist without touching the orchestration layer. The Windows GUI was an experiment in making Partner visible to non-CLI users — it reads from the same state files, so it's a viewport onto the existing architecture, not a separate system. The installer suite automated what was previously a manual setup process. |
| v0.3.1 | 2026-05-27 | Windows GUI rewrite (GitHub Dark theme), NapCat bridge support, GREETING intent added to conversation router, instant QQ reply optimization, bug fixes (duplicate replies, markdown stripping, heartbeat suppression after enqueue, timeout increases from 60s→300s). | The NapCat bridge was added because the official QQ bot API has rate limits and reliability issues — NapCat provides a fallback protocol path. The GREETING intent addressed a UX gap: users naturally say "hi" before asking questions, and the original router treated greetings as GENERAL, producing robotic responses. The instant reply optimization eliminated the "please wait..." double-reply by ensuring each message produces exactly one response. Timeout increases from 60s to 300s for Hermes chat reflected the reality that research-grade LLM responses take longer. |
| v0.4.0 | 2026-05-27 | Instant QQ reply (no queue delay), self-evolution engine matured to full 3-layer (StrategyLearner + MemoryPruner + CPEGuard), research self-check after each plan, cron planner upgrade (dual-plan fix preventing overwrite, heartbeat fix for reliable cycle detection). | Two architectural patterns solidified in v0.4.0. First, the self-evolution engine graduated from prototype to production: the 3-layer design (learn → prune → protect) mirrors how a human researcher improves — learn from past work, forget what's stale, protect against skill regression. Second, the dual-plan fix addressed a subtle race condition where cron ticks could create overlapping plans; the fix was to add an active-plan detection gate before any new plan creation. The heartbeat fix ensures the system reliably distinguishes between "actively working" and "stuck" states by checking progress deltas rather than absolute timestamps. |

---

### Architectural Principles (Emerged over time)

1. **Decoupled cron heartbeat:** Maintenance and research are separate concerns. The heartbeat maintains connectivity and health; research runs independently.
2. **Non-destructive self-evolution:** The evolution engine recommends but never overwrites user data. Archived knowledge entries are marked, not deleted. Strategy profiles suggest priority boosts but the task queue always respects user-assigned priorities.
3. **Proactive by default:** Every idle cycle is a missed discovery. The system auto-generates events when nothing is queued, pushes QQ notifications every cycle, and surfaces findings without being asked.
4. **Pluggable agent backends:** The `AgentAdapter` interface means Partner is not tied to any single LLM or agent framework. This was critical for supporting Hermes, Codex, OpenClaw, Claude Code, and future backends under one orchestration layer.
5. **State-file-based persistence:** No database, no daemon — just JSON state files. This makes the architecture trivially inspectable, debuggable, and recoverable. A crash loses at most one cron cycle's work.
