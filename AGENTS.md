# Partner Repository Working Contract

This repository treats documentation, execution evidence, and state contracts as product code.
Every human or coding agent working here must follow this file.

## Required reading order

1. `docs/README.md`
2. `docs/current_status.md`
3. `docs/handoff/reading_order.md`
4. The architecture and project documents selected by `docs/catalog.yaml`
5. Relevant tests and implementation files

Historical sprint documents are evidence, not current truth. When documents conflict, prefer the
non-deprecated document with the highest authority and newest `updated_at` in `docs/catalog.yaml`.

## Non-negotiable completion rules

- The production default is `runtime.mode=manual_stable`: a user message is the sole task trigger.
  It must produce an immediate receipt, one bounded plan, visible start/finish for every real step,
  and a truthful final result. Campaign, Research Loop, automatic iteration, self-heal and autonomous
  cron are experimental and must remain off unless the user explicitly starts a separately accepted experiment.
- Never route a normal user message through a Campaign-specific fast path or replace its established
  message sequence with a report-only/fixed-template protocol. Shared infrastructure may be reused;
  the manual user experience is the compatibility boundary.
- A queue/log write is not message delivery. Delivery requires a runtime channel acknowledgment.
- File existence is not content quality. Validate format, substance, provenance, and user delivery.
- Writing a next step is not executing it. A next action must have an explicit state and execution receipt.
- Writing a reflection is not self-evolution. Evolution requires evidence, a testable hypothesis,
  an intervention, before/after verification, and a promote/reject/inconclusive decision.
- Project progress and Partner evolution are separate ledgers. Neither may count as the other.
- A vision-model description is probabilistic evidence. Browser success also requires deterministic
  DOM/control/runtime evidence.
- Work and communication are one loop. Campaign fast paths must preserve user-visible started/executed/finished
  progress receipts; a final PDF alone is not an acceptable user experience.
- Machine JSON and user reports are different products. Reports must use the project's domain structure and
  conclusions; do not wrap every result in one shared prose template or paste raw JSON as the report body.
- A previously verified good behavior must be preserved in canonical docs and regression tests before a new
  optimization, model, scheduler, or deterministic shortcut can replace its path.
- Do not claim an external side effect, login, foreground window, publication, or delivery without
  evidence from the system that performed it.

## Change protocol

- Preserve user changes and historical records.
- Prefer small modules and declarative protocols over new instance-specific branches in
  `partner/mind/executor.py`.
- A production behavior change requires focused tests, proportional regression tests, and a documentation update.
- Self-generated production changes begin as candidates. Promote only after validation; regressions must be rejected or rolled back.
- Never expose API keys, access tokens, private QQ identifiers, cookies, or credentials in logs, tests, or documents.
- Do not start more than two Partner instances. Do not start paused instances outside the slot scheduler.
- Do not start instances 03–05 unless the user or an approved scheduling decision places them in an available slot.
- Experimental long-running work, when explicitly re-enabled, must use a persisted Campaign and bounded
  WorkItems. It is not a production default. Process uptime, heartbeat, `Bot ready`, a written next step,
  or one passing task is not a continuous-run result.
- Campaign tasks have one continuation owner. When a Campaign marker is present, do not also start
  the process-local Research Loop.
- Respect Campaign deadline, work-item, retry, failure, model-call, cost and human-approval budgets.
- At campaign boundaries, create a final report and require real channel delivery; record delivery failure honestly.

## Required records

- Project rounds produce an `IterationReceipt` and explicit `NextAction` states.
- System defects produce an `IssueRecord`.
- Evolution attempts produce an `EvolutionExperiment` and `PromotionDecision`.
- Context selection records document IDs, reasons, versions, and token/character budget usage.
- Important architecture decisions are recorded under `docs/decisions/`.
- Explicit experimental long runs additionally produce CampaignState, WorkItem, InstanceLease, event history
  and CampaignReport records. Manual tasks do not need these Campaign records.

See `docs/handoff/change_protocol.md` and `docs/handoff/verification_rules.md` for the full workflow.
