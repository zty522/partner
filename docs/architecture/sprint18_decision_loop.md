# Sprint18 Self-Evolution Decision Loop — State Machine & Event Contract

This document specifies the single source of truth for "should this task
trigger self-evolution, active learning, external retrieval, or just continue?"
It is intentionally written as a state machine + LLM-facing event vocabulary
so a downstream language model can read the ledger and answer "what is partner
doing right now, why, and what's next?" without guessing.

This is **not** a new ADR or sprint doc — it extends ADR 0062 §3.
It supersedes any earlier prose about "what counts as a self-evolution trigger".

---

## 1. Why this exists

The previous system had two contracts that almost-overlap:

- *active learning* meant "I observed something useful, propose a candidate"
- *self-evolution* meant "I have a candidate, validate and maybe apply it"

Nothing wrote a clean, replayable event stream explaining *why* one path was
chosen over the other. The same task sometimes produced zero events (silent
no-op), sometimes produced six events with no clear decision, and several
"successful" runs were indistinguishable from "stuck" runs in the ledger.

This contract fixes that by making the decision itself an event the LLM can
read and write. Every state transition emits exactly one event with a
structured payload. The state machine has no hidden branches; every branch
the machine can take is enumerated below.

---

## 2. The state machine

There is **one** state machine. Every task, every slot, every instance
slot entry, and every curiosity proposal moves through it. Each node emits
the matching event when entering.

```
                ┌─────────────────────────────────────┐
                │                                     │
                ▼                                     │
   [seed] → IDLE  ───────────────────► OBSERVING      │
                       (no work)     (a task is in    │
                                     scope; or slot  │
                                     triggered OBS)   │
                                                │     │
                                                ▼     │
                                          DIAGNOSING  │
                                     (LLM or rule:   │
                                      what's wrong?) │
                                                │     │
                                                ▼     │
                                          DECIDING ◄──┘
                                     (LLM emits a    │
                                      DecisionEvent  │
                                      with explicit  │
                                      next_action)   │
                ┌──────────────┬─────────────┴─────────────┐
                ▼              ▼                           ▼
        SELF_EVOLVING    ACTIVE_LEARNING              EXTERNAL_RETRIEVAL
        (apply pipeline) (curiosity/internal          (network/search/
                         proposal run)                harvest)
                │              │                           │
                ▼              ▼                           ▼
            APPLIED      CANDIDATE_PROPOSED           EVIDENCE_COLLECTED
                │              │                           │
                └───► COMPLETE ◄──────────────────────────┘
                            │
                            ▼
                          IDLE
```

### 2.1 Nodes

| Node                  | Owned by      | What it produces                                    |
| --------------------- | ------------- | --------------------------------------------------- |
| IDLE                  | slot scheduler | nothing (waiting for slot or task)                  |
| OBSERVING             | task harness  | event `observe/context_recorded`                    |
| DIAGNOSING            | LLM or rule   | event `diagnose/classification_recorded`            |
| DECIDING              | LLM           | event `decide/decision_recorded` (with next_action) |
| SELF_EVOLVING         | apply_pipeline| event `evolve/state_recorded` (per sub-step)        |
| ACTIVE_LEARNING       | curiosity_bridge| event `learn/state_recorded`                      |
| EXTERNAL_RETRIEVAL    | external_retrieval | event `retrieve/state_recorded`              |
| APPLIED               | apply_pipeline| event `apply/outcome_recorded`                      |
| CANDIDATE_PROPOSED    | curiosity_bridge| event `candidate/proposal_recorded`                |
| EVIDENCE_COLLECTED    | external_retrieval| event `evidence/intake_recorded`                |
| COMPLETE              | coordinator   | event `loop/completion_recorded`                    |

### 2.2 Edges

The machine moves one edge at a time. Each edge is triggered by:

- **edge_seed_to_idle** — slot entry or new task delivered
- **edge_idle_to_observing** — there is a fresh task or slot budget allows
- **edge_observing_to_diagnosing** — observe phase produced signals worth diagnosing
- **edge_diagnosing_to_deciding** — diagnostic ran (LLM or rule)
- **edge_deciding_to_<branch>** — the LLM emitted a `DecisionEvent` whose
  `next_action ∈ {"self_evolve", "active_learning", "external_retrieval", "noop"}`
