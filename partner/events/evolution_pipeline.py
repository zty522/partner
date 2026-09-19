"""Aspect-level self-reflection pipeline glue.

Five aspects (iteration | intent | message | pdf_report | event_flow)
each go through the same three-event review (observe / counter_read /
synthesize) plus a bookkeeping emit. ``trigger_aspect`` is the entry
point called by PROJECT_ITERATION.reflect_to_evolve and by other
schedule hooks; it runs the pipeline synchronously and decides whether
to spawn self_evolution.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from partner.event_fabric.catalog import EventDefinition
from ._llm import call_model, json_object
from .cross_cutting import (
    aspect_observe, aspect_counter_read, aspect_synthesize, aspect_emit,
    _workspace, _aspect,
)


def _state_path(workspace: str) -> Path:
    if not workspace:
        return Path()
    return Path(workspace) / "state/governance/aspect_state.json"


def _read_streak(workspace: str, aspect: str) -> int:
    path = _state_path(workspace)
    if not path.exists():
        return 0
    try:
        state = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return int(state.get(aspect, {}).get("no_op_streak", 0))
    except (OSError, ValueError):
        return 0


def _write_streak(workspace: str, aspect: str, decision: str, dispatched: bool) -> None:
    path = _state_path(workspace)
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else {}
    except (OSError, ValueError):
        state = {}
    entry = state.setdefault(aspect, {"no_op_streak": 0, "last_decision": "", "last_at": 0})
    if decision == "no_op":
        entry["no_op_streak"] = int(entry.get("no_op_streak", 0)) + 1
    else:
        entry["no_op_streak"] = 0
    entry["last_decision"] = decision
    entry["last_at"] = time.time()
    entry["last_dispatched"] = dispatched
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def trigger_aspect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Run the three-pass review for one aspect and dispatch to
    self_evolution if the synthesizer returned a non-trivial candidate.

    Designed to be called from PROJECT_ITERATION.reflect_to_evolve
    (aspect=iteration, every round) and from operator-scheduled hooks
    (other aspects, less often).
    """
    workspace = _workspace(ctx)
    aspect = _aspect(params)
    no_op_streak = _read_streak(workspace, aspect)

    pipeline_params = dict(params)
    pipeline_params["aspect"] = aspect
    pipeline_params["no_op_streak"] = no_op_streak
    pipeline_params["round_id"] = pipeline_params.get("round_id") or         f"{int(time.time())}-{params.get('job_id', '')}"

    observe_out = aspect_observe(ctx, pipeline_params)
    counter_out = aspect_counter_read(ctx, pipeline_params)
    synth_params = {**pipeline_params, "flow_outputs": {
        "observe": observe_out,
        "counter_read": counter_out,
    }}
    synth_out = aspect_synthesize(ctx, synth_params)

    emit_params = {**synth_params, "flow_outputs": {
        "observe": observe_out,
        "counter_read": counter_out,
        "synthesize": synth_out,
    }}
    emit_out = aspect_emit(ctx, emit_params)

    decision = (synth_out.get("semantic_output") or {}).get("decision") or "no_op"
    candidates = (synth_out.get("semantic_output") or {}).get("candidates") or []
    dispatched = False

    if decision == "candidate" and candidates:
        from partner.governance.evolution_events import append_evolution_event
        if append_evolution_event is not None and workspace:
            try:
                append_evolution_event(
                    workspace,
                    "aspect/candidate_emitted",
                    subject_id=f"aspect/{aspect}/{pipeline_params['round_id']}",
                    project_id=str(params.get("project_id") or ""),
                    payload={"aspect": aspect, "candidates": candidates,
                             "next_step": "spawn self_evolution flow"},
                    idempotency_key=f"aspect-candidate-emit:{aspect}:{pipeline_params['round_id']}",
                )
                dispatched = True
            except (OSError, ValueError):
                pass

    _write_streak(workspace, aspect, decision, dispatched)

    return {"ok": True, "status": "completed",
            "semantic_output": {
                "aspect": aspect,
                "decision": decision,
                "no_op_streak_after": 0 if decision != "no_op" else no_op_streak + 1,
                "candidates": candidates,
                "observe_summary": observe_out.get("summary"),
                "counter_summary": counter_out.get("summary"),
                "synth_summary": synth_out.get("summary"),
                "dispatched_to_self_evolution": dispatched,
                "no_op_streak": no_op_streak,
            },
            "summary": (f"aspect={aspect} trigger: decision={decision}, "
                        f"candidates={len(candidates)}, dispatched={dispatched}, "
                        f"no_op_streak={no_op_streak}")}


def reflect_to_evolve(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """PROJECT_ITERATION flow node entry: trigger the iteration aspect.

    Returns ok=True even when no candidate is found (the most common
    case). Self-evolution spawn happens only when trigger_aspect
    decides candidates are non-trivial.
    """
    workspace = _workspace(ctx)
    aspect = "iteration"
    round_id = params.get("round_id") or         f"{int(time.time())}-{params.get('job_id', '')}"
    p = dict(params)
    p["aspect"] = aspect
    p["round_id"] = round_id
    out = trigger_aspect(ctx, p)
    sem = out.get("semantic_output") or {}
    decision = sem.get("decision") or "no_op"
    candidates = sem.get("candidates") or []

    if decision == "candidate" and candidates and workspace:
        try:
            from partner.event_fabric import EventFlowController, EventFlowStore
            # build_flow_registry lives in partner.event_flows.registry; importing it
            # from partner.event_fabric raises ImportError and fails the node.
            from partner.event_flows.registry import build_flow_registry
            from partner.event_fabric.catalog import build_catalog
            catalog = build_catalog(workspace=workspace)
            catalog.snapshot(Path(workspace) / "state/event_catalog" / f"catalog_{catalog.version}.json")
            definition = build_flow_registry().get("self_evolution")
            controller = EventFlowController(EventFlowStore(workspace))
            primary = candidates[0]
            target_files = primary.get("target_files") or []
            if isinstance(target_files, str):
                target_files = [target_files]
            intent_contract = {
                "target_files": target_files,
                "causal_hypothesis": primary.get("causal_hypothesis", ""),
                "improvement_focus": primary.get("expected_improvement", ""),
                "rollback": primary.get("rollback", ""),
                "risk": primary.get("risk", ""),
                "aspect": aspect,
                "source_candidate": primary,
            }
            flow_state = controller.start(
                definition, catalog_version=catalog.version,
                task_id=params.get("job_id") or round_id,
                project_id=str(params.get("project_id") or "agent_self_evolution"),
                instance_id=str(params.get("instance_id") or "self_evolution"),
            )
            EventFlowStore(workspace).save(flow_state)
            sem["self_evolution_flow_id"] = flow_state.flow_id
            out["summary"] = out.get("summary", "") + f" | spawned self_evolution {flow_state.flow_id}"
        except (OSError, ValueError, KeyError) as exc:
            sem["self_evolution_error"] = str(exc)
            out["summary"] = out.get("summary", "") + f" | self_evolution spawn failed: {exc}"

    out["semantic_output"] = sem
    return out


DEFINITIONS = [
    EventDefinition("evolution.trigger_aspect", "evolution",
                    "按 aspect 触发三段式自反思，必要时派发到 self_evolution",
                    trigger_aspect, execution_method="llm"),
    EventDefinition("evolution.reflect_to_evolve", "evolution",
                    "PROJECT_ITERATION 末尾的反思节点；本轮无意义改进则 no_op",
                    reflect_to_evolve, execution_method="llm"),
]
