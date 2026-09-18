"""Pre-iteration reflection event.

Runs at the START of every PROJECT_ITERATION round (before hypothesis
propose). Reads INSTANCE history of:
  - recent PROJECT_ITERATION rounds
  - prior self-evolution modifications
  - message_critic rejections / claim_verify failures

Produces an `improvement_focus` string that gets injected into the
hypothesis_propose prompt so the LLM enters the round with explicit
guidance about what went wrong last time.

Mirrors the INTENT_FLOW structure: observe -> counter_read -> synthesize,
plus an emit step. Aspect = "pre_iteration".
"""
from __future__ import annotations

import json
import time
from typing import Any

from partner.event_fabric.catalog import EventDefinition
from .cross_cutting import (
    aspect_observe, aspect_counter_read, aspect_synthesize, aspect_emit,
    _workspace,
)
from ._llm import call_model, json_object


def _improvement_focus_from_synth(synth_sem: dict[str, Any]) -> str:
    """Extract a short, plain-language hint from synthesize's candidates.

    If synthesize said decision=no_op or gave no candidates, return an
    empty string (hypothesis_propose will fall back to no hint).
    """
    decision = str(synth_sem.get("decision") or "")
    if decision != "candidate":
        return ""
    candidates = synth_sem.get("candidates") or []
    if not candidates:
        return ""
    primary = candidates[0] if isinstance(candidates[0], dict) else {}
    focus = str(primary.get("causal_hypothesis") or "").strip()
    if not focus:
        return ""
    return focus[:280]


def pre_iteration_reflect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Run observe -> counter_read -> synthesize -> emit, then return
    `improvement_focus` in the flow output so the next node (hypothesis_propose)
    can read it via params['upstream']['pre_iteration_reflect']."""
    pipeline_params = dict(params)
    pipeline_params["aspect"] = "pre_iteration"
    if "round_id" not in pipeline_params:
        pipeline_params["round_id"] = f"pre-{int(time.time())}-{params.get('job_id', '')}"

    obs = aspect_observe(ctx, pipeline_params)
    counter = aspect_counter_read(ctx, pipeline_params)
    synth_params = {**pipeline_params, "flow_outputs": {
        "observe": obs,
        "counter_read": counter,
    }}
    synth = aspect_synthesize(ctx, synth_params)
    emit_params = {**synth_params, "flow_outputs": {
        "observe": obs,
        "counter_read": counter,
        "synthesize": synth,
    }}
    emit = aspect_emit(ctx, emit_params)

    sem = (synth.get("semantic_output") or {}) if synth else {}
    improvement_focus = _improvement_focus_from_synth(sem)
    candidates = sem.get("candidates") or []
    return {
        "ok": True,
        "status": "completed",
        "semantic_output": {
            "improvement_focus": improvement_focus,
            "decision": sem.get("decision"),
            "candidates": candidates[:2],
            "observe_summary": obs.get("summary"),
            "counter_summary": counter.get("summary"),
            "synth_summary": synth.get("summary"),
            "emit_summary": emit.get("summary"),
        },
        "summary": (f"pre_iteration_reflect: decision={sem.get('decision')}, "
                    f"improvement_focus_len={len(improvement_focus)}"),
    }


DEFINITIONS = [
    EventDefinition("evolution.pre_iteration_reflect", "evolution",
                    "PROJECT_ITERATION 起始节点：用历史 PROJECT_ITERATION 轮次 + 自进化修改 + message_critic 拒收记录产 improvement_focus，注入到 hypothesis_propose prompt",
                    pre_iteration_reflect, execution_method="llm"),
]