- **edge_<branch>_to_complete** — branch wrote its terminal state event
- **edge_complete_to_idle** — return to slot idle

### 2.3 Termination guarantee

Every entry into DECIDING must produce either a transition event or a
`decision/dead_letter_recorded` event. The loop can never silently abort.

---

## 3. DecisionEvent — what the LLM writes when it reaches DECIDING

The decision event is the single most important contract in this loop.
A model writing this event is committing to a single next_action; the
runtime executes **only** the chosen branch.

### 3.1 Schema

```yaml
event_type: "decide/decision_recorded"
occurred_at: <ISO8601>                       # produced automatically
actor: <instance_id>                         # produced automatically
subject_id: <task_id or slot_id>             # produced automatically
project_id: <current project>                # produced automatically
schema_version: 2
payload:
  observation_summary: "<=800 chars"         # what was observed
  classification:
    failure_class: "<enum>"                  # see §3.2
    confidence: <float 0..1>
  decision:
    next_action: "<enum>"                   # see §3.3
    rationale: "<=600 chars"                 # why this branch and not others
    estimated_cost_seconds: <int>
    requires_external: <bool>
    requires_apply: <bool>
  preconditions:
    have_diff_hunk: <bool>                   # true iff candidate has a diff
    have_target_file: <bool>
    have_external_query: <bool>              # true iff observe captured a query
    signals_count: <int>
```

### 3.2 failure_class enum

Each value corresponds to a routing rule:

```
bug_in_partner_source          → self_evolve   (must have target_file + diff_hunk)
missing_capability             → external_retrieval  (must have external_query)
ambiguous_decision             → active_learning     (queries local evidence)
regression_in_recent_apply     → self_evolve (rollback path)
config_drift                   → noop               (log and reobserve)
needs_more_data                → external_retrieval
knowledge_gap                  → active_learning
unknown                        → noop  (ask user / escalate)
```

### 3.3 next_action enum

Only four values are valid. Anything else triggers
`decision/dead_letter_recorded` and falls back to `noop`.

- `self_evolve` — must invoke `apply_one(candidate)` after producing diff_hunk
- `active_learning` — must invoke `curiosity_bridge.propose()`
- `external_retrieval` — must invoke `external_retrieval.harvest_for_topic()`
- `noop` — explicitly do nothing; emits `loop/completion_recorded` immediately

### 3.4 Preconditions

The runtime refuses to enter a branch unless its preconditions match.
For example, `next_action=self_evolve` requires both `have_diff_hunk` and
`have_target_file` to be true. If they are false and the LLM still wrote
`self_evolve`, the event is reframed as `noop` and a `decision/refused_recorded`
event is appended.

---

## 4. Branch execution events (what each branch writes)

Every branch writes **per-substep** events. Not just one event at the end —
each substep writes its own. This is what makes the loop "visible" to the LLM.

### 4.1 SELF_EVOLVING

```
evolve/diff_validated
evolve/git_apply_check_recorded
evolve/git_apply_recorded
evolve/git_add_recorded
evolve/git_commit_recorded       (or evolve/git_commit_failed_recorded)
apply/outcome_recorded            (terminal)
```

### 4.2 ACTIVE_LEARNING

```
learn/signals_extracted_recorded
learn/topic_proposed_recorded
learn/note_written_recorded
learn/evolution_event_appended_recorded
candidate/proposal_recorded        (terminal)
```

### 4.3 EXTERNAL_RETRIEVAL

```
retrieve/query_normalized_recorded
retrieve/cache_hit_recorded       (or retrieve/cache_miss_recorded)
retrieve/fetch_started_recorded
retrieve/fetch_completed_recorded
retrieve/fact_card_written_recorded
evidence/intake_recorded           (terminal)
```

### 4.4 NOOP

```
noop/reason_recorded
loop/completion_recorded
```

---

## 5. Why this fixes "empty run" and "stuck run"

A reviewer (human or LLM) answers the following by reading the ledger:

- "What did partner do?" → grep `event_type` for the slot id subject
- "Why did it choose branch X?" → read the most recent
  `decide/decision_recorded` event's `payload.decision.rationale`
