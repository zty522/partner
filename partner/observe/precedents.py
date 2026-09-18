"""Precedent library for self-evolution aspect_synthesize.

Each entry encodes ONE real bug we (hermes + codex) discovered in
Partner runtime, what the symptom looks like in event logs / flow
outputs, which file the root cause lives in, and what fix was applied.

aspect_synthesize reads this library as "precedent retrieval": when
INSTANCE history shows symptoms matching a precedent, the synthesize
step MUST emit a candidate whose target_files / change follow that
precedent fix shape (with file-specific adaptation). This converts
"self-evolution guessing what to fix" into "self-evolution matching
known patterns" - same approach we used when we hand-fixed Partner
the first time.
"""
from __future__ import annotations
from typing import Any, TypedDict


class Precedent(TypedDict):
    case_id: str
    symptom_query: str
    symptom_signals: list
    root_cause_files: list
    root_cause_summary: str
    fix_shape: str
    fix_examples: list
    candidate_template: dict


PRECEDENTS = [
    {
        "case_id": "case_01_intake_regex_blocking_dispatch",
        "symptom_query": "service.submit returns accepted=True status=dispatched but actual flow never starts; sync reply is generic template",
        "symptom_signals": [
            "service.submit accepted=True status=dispatched",
            "actual flow (browser_video_learning / xhs_authoring) status stays queued",
            "sync reply contains generic 'already enqueued' template phrase",
            "intent dispatcher matches regex keyword (e.g. 'video' 'look at' 'xiaohongshu') but routes to wrong project",
        ],
        "root_cause_files": [
            "partner/social_video/integration.py",
            "partner/application/service.py",
        ],
        "root_cause_summary": (
            "service.submit internal _route() / _project() use regex keyword dispatch; "
            "social_video.intake() uses regex to grab URL and write intake/*.json; "
            "submit direct_answer branch only returns Submission.message, calls no QQ delivery. "
            "Three problems stack: regex misroutes + missing delivery + template reply."
        ),
        "fix_shape": (
            "delete _route / _project regex dicts; delete social_video.intake() whole module; "
            "replace with INTENT_FLOW three-pass (observe / counter_read / synthesize); "
            "synthesize outputs {route, dispatch_target, warm_reply, payload, reason}; "
            "service.submit direct_answer branch tail calls _enqueue_outbound_text() to write outbound/, "
            "reusing instance qq_bridge notification_poller delivery chain."
        ),
        "fix_examples": [],
        "candidate_template": {
            "target_files_hint": [
                "partner/application/service.py",
                "partner/social_video/integration.py",
            ],
            "change_kind": "delete_regex_dispatch_and_add_intent_flow + outbound_enqueue",
            "causal_hypothesis": (
                "INSTANCE sync reply uses template / dispatch_target hits but flow never starts = "
                "service.submit internal regex / social_video intake is doing dispatch, "
                "needs LLM-driven INTENT_FLOW + outbound enqueue."
            ),
            "expected_improvement": (
                "next direct_answer / project_iteration sync reply should be LLM-generated real content, "
                "and video / xhs_authoring tasks will actually spawn child flow."
            ),
        },
    },
    {
        "case_id": "case_02_message_critic_reject_realtime",
        "symptom_query": "message_compose writes reasonable text but message_critic rejects 3x in a row",
        "symptom_signals": [
            "job error = message did not pass independent review within budget",
            "node_outputs.message_critic.problems contains unsupported_claims or template tag or too many numbers",
            "last 3-5 message_compose outputs contain >= 3 numeric literals / '**Heading:**' template / '[V8_xxx]' reference",
            "user sees generic 'direct answer' while INSTANCE has real business results",
        ],
        "root_cause_files": [
            "partner/events/presentation.py",
        ],
        "root_cause_summary": (
            "message_critic has three hard rules: (1) LLM fact_audit unsupported_claims; "
            "(2) regex len(numeric_literals)>2; "
            "(3) regex template tag (段落1 / 段落2 / '**Heading:**'). "
            "LLM does not know these rules and keeps tripping the same rejection."
        ),
        "fix_shape": (
            "top-insert message_compose prompt with 'max 2 numbers, no **Heading:** template, no _xxx reference'; "
            "or pass hard rules to upstream compose node; "
            "or have message_compose read upstream message_critic problems feedback directly (already does 3-revise loop)."
        ),
        "fix_examples": [],
        "candidate_template": {
            "target_files_hint": ["partner/events/presentation.py"],
            "change_kind": "loosen_or_signal_message_critic",
            "causal_hypothesis": (
                "INSTANCE message_critic rejection rate is high = LLM does not know number / template limits."
            ),
            "expected_improvement": (
                "next message_critic accepted=True ratio should rise; "
                "3 consecutive rejections should trigger fallback (no more retry x 3)."
            ),
        },
    },
    {
        "case_id": "case_03_pdf_report_never_triggered",
        "symptom_query": "PROJECT_ITERATION runs multiple rounds, user never receives PDF_REPORT instance push",
        "symptom_signals": [
            "assess_next routes primary_route=continue_project forever (no route= report)",
            "PDF_REPORT flow instance count = 0",
            "report_decide node force=undefined / notification_kind != milestone|final",
            "user asks 'write progress as PDF' but assessor picks continue_project instead of report",
        ],
        "root_cause_files": [
            "partner/events/delivery.py",
            "partner/events/presentation.py",
            "partner/event_flows/builtins.py",
        ],
        "root_cause_summary": (
            "PROJECT_ITERATION tail report_decide condition is too strict (only accepts force / milestone / final); "
            "assess_next 6-way route prompt gives no clear boundary between report vs continue, "
            "LLM always picks continue_project when 'can run another round'."
        ),
        "fix_shape": (
            "assess_next prompt add: 'when this round produced >= 1 verified artifact AND original goal >= 70% done, "
            "prefer route=report'. report_decide add fallback: notification_kind=routine but business_delta=true "
            "still produces PDF."
        ),
        "fix_examples": [],
        "candidate_template": {
            "target_files_hint": [
                "partner/events/delivery.py",
                "partner/events/presentation.py",
            ],
            "change_kind": "loosen_report_trigger",
            "causal_hypothesis": (
                "INSTANCE PROJECT_ITERATION ran >= 3 rounds but no PDF = "
                "report_decide / assess_next jointly refuse to emit."
            ),
            "expected_improvement": (
                "next PROJECT_ITERATION end should trigger at least one PDF_REPORT flow."
            ),
        },
    },
    {
        "case_id": "case_04_pre_iteration_reflect_noop_loop",
        "symptom_query": "INSTANCE history shows pre_iteration_reflect always decision=no_op, candidates=0",
        "symptom_signals": [
            "aspect/synthesize_decided >= 5 consecutive decision=no_op, candidates_count=0",
            "INSTANCE PROJECT_ITERATION ran >= 3 rounds but flow_outputs round_summaries < 5",
            "no_op_streak keeps increasing",
        ],
        "root_cause_files": [
            "partner/events/cross_cutting.py",
        ],
        "root_cause_summary": (
            "_load_history reads limited INSTANCE history; "
            "first few rounds INSTANCE has no history -> synthesize sees no evidence -> LLM conservatively picks no_op -> dead loop."
        ),
        "fix_shape": (
            "aspect_synthesize prompt when no_op_streak >= 3: inject 'must decision=candidate AND target_files "
            "in one of partner/events/cross_cutting.py / presentation.py / delivery.py, give a preloaded candidate change template'. "
            "Or: synthesize when INSTANCE history < 5 force-match one PRECEDENT case."
        ),
        "fix_examples": [],
        "candidate_template": {
            "target_files_hint": ["partner/events/cross_cutting.py"],
            "change_kind": "force_candidate_after_noop_streak",
            "causal_hypothesis": (
                "synthesize always no_op = LLM has no forced candidate prompt guidance."
            ),
            "expected_improvement": (
                "no_op_streak >= 3 then aspect_synthesize MUST emit candidate; "
                "next INSTANCE PROJECT_ITERATION at least one aspect/* decision=candidate."
            ),
        },
    },
    {
        "case_id": "case_05_event_series_unknown",
        "symptom_query": "newly added event_definition series is rejected by ledger.create as unknown Event series",
        "symptom_signals": [
            "job error = checkpoint_crashed: ValueError: unknown Event series: <name>",
            "EVENT_SERIES set does not contain new series name",
            "catalog.build_catalog() contains new event_definition but flow startup crashes",
        ],
        "root_cause_files": [
            "partner/event_fabric/models.py",
        ],
        "root_cause_summary": (
            "EventLedger.create() enforces series in EVENT_SERIES; "
            "when adding new event_definition, forgot to register series in EVENT_SERIES frozen set."
        ),
        "fix_shape": (
            "add new series to EVENT_SERIES; "
            "or add EventDefinition helper: register(definition) auto-inserts definition.series into EVENT_SERIES."
        ),
        "fix_examples": [],
        "candidate_template": {
            "target_files_hint": ["partner/event_fabric/models.py"],
            "change_kind": "extend_EVENT_SERIES",
            "causal_hypothesis": (
                "new event_definition series not registered in EVENT_SERIES -> "
                "EventLedger.create throws ValueError."
            ),
            "expected_improvement": (
                "after new event_definition, next build_catalog() + flow startup no longer throws unknown series."
            ),
        },
    },
]


