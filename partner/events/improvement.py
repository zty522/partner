"""Improvement-domain Event handlers.

Self-improvement (01) and learning-improvement (02) both feed an OpportunityRecord
into the shared autonomous_evolution experiment flow. The handlers below are
kept minimal so callers can plug in their own evidence collection.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import time

from partner.event_fabric.catalog import EventDefinition
from partner.runtime.action_execution import write_json
from partner.improvement.opportunity import (
    EvidenceBundle,
    OpportunityRecord,
    OpportunityStatus,
    list_opportunities,
)


def _semantic(value, summary=""):
    return {"ok": True, "status": "completed", "semantic_output": value,
            "summary": summary, "files": [], "evidence_refs": [], "token_usage": {}}


def improvement_recall(ctx, params):
    """Recall prior opportunity records for this instance and bundle."""
    instance_id = getattr(ctx, "instance_id", "")
    opps = list_opportunities(ctx.workspace, instance_id=instance_id)
    bundles_dir = Path(ctx.workspace) / "state/improvement_evidence"
    from partner.index.resource_catalog import ResourceCatalog
    bundle_files = [Path(r['path']) for r in ResourceCatalog(ctx.workspace).query('bundle',limit=10)]
    bundles = []
    for path in bundle_files[-10:]:
        try:
            bundles.append(json.loads(path.read_text()))
        except Exception:
            continue
    return _semantic({"opportunities": opps, "bundles": bundles,
        "instance_id": instance_id,
        "recall_summary": {"opp_count": len(opps), "bundle_count": len(bundles)}},
        f"recalled {len(opps)} opportunities and {len(bundles)} bundles")


def improvement_observe_plan(ctx, params):
    """Read recent runtime state to scope which internal mechanism to probe.

    Real runtime evidence only. Does not fabricate QQ messages or business rounds.
    """
    from partner.event_fabric import EventLedger
    from partner.index.resource_catalog import ResourceCatalog
    evidence=[r['path'] for r in ResourceCatalog(ctx.workspace).query('flow',limit=5)]
    return _semantic({"available_evidence": evidence[:20],
        "plan_mode": params.get("plan_mode", "self_improvement"),
        "max_observation_steps": int(params.get("max_observation_steps", 2)),
        "max_opportunities": int(params.get("max_opportunities", 1))},
        "observation plan ready; evidence paths listed")


def improvement_observe_execute(ctx, params):
    """Run at most N bounded, low-side-effect probes to confirm a current symptom.

    This handler does not restart business projects, send QQ messages, or
    fabricate message acknowledgments. Probes are limited to local partner/
    introspection: importing a module, calling a pure helper, reading an
    evidence file, or running an isolated pytest collection.
    """
    log_lines = []
    evidence_paths = []
    plan = saved_hop(ctx, params, "observe_plan") or {}
    targets = list(plan.get("available_evidence") or [])[: int(plan.get("max_observation_steps", 2))]
    for p in targets:
        path = Path(p)
        if not path.exists():
            continue
        evidence_paths.append(str(path))
        if path.is_dir():
            # Bounded listing: at most 20 files per directory.
            try:
                files = sorted([f for f in path.iterdir() if f.is_file()])[:20]
                for f in files:
                    evidence_paths.append(str(f))
                    try:
                        text = f.read_text(errors="replace")[:1000]
                        log_lines.append(f"probe {f.name}: {text[:150]}")
                    except Exception as exc:
                        log_lines.append(f"probe {f.name}: read_failed={exc}")
            except Exception as exc:
                log_lines.append(f"probe {path.name}: list_failed={exc}")
        elif path.is_file() and path.stat().st_size < 100000:
            try:
                text = path.read_text(errors="replace")[:2000]
                log_lines.append(f"probe {path.name}: {text[:200]}")
            except Exception as exc:
                log_lines.append(f"probe {path.name}: read_failed={exc}")
    return _semantic({"probed_paths": evidence_paths, "probe_summary": log_lines[:10],
        "side_effect": "internal only; no QQ delivery"},
        "observation probes completed (internal only)")


def improvement_evidence_seal(ctx, params):
    """Freeze a versioned EvidenceBundle at the start of self-improvement."""
    bundle_path = (params.get("bundle_path") or "").strip()
    instance_id = getattr(ctx, "instance_id", "")
    if not bundle_path or not Path(bundle_path).is_file():
        # Synthesize a runtime-observation bundle from current source.
        learning=saved_hop(ctx,params,'local_compare')
        candidate_paths=list((saved_hop(ctx,params,'observe_execute') or {}).get('probed_paths') or [])
        if learning:
            candidate_paths += [str(Path(ctx.working_dir)/'local_adaptation.json'),learning.get('reading_path','')]
            candidate_paths += learning.get('local_paths') or []
        for relative in ("partner/events/autonomous_evolution.py",
                         "partner/runtime/evolution_experiment.py",
                         "partner/event_flows/cycle.py"):
            full = Path("/mnt/e/work/partner") / relative
            if full.exists():
                candidate_paths.append(str(full))
        candidate_paths=[p for p in candidate_paths if p and Path(p).is_file()]
        bundle = EvidenceBundle.from_runtime_observation(instance_id, candidate_paths,
            "internal partner mechanisms only; no real QQ delivery")
    else:
        bundle = EvidenceBundle.from_sealed_cycle(bundle_path, ctx)
    if saved_hop(ctx,params,'local_compare'):
        bundle.origin='external_learning_opportunity'
    bundle_path = bundle.write(ctx.workspace)
    return _semantic({"bundle_id": bundle.bundle_id, "bundle_path": bundle_path,
        "origin": bundle.origin, "file_count": len(bundle.file_refs),
        "source_version": bundle.source_version},
        f"EvidenceBundle {bundle.bundle_id} sealed ({bundle.origin})")


def improvement_opportunity_assess(ctx, params):
    """Promote one evidence finding into an OpportunityRecord draft.

    Reads the observe probes and applies the typed OpportunityRecord contract.
    Does not pretend to know the root cause; only writes what the probes support.
    """
    probes = saved_hop(ctx, params, "observe_execute") or {}
    bundle_info = saved_hop(ctx, params, "evidence_seal") or {}
    instance_id = getattr(ctx, "instance_id", "")
    bundle_id = bundle_info.get("bundle_id", "")
    summary = []
    for p in probes.get("probed_paths", [])[:5]:
        summary.append({"path": p, "kind": "internal_probe"})
    learning=saved_hop(ctx,params,'local_compare')
    if learning:
        if learning.get('decision') != 'adopt_for_experiment':
            return _semantic({'opportunities':[], 'reason':learning.get('reason'), 'decision':learning.get('decision')})
        summary=[{'path':str(Path(ctx.working_dir)/'local_adaptation.json'),'kind':'local_adaptation'},
                 {'path':learning['reading_path'],'kind':'source_reading'}]
    if not summary:
        return _semantic({"opportunities": [], "no_probe_evidence": True,
            "unknowns": ["current probe could not reproduce any issue"],
            "counterevidence": "no probe evidence available"},
            "no reproducible opportunity; defer to evidence review")
    opp = OpportunityRecord(
        opportunity_id=f"opp-{int(time.time())}-{hash(tuple(s['path'] for s in summary))&0xffff:04x}",
        origin="external_learning_opportunity" if learning else "runtime_observation",
        type="optimize",
        instance_id=instance_id,
        scope="partner",
        capability="internal-mechanism",
        current_behavior=learning.get("current_behavior","observed via probe; requires diagnosis"),
        desired_behavior=learning.get("desired_behavior","real measurable improvement verifiable by paired experiment"),
        evidence_refs=[s["path"] for s in summary],
        bundle_id=bundle_id,
        source_version=bundle_info.get("source_version", ""),
        unknowns=["need real paired experiment to confirm improvement"],
        expected_value="bounded observable improvement",
        cost_and_risk="isolated experiment + rollback; max attempt budget",
        minimum_probe="isolated experiment per shared protocol",
    )
    opp.write(ctx.workspace)
    return _semantic({"opportunities": [opp.to_dict()],
        "draft_opportunity_id": opp.opportunity_id,
        "evidence_count": len(summary)},
        f"draft opportunity {opp.opportunity_id} written")


def improvement_opportunity_select(ctx, params):
    """Pick at most one OpportunityRecord and promote to SELECTED.

    Honest exit when no evidence-backed opportunity exists.
    """
    candidates = (saved_hop(ctx,params,'opportunity_assess') or {}).get('opportunities') or []
    if not candidates:
        return _semantic({"selected": [], "reason": "no candidate opportunities",
            "action": "no_change"}, "no opportunities to select")
    selected = []
    for cand in candidates[: int(params.get("max_opportunities", 1))]:
        opp_id = cand.get("opportunity_id")
        if not opp_id:
            continue
        opp = OpportunityRecord.load(ctx.workspace, opp_id)
        if opp is None:
            continue
        opp.status = OpportunityStatus.SELECTED
        opp.write(ctx.workspace)
        selected.append(opp.to_dict())
    return _semantic({"selected": selected,
        "selection_count": len(selected)},
        f"selected {len(selected)} opportunities")


def improvement_experiment_request(ctx, params):
    """Spawn the shared autonomous_evolution v2 child for the selected opportunity.

    Persists the parent/child link; does not pretend to have started the experiment.
    """
    selected = saved_hop(ctx, params, "opportunity_select") or {}
    sels = list(selected.get("selected") or [])
    if not sels:
        return _semantic({"status": "no_experiment", "reason": "no selected opportunity"},
            "no experiment launched; honest no-change exit")
    target = sels[0]
    cycle_child = {
        "flow": "autonomous_evolution",
        "owner_node": params.get("node_id", "experiment_request"),
        "context": {
            "intent_contract": params.get("intent_contract") or {},
            "evidence_refs": list(target.get("evidence_refs") or []),
            "experiment_context": {
                "opportunity_id": target.get("opportunity_id"),
                "opportunity_origin": target.get("origin"),
                "opportunity_type": target.get("type"),
                "bundle_id": target.get("bundle_id", ""),
                "expected_value": target.get("expected_value", ""),
                "minimum_probe": target.get("minimum_probe", ""),
                "apply_authorized": bool((params.get("intent_contract") or {}).get("execution_constraints", {}).get("evolution_apply")),
                "prompt_version": target.get("prompt_version", "opportunity.v1"),
            },
        },
    }
    return _semantic({"cycle_child": cycle_child,
        "opportunity_id": target.get("opportunity_id"),
        "experiment_status": "queued"},
        "autonomous_evolution child queued; awaiting real terminal state")


def improvement_outcome_settle(ctx, params):
    """Wait for the autonomous_evolution child to reach a real terminal state.

    Inspects the child flow state and persists it under the opportunity record.
    """
    selected = saved_hop(ctx, params, "opportunity_select") or {}
    sels = list(selected.get("selected") or [])
    if not sels:
        return _semantic({"settled": False, "reason": "no selection"}, "no outcome to settle")
    opp_id = sels[0].get("opportunity_id")
    opp = OpportunityRecord.load(ctx.workspace, opp_id)
    if opp is None:
        return _semantic({"settled": False, "reason": "opportunity not found"}, "opportunity missing")
    receipt=saved_hop(ctx,params,'experiment_request')
    record_path=receipt.get('record_path')
    if not record_path:
        return _semantic({'settled':False,'reason':'child terminal receipt missing'})
    record=json.loads(Path(record_path).read_text())
    if record.get('status') not in {'completed','failed','cancelled'}:
        return _semantic({'settled':False,'reason':'child still active'})
    final=record.get('node_outputs',{}).get('record',{}).get('semantic_output',{})
    return _semantic({'settled':True,'opportunity_id':opp_id,'child_status':record['status'],
        'record_path':record_path,'result':final,'production_effective':bool(final.get('production_effective'))},'子流程真实终态已归集')


def improvement_memory_consolidate(ctx, params):
    """Append lessons/habits/growth based on the settled outcome.

    No content is invented. If nothing in the outcome supports a new
    lesson/habit/growth, this stage returns an empty list rather than fabricate.
    """
    from partner.memory import EventMemory
    settled=saved_hop(ctx,params,'outcome_settle') or {}
    if not settled.get('settled'):
        return _semantic({'consolidated':[], 'reason':'no verified child terminal'})
    record={'record_id':'lesson:'+str(ctx.job_id),'project_id':params.get('project_id',''),
        'status':'active','scope':'this experiment only','content':settled['result'],
        'evidence_refs':[settled['record_path']], 'production_effective':settled['production_effective']}
    path=EventMemory(ctx.workspace).append_semantic('lesson',record)
    return _semantic({'consolidated':[{'kind':'lesson','path':path}],
        'habit':'not activated; requires independent repeated evidence',
        'growth':'not confirmed; requires independent repeated evidence'},'实验经验已记录，不将一次运行冒充长期成长')


def improvement_finish(ctx, params):
    """Mark the improvement cycle finished and stop.

    Mirrors the bounded contract: a single cycle, no further cycles.
    """
    return _semantic({"finished": True,
        "mode": params.get("mode", "self_improvement"),
        "instance_id": getattr(ctx, "instance_id", "")},
        "improvement cycle finished; no further cycles will be started")


# ---- internal helpers ----

def saved_hop(ctx, params, name):
    """Read an already-saved node output inside this flow.

    Prefers the runner-provided ``flow_outputs`` (in-memory, authoritative
    view of state.node_outputs), then falls back to the flow file, and
    finally to the legacy cycles/ dir.  The previous implementation only
    read the cycles/ dir, which is never populated by the improvement
    flows, so every downstream step saw an empty plan.
    """
    if params and isinstance(params, dict):
        flow_outputs = params.get("flow_outputs") or {}
        out = flow_outputs.get(name) or {}
        sem = out.get("semantic_output") or {}
        if sem:
            return sem
    flow_id = getattr(ctx, "flow_id", None) or (params or {}).get("flow_id")
    if flow_id:
        fp = Path(ctx.workspace) / "state/event_flows" / f"{flow_id}.json"
        if fp.exists():
            try:
                fd = json.loads(fp.read_text())
                out = fd.get("node_outputs", {}).get(name, {})
                sem = out.get("semantic_output") or {}
                if sem:
                    return sem
            except Exception:
                pass
    job_id = getattr(ctx, "job_id", None)
    if job_id:
        for sub in ("evolution", ""):
            path = (Path(ctx.workspace) / "state/cycles" / job_id / sub / f"{name}.json"
                    if sub else Path(ctx.workspace) / "state/cycles" / job_id / f"{name}.json")
            if path.exists():
                try:
                    return json.loads(path.read_text()).get("semantic_output") or {}
                except Exception:
                    pass
    return {}


DEFINITIONS = [
    EventDefinition("improvement.recall", "improvement", "recall prior opportunities",
                    improvement_recall, execution_method="local", timeout_seconds=60),
    EventDefinition("improvement.observe_plan", "improvement", "plan internal observations",
                    improvement_observe_plan, execution_method="local", timeout_seconds=120),
    EventDefinition("improvement.observe_execute", "improvement", "execute bounded internal probes",
                    improvement_observe_execute, execution_method="local", timeout_seconds=240),
    EventDefinition("improvement.evidence_seal", "improvement", "freeze evidence bundle",
                    improvement_evidence_seal, execution_method="local", timeout_seconds=60),
    EventDefinition("improvement.opportunity_assess", "improvement", "promote probe to opportunity",
                    improvement_opportunity_assess, execution_method="llm", timeout_seconds=180),
    EventDefinition("improvement.opportunity_select", "improvement", "select opportunity",
                    improvement_opportunity_select, execution_method="local", timeout_seconds=60),
    EventDefinition("improvement.experiment_request", "improvement", "spawn experiment child flow",
                    improvement_experiment_request, execution_method="local", timeout_seconds=60),
    EventDefinition("improvement.outcome_settle", "improvement", "settle outcome of child flow",
                    improvement_outcome_settle, execution_method="local", timeout_seconds=60),
    EventDefinition("improvement.memory_consolidate", "improvement", "consolidate memory if outcome supports",
                    improvement_memory_consolidate, execution_method="local", timeout_seconds=60),
    EventDefinition("improvement.finish", "improvement", "mark improvement cycle finished",
                    improvement_finish, execution_method="local", timeout_seconds=30),
]