- "Did the branch actually run?" → count `*/state_recorded` events; if zero,
  the run is empty (and the dead_letter guarantees no silent abort)
- "Did the run finish?" → look for `loop/completion_recorded`. If absent
  past the budget, the run is stuck; watchdog emits `loop/stuck_recorded`

Empty and stuck are now detectable from the ledger alone. The state machine
makes it impossible to write either condition while also claiming success.

---

## 6. Coordinator — the one place the loop is driven

A single Python function `run_decision_loop(...)` is the entrypoint. It
drives the entire machine by reading and writing these events. It is the
only thing that may call into the branches. Branches never call each other
directly.

```
run_decision_loop(
    workspace, instance_id, budget_seconds,
    task_state=None,
    candidate=None,           # optional pre-formed candidate (for SELF_EVOLVING)
    external_query=None,       # optional pre-formed query (for EXTERNAL_RETRIEVAL)
) -> {
    next_node: "<terminal node>",
    decisions: int,            # how many DECIDING visits occurred
    applied: int,              # how many apply outcomes succeeded
    completed: bool,           # True iff COMPLETE was reached
    stuck_watchdog_triggered: bool,
}
```

Determinism rules:

1. The loop is single-threaded per slot; concurrent branches are not allowed.
2. Budget is checked *after* every event write; exceeding budget falls to
   `loop/completion_recorded` with `budget_exceeded=true`.
3. If a branch panics, the panic is caught and translated to
   `loop/branch_panic_recorded`; the loop returns to IDLE without aborting
   the calling slot entry.

---

## 7. Mapping to existing modules

| Concern                  | Module                                     |
| ------------------------ | ------------------------------------------ |
| State machine driver     | `partner/evolution/decision_loop.py` (new) |
| Branch: SELF_EVOLVING    | `partner/evolution/apply_pipeline.py`      |
| Branch: ACTIVE_LEARNING  | `partner/evolution/curiosity_bridge.py`    |
| Branch: EXTERNAL_RETRIEVAL| `partner/governance/external_retrieval.py` |
| Ledger writing           | `partner/evolution/ledger.py` (new helper) |
| Slot entry call          | `partner/governance/instance_native.py::recover_or_start` |
| Watchdog (stuck/empty)   | `partner/evolution/loop_watchdog.py` (new) |

Slots use `run_decision_loop`. Branches continue to be callable directly
for unit tests; the loop is just glue.

---

## 8. Event vocabulary cheat-sheet

```
observe/context_recorded
diagnose/classification_recorded
decide/decision_recorded        # THE key event
decide/refused_recorded         # precondition mismatch
decide/dead_letter_recorded     # unrecognised next_action
evolve/diff_validated
evolve/git_apply_check_recorded
evolve/git_apply_recorded
evolve/git_add_recorded
evolve/git_commit_recorded
evolve/git_commit_failed_recorded
apply/outcome_recorded
learn/signals_extracted_recorded
learn/topic_proposed_recorded
learn/note_written_recorded
learn/evolution_event_appended_recorded
candidate/proposal_recorded
retrieve/query_normalized_recorded
retrieve/cache_hit_recorded
retrieve/cache_miss_recorded
retrieve/fetch_started_recorded
retrieve/fetch_completed_recorded
retrieve/fact_card_written_recorded
evidence/intake_recorded
noop/reason_recorded
loop/completion_recorded
loop/stuck_recorded
loop/branch_panic_recorded
```

Every one of the above is appended exactly once per crossing.
Reading any slot's events in order reconstructs the path it took through
the state machine without reading any other code.

---

## 9. Acceptance

For each slot entry, the ledger for that subject_id must contain:

1. exactly one `observe/*` entering OBSERVING
2. exactly one `diagnose/*` entering DIAGNOSING
3. exactly one `decide/*` entering DECIDING (with next_action ∈ valid enum)
4. ≥1 per-substep events from the chosen branch
5. exactly one `*/..._recorded` terminal event for the chosen branch
6. exactly one `loop/completion_recorded` exiting COMPLETE

Stuck runs are detectable because (6) is missing. Empty runs are detectable
because (4) has fewer substep events than the branch's documented minimum
(self_evolve: ≥3, active_learning: ≥3, external_retrieval: ≥3).