def load_precedents(workspace):
    """Return the precedent library. workspace is kept as arg for future
    workspace-scoped precedents (per-project bug patterns)."""
    return list(PRECEDENTS)


def precedents_as_prompt_text(workspace, max_chars=8000):
    """Render precedents as a structured prompt block for aspect_synthesize."""
    nl = chr(10)
    blocks = []
    for p in load_precedents(workspace):
        b = (
            "### " + p["case_id"] + nl
            + "  query: " + p["symptom_query"] + nl
            + "  signals: " + ", ".join(p["symptom_signals"]) + nl
            + "  root_cause_files: " + ", ".join(p["root_cause_files"]) + nl
            + "  mechanism: " + p["root_cause_summary"] + nl
            + "  fix_shape: " + p["fix_shape"] + nl
            + "  candidate_template.causal_hypothesis: "
            + p["candidate_template"].get("causal_hypothesis", "") + nl
            + "  candidate_template.expected_improvement: "
            + p["candidate_template"].get("expected_improvement", "") + nl
        )
        blocks.append(b)
    text = nl.join(blocks)
    if len(text) > max_chars:
        text = text[:max_chars] + nl + "[truncated]"
    return text


__all__ = ["Precedent", "PRECEDENTS", "load_precedents", "precedents_as_prompt_text"]
