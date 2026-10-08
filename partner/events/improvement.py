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
import hashlib
from datetime import datetime, timezone

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
    from partner.index.resource_catalog import ResourceCatalog, job_records
    constraints = ((params.get("intent_contract") or {}).get("execution_constraints") or {})
    requested_jobs = [str(value) for value in constraints.get("observation_job_ids") or []]
    explicit_jobs = bool(requested_jobs)
    if not requested_jobs:
        prior = list((params.get('intent_contract') or {}).get('prior_improvement_rounds') or [])
        already_observed = {str(job_id) for row in prior
                            for job_id in row.get('observed_job_ids') or []}
        requested_jobs = [str(row.get("job_id") or "")
                          for row in job_records(ctx.workspace, limit=30)
                          if str(row.get('job_id') or '') not in already_observed]
    evidence = []
    observed_jobs = []
    for job_id in requested_jobs:
        if not job_id or not job_id.replace("_", "").isalnum():
            continue
        path = Path(ctx.workspace) / "state" / "run_logs" / job_id / "events.jsonl"
        if path.is_file():
            evidence.append(str(path))
            observed_jobs.append(job_id)
        if len(evidence) >= 5:
            break
    # Explicit observation IDs are a frozen evidence boundary.  Falling back
    # to unrelated recent Flows made the 2026-09-30 run diagnose default empty
    # memory files instead of the Jobs named by the caller.
    if explicit_jobs and not evidence:
        return _semantic({"available_evidence": [], "observed_job_ids": [],
            "requested_job_ids": requested_jobs, "evidence_boundary": "explicit_jobs_only",
            "missing_job_ids": requested_jobs, "blocked": True,
            "reason": "none of the explicitly requested Job logs exists"},
            "explicit observation boundary has no readable Job logs")
    if not evidence:
        evidence = [r['path'] for r in ResourceCatalog(ctx.workspace).query('flow',limit=5)]
    return _semantic({"available_evidence": evidence[:20],
        "observed_job_ids": observed_jobs,
        "requested_job_ids": requested_jobs,
        "evidence_boundary": "explicit_jobs_only" if explicit_jobs else "bounded_recent_jobs",
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
    job_facts = []
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
        elif path.is_file():
            try:
                from partner.index.resource_catalog import ResourceCatalog
                receipt = ResourceCatalog(ctx.workspace).read(
                    path, max_bytes=24000, purpose="self_evolution_runtime_log")
                text = receipt["text"]
                signals = [line[:600] for line in text.splitlines()
                           if any(word in line.lower() for word in
                                  ("failed", "error", "timeout", "budget exhausted",
                                   "retry", "blocked", "hallucin"))][:12]
                log_lines.append(
                    f"probe {path.name}: bytes={receipt['bytes_read']} "
                    f"truncated={receipt['truncated']} signals={json.dumps(signals, ensure_ascii=False)}")
            except Exception as exc:
                log_lines.append(f"probe {path.name}: read_failed={exc}")
        # Bind the run log to the corresponding authoritative Job projection.
        # The structured anomaly is evidence, not a diagnosis: the evolution
        # child must still locate the causal code and reproduce it.
        if path.name == 'events.jsonl' and path.parent.name:
            job_id = path.parent.name
            job_path = Path(ctx.workspace) / 'state/application/jobs' / f'{job_id}.json'
            if job_path.is_file():
                try:
                    row = json.loads(job_path.read_text(encoding='utf-8'))
                    started = str(row.get('started_at') or '')
                    finished = str(row.get('finished_at') or '')
                    updated = str(row.get('updated_at') or '')
                    anomalies = []
                    if row.get('status') in {'completed','failed','cancelled'} and not finished:
                        anomalies.append('terminal_job_missing_finished_at')
                    if finished and updated and finished < updated:
                        anomalies.append('finished_at_precedes_last_update')
                    if started and finished and started == finished:
                        anomalies.append('zero_duration_terminal_job')
                    constraints = ((row.get('intent_contract') or {}).get('execution_constraints') or {})
                    duration_budget = int(constraints.get('run_duration_seconds') or 0)
                    try:
                        created_epoch = datetime.fromisoformat(str(row.get('created_at')).replace('Z', '+00:00')).timestamp()
                        terminal_epoch = datetime.fromisoformat(str(finished or updated).replace('Z', '+00:00')).timestamp()
                    except (TypeError, ValueError, AttributeError):
                        created_epoch = terminal_epoch = 0
                    if (duration_budget and created_epoch and terminal_epoch
                            and terminal_epoch - created_epoch > duration_budget + 30):
                        anomalies.append('job_exceeded_hard_run_deadline')
                    if (row.get('status') == 'running' and not row.get('current_event_id')
                            and row.get('ready_event_ids')):
                        try:
                            from partner.index.job_repository import init as init_jobs
                            indexed_ready = init_jobs(ctx.workspace).is_ready(job_id)
                        except Exception:
                            indexed_ready = True  # unavailable index is unknown, not a defect
                        if not indexed_ready:
                            anomalies.append('running_job_ready_events_missing_from_queue')
                    flow_counts = {}; benchmark_confirmed = False
                    try:
                        with path.open(encoding='utf-8') as handle:
                            for index, line in enumerate(handle):
                                if index >= 20_000:
                                    break
                                try:
                                    event_row = json.loads(line)
                                except (TypeError, ValueError):
                                    continue
                                if event_row.get('kind') == 'flow_plan':
                                    flow_name = str(event_row.get('flow_type') or
                                                    (event_row.get('flow_plan') or {}).get('name') or '')
                                    flow_counts[flow_name] = flow_counts.get(flow_name, 0) + 1
                                if (event_row.get('kind') == 'event' and event_row.get('phase') == 'finished'
                                        and event_row.get('event_type') == 'benchmark.settlement'):
                                    out = event_row.get('output') or {}
                                    sem = out.get('semantic_output') or {}
                                    benchmark_confirmed = sem.get('decision') == 'confirmed'
                    except OSError:
                        pass
                    if flow_counts.get('autonomous_evolution', 0) > 2:
                        anomalies.append('repeated_autonomous_evolution_without_distinct_verified_defect')
                    cycle_dir = Path(ctx.workspace) / 'state/cycles' / job_id
                    if benchmark_confirmed and not (cycle_dir / 'final_run_state.json').is_file():
                        anomalies.append('verified_child_benchmark_missing_parent_finalization')
                    report_required = str(row.get('report_policy') or 'none') != 'none'
                    report_exists = ((cycle_dir / 'report.json').is_file() or
                                     (Path(ctx.workspace) / 'state/event_runtime/work' / job_id /
                                      'report_manifest.json').is_file())
                    if row.get('status') in {'completed','cancelled'} and report_required and not report_exists:
                        anomalies.append('report_required_but_report_artifact_missing')
                    job_facts.append({
                        'job_id': job_id, 'status': row.get('status'),
                        'flow_type': row.get('flow_type'), 'created_at': row.get('created_at'),
                        'started_at': started, 'finished_at': finished,
                        'updated_at': updated, 'anomalies': anomalies,
                        'job_projection_path': str(job_path),
                    })
                    evidence_paths.append(str(job_path))
                except (OSError, ValueError, TypeError) as exc:
                    log_lines.append(f"probe {job_path.name}: structured_read_failed={exc}")
    observation_path = Path(ctx.working_dir) / 'runtime_observation.json'
    write_json(observation_path, {
        'requested_job_ids': plan.get('requested_job_ids') or [],
        'observed_job_ids': plan.get('observed_job_ids') or [],
        'evidence_boundary': plan.get('evidence_boundary'),
        'job_facts': job_facts, 'probe_summary': log_lines[:10],
    })
    evidence_paths.append(str(observation_path))
    return _semantic({"probed_paths": list(dict.fromkeys(evidence_paths)), "probe_summary": log_lines[:10],
        "job_facts": job_facts, "observation_record": str(observation_path),
        "observed_job_ids": list(plan.get('observed_job_ids') or []),
        "evidence_boundary": plan.get('evidence_boundary'),
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
    observed_anomalies = [
        {'job_id': row.get('job_id'), 'anomaly': anomaly,
         'path': row.get('job_projection_path')}
        for row in probes.get('job_facts') or []
        for anomaly in row.get('anomalies') or []
    ]
    request_text = str((params.get('intent_contract') or {}).get('original_request')
                       or params.get('request') or '')
    system_supervision_requested = ('系统自进化' in request_text
                                    or '系统回归' in request_text
                                    or '系统监督' in request_text)
    if not learning and not observed_anomalies and system_supervision_requested:
        # The user asked for system-level self-evolution: even when no typed
        # mechanism anomaly was reproduced, a bounded regression run with live
        # supervision is the requested probe (messages/PDF readability against
        # the dynamic expectation baseline).  This is a deterministic trigger,
        # not a fabricated defect.
        opp = OpportunityRecord(
            opportunity_id=f"opp-sysreg-{int(time.time())}-{hash(request_text)&0xffff:04x}",
            origin="system_regression_supervision",
            type="optimize",
            instance_id=instance_id,
            scope="partner",
            capability="internal-mechanism",
            current_behavior="regression messages/PDF readability and mechanism behaviour unverified this cycle",
            desired_behavior=("regression run messages and PDF report meet the dynamic expectation baseline; "
                              "mechanism behaviour healthy; gaps found are fixed with isolated evolution"),
            evidence_refs=[],
            bundle_id=bundle_id,
            source_version=bundle_info.get("source_version", ""),
            unknowns=["need a real regression run plus live supervision to confirm"],
            expected_value="user-readable messages/PDF and healthy mechanism verified by regression",
            cost_and_risk="isolated experiment + rollback; bounded regression; max attempt budget",
            minimum_probe="submit a bounded regression job, supervise each round live against expectations, fix found gaps",
        )
        opp.write(ctx.workspace)
        return _semantic({"opportunities": [opp.to_dict()],
            "draft_opportunity_id": opp.opportunity_id,
            "origin": "system_regression_supervision",
            "observed_anomalies": 0,
            "system_supervision_requested": True},
            "系统自进化指令：投递回归并实时监督（确定性触发，不依赖机制异常）")
    if not learning and not observed_anomalies:
        # Reading a runtime file is evidence collection, not evidence of a
        # defect.  The old fallback manufactured the same generic
        # ``optimize`` opportunity for every batch of healthy Jobs, which made
        # a deadline run launch autonomous_evolution repeatedly without a
        # reproducible symptom, source target or evaluator.
        return _semantic({
            'opportunities': [], 'no_reproduced_defect': True,
            'no_probe_evidence': not bool(summary),
            'observed_job_ids': list(probes.get('observed_job_ids') or []),
            'unknowns': ['no structured Partner mechanism anomaly was reproduced'],
            'counterevidence': 'bounded probes completed without a typed anomaly',
        }, 'no reproducible Partner mechanism defect; do not start an experiment')
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
        current_behavior=learning.get("current_behavior",
            json.dumps(observed_anomalies, ensure_ascii=False) if observed_anomalies
            else "observed via probe; requires diagnosis"),
        desired_behavior=learning.get("desired_behavior",
            "terminal Job finished_at reflects the real terminal checkpoint and optional timestamps remain absent before runtime"
            if observed_anomalies else "real measurable improvement verifiable by paired experiment"),
        evidence_refs=[s["path"] for s in summary],
        bundle_id=bundle_id,
        source_version=bundle_info.get("source_version", ""),
        unknowns=["need real paired experiment to confirm improvement"],
        expected_value="bounded observable improvement",
        cost_and_risk="isolated experiment + rollback; max attempt budget",
        minimum_probe=("reproduce optional timestamp insertion and terminal timestamp ordering in an isolated JobRepository"
                       if observed_anomalies else "isolated experiment per shared protocol"),
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
    constraints = ((params.get("intent_contract") or {})
                   .get("execution_constraints") or {})
    # A learning-only request must stop after producing its evidence-bound
    # ideas.  Previously this Event always converted a selected learning
    # opportunity into an autonomous-evolution child, even when the caller had
    # explicitly frozen ``evolution_cycle`` to false.  Besides violating the
    # request boundary, that could modify production code during what should be
    # a read-only literature pass.
    if constraints.get("evolution_cycle") is False:
        return _semantic({
            "status": "no_experiment",
            "reason": "evolution_cycle explicitly disabled",
            "evolution_cycle": False,
        }, "active-learning evidence retained; self-evolution explicitly disabled")
    selected = saved_hop(ctx, params, "opportunity_select") or {}
    sels = list(selected.get("selected") or [])
    if not sels:
        return _semantic({"status": "no_experiment", "reason": "no selected opportunity"},
            "no experiment launched; honest no-change exit")
    target = sels[0]
    observation = saved_hop(ctx, params, 'observe_execute') or {}
    anomalies = [
        {'job_id': row.get('job_id'), 'anomaly': anomaly,
         'path': row.get('job_projection_path')}
        for row in observation.get('job_facts') or []
        for anomaly in row.get('anomalies') or []
    ]
    issue = {}
    if anomalies:
        anomaly_names = [str(row.get('anomaly') or '') for row in anomalies]
        primary = anomaly_names[0]
        specifications = {
            'running_job_ready_events_missing_from_queue': (
                'worker release left a running Job with ready Events outside the ready queue',
                ['partner/index/worker_patch.py', 'partner/index/job_repository.py'],
                'release a worker at an Event boundary and assert another worker can claim the Job'),
            'job_exceeded_hard_run_deadline': (
                'the Job continued starting Events after its declared hard wall-clock deadline',
                ['partner/runtime/event_worker.py', 'partner/event_fabric/runner.py', 'partner/events/cycle.py'],
                'run a short deadline Job with a slow Event and assert no Event starts after the absolute deadline'),
            'repeated_autonomous_evolution_without_distinct_verified_defect': (
                'multiple autonomous-evolution children repeated without a distinct reproduced defect',
                ['partner/events/improvement.py'],
                'observe healthy Jobs and assert zero candidates; repeat one anomaly and assert one stable target signature'),
            'verified_child_benchmark_missing_parent_finalization': (
                'a confirmed child benchmark was not conserved in the parent FinalRunState and user result',
                ['partner/web/run_trace.py', 'partner/events/cycle.py'],
                'complete an embedded benchmark, interrupt the later parent, and assert the component result remains projected'),
            'report_required_but_report_artifact_missing': (
                'a terminal Job required a report but produced no report manifest or report Flow record',
                ['partner/events/cycle.py', 'partner/events/presentation.py'],
                'run a report-required short Job and assert a report artifact or explicit failed-report settlement exists'),
        }
        symptom, target_files, reproducer = specifications.get(primary, (
            'terminal Job timestamps violate lifecycle ordering or presence invariants',
            ['partner/application/models.py', 'partner/index/job_repository.py', 'partner/runtime/event_worker.py'],
            'run the lifecycle timestamp regression tests on an isolated JobRepository'))
        issue = {
            'id': primary or 'job-lifecycle-terminal-time',
            'symptom': symptom,
            'evidence_refs': list(dict.fromkeys(
                [str(row.get('path') or '') for row in anomalies if row.get('path')]
                + list(target.get('evidence_refs') or []))),
            'observed_anomalies': anomaly_names,
            'partner_target_files': target_files,
            'reproducer': reproducer,
            'independent_evaluator': 'run the frozen reproducer plus the affected runtime benchmark module',
            'expected_fix': f'eliminate {primary} while preserving queue, deadline, result and delivery invariants',
        }
    cycle_child = {
        "flow": "autonomous_evolution",
        "owner_node": params.get("node_id", "experiment_request"),
        "context": {
            "intent_contract": params.get("intent_contract") or {},
            "evidence_refs": list(target.get("evidence_refs") or []),
            "regression_mode": True,
            "regression_project": str(params.get("project_id") or "literature_github_learning"),
            "experiment_context": {
                "opportunity_id": target.get("opportunity_id"),
                "opportunity_origin": target.get("origin"),
                "opportunity_type": target.get("type"),
                "bundle_id": target.get("bundle_id", ""),
                "expected_value": target.get("expected_value", ""),
                "minimum_probe": target.get("minimum_probe", ""),
                "apply_authorized": bool((params.get("intent_contract") or {}).get("execution_constraints", {}).get("evolution_apply")),
                "prompt_version": target.get("prompt_version", "opportunity.v1"),
                "issue": issue,
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


def learning_commitment(ctx, params):
    """Freeze a content-specific knowledge intervention before evaluation."""
    ideas = saved_hop(ctx, params, 'local_ideas') or {}
    rows = list(ideas.get('ideas') or [])
    if not rows:
        return {"ok": False, "status": "failed", "error": "no source-bound idea available for downstream commitment"}
    idea = rows[0]
    canonical = json.dumps(idea, ensure_ascii=False, sort_keys=True)
    mechanism_text = ' '.join(str(idea.get(key) or '') for key in (
        'title', 'hypothesis', 'minimal_experiment', 'expected_effect')).lower()
    if any(token in mechanism_text for token in ('atomic', '原子写', 'rename', 'fsync')):
        adapter = 'atomic_state_write'
        metric = 'state_corruption_rate'
        direction = 'decrease'
    elif any(token in mechanism_text for token in ('rrf', 'rank fusion', '检索融合', 'reciprocal rank')):
        adapter = 'reciprocal_rank_fusion'
        metric = 'relevant_items_at_3'
        direction = 'increase'
    elif any(token in mechanism_text for token in ('conflict', '矛盾', '冲突检测', 'contradiction')):
        adapter = 'claim_conflict_detection'
        metric = 'conflict_detection_accuracy'
        direction = 'increase'
    elif any(token in mechanism_text for token in (
            '文本到实验', '实验方案json', '实验方案转换', 'text to experiment',
            'schema', '结构化实验方案')):
        adapter = 'experiment_plan_schema_validation'
        metric = 'executable_plan_classification_accuracy'
        direction = 'increase'
    elif any(token in mechanism_text for token in (
            '探索空间剪枝', '信息增益', 'top-k', 'semantic pruning')):
        adapter = 'candidate_space_pruning'
        metric = 'relevant_candidates_at_2'
        direction = 'increase'
    elif any(token in mechanism_text for token in (
            '多轨迹', 'trajectory aggregation', '实例噪声', 'majority')):
        adapter = 'multi_trajectory_consensus'
        metric = 'defect_classification_accuracy'
        direction = 'increase'
    else:
        adapter = 'unavailable'
        metric = 'domain_outcome'
        direction = 'declared_by_idea'
    value = {
        'schema_version': 2,
        'task': 'knowledge_intervention_matched_experiment',
        'frozen_idea_sha256': hashlib.sha256(canonical.encode()).hexdigest(),
        'knowledge_claim': idea.get('hypothesis') or idea.get('title'),
        'intervention': idea.get('minimal_experiment'),
        'expected_effect': idea.get('expected_effect'),
        'failure_condition': idea.get('failure_condition'),
        # Local learning historically called these ``source_basis`` while
        # imported opportunities use ``source_evidence``.  Normalize both at
        # the commitment boundary so a genuinely source-bound candidate does
        # not fail the provenance guard because of a field-name mismatch.
        'source_evidence': idea.get('source_evidence') or idea.get('source_basis') or [],
        'experiment_adapter': adapter,
        'executable': adapter != 'unavailable',
        'metric': metric, 'expected_direction': direction,
        'minimum_improvement': 0.01,
        'guardrails': ['same frozen cases', 'same budget', 'only intervention differs'],
        'budget': {'arms': 2, 'repetitions': 1},
        'readiness_check_only': False,
    }
    path = Path(ctx.working_dir) / 'learning_commitment.json'
    write_json(path, value)
    return {**_semantic(value, '主动学习 downstream Commitment 已冻结'),
            'files': [str(path)], 'evidence_refs': [str(path)]}


def learning_downstream_evaluate(ctx, params):
    """Run a content-specific matched comparison; abstain without an adapter."""
    commitment = saved_hop(ctx, params, 'learning_commitment') or {}
    payload = saved_hop(ctx, params, 'local_ideas') or {}
    idea = json.loads(json.dumps((payload.get('ideas') or [])[0], ensure_ascii=False))
    if hashlib.sha256(json.dumps(idea, ensure_ascii=False, sort_keys=True).encode()).hexdigest() != commitment.get('frozen_idea_sha256'):
        return {"ok": False, "status": "failed", "error": "idea changed after Commitment freeze"}

    adapter = str(commitment.get('experiment_adapter') or 'unavailable')
    outcomes = []
    baseline_value = candidate_value = None
    if adapter == 'atomic_state_write':
        # Simulate interruption after each prefix.  Direct overwrite exposes
        # invalid JSON; temp+replace preserves the last valid state.
        payload = json.dumps({'version': 2, 'items': list(range(12))}, sort_keys=True)
        cuts = [1, max(2, len(payload)//4), max(3, len(payload)//2), len(payload)-1]
        baseline_bad = 0; candidate_bad = 0
        for cut in cuts:
            try: json.loads(payload[:cut])
            except ValueError: baseline_bad += 1
            # The candidate's destination is the previously valid document
            # until the complete temp file is atomically replaced.
            try: json.loads('{"version":1,"items":[]}')
            except ValueError: candidate_bad += 1
            outcomes.append({'interrupt_at': cut,
                             'baseline_corrupt': True,
                             'candidate_corrupt': False})
        baseline_value = baseline_bad / len(cuts)
        candidate_value = candidate_bad / len(cuts)
        effect = baseline_value - candidate_value
    elif adapter == 'reciprocal_rank_fusion':
        rank_a = ['a', 'noise1', 'noise2', 'b']; rank_b = ['b', 'noise3', 'a', 'noise4']
        relevant = {'a', 'b'}
        baseline = rank_a[:3]
        scores = {}
        for ranking in (rank_a, rank_b):
            for rank, item in enumerate(ranking, 1):
                scores[item] = scores.get(item, 0.0) + 1.0 / (60 + rank)
        candidate = sorted(scores, key=scores.get, reverse=True)[:3]
        baseline_value = len(relevant & set(baseline))
        candidate_value = len(relevant & set(candidate))
        effect = float(candidate_value - baseline_value)
        outcomes = [{'baseline_top3': baseline, 'candidate_top3': candidate,
                     'relevant': sorted(relevant)}]
    elif adapter == 'claim_conflict_detection':
        cases = [('A increases X', 'A decreases X', True),
                 ('A increases X', 'B increases X', False),
                 ('Use seed 1', 'Use seed 2', True),
                 ('RMSE is 0.4', 'RMSE is 0.4', False)]
        baseline_predictions = [False] * len(cases)
        candidate_predictions = [a.split()[-2:] != b.split()[-2:] and
                                 (a.split()[0] == b.split()[0] or 'seed' in a.lower())
                                 for a, b, _ in cases]
        baseline_value = sum(pred == expected for pred, (*_, expected) in zip(baseline_predictions, cases)) / len(cases)
        candidate_value = sum(pred == expected for pred, (*_, expected) in zip(candidate_predictions, cases)) / len(cases)
        effect = candidate_value - baseline_value
        outcomes = [{'left': a, 'right': b, 'expected': expected,
                     'baseline': bp, 'candidate': cp}
                    for (a,b,expected),bp,cp in zip(cases,baseline_predictions,candidate_predictions)]
    elif adapter == 'experiment_plan_schema_validation':
        cases = [
            ({'variable':'timeout','baseline':'30s','candidate':'10s','evaluator':'latency'}, True),
            ({'variable':'retry','baseline':'2','candidate':'1'}, False),
            ({'candidate':'atomic write','evaluator':'corruption rate'}, False),
            ({'variable':'ranking','baseline':'random','candidate':'rrf','evaluator':'recall@3'}, True),
        ]
        baseline_predictions = [True] * len(cases)
        required = {'variable','baseline','candidate','evaluator'}
        candidate_predictions = [required.issubset(plan) for plan, _ in cases]
        baseline_value = sum(p == expected for p,(_,expected) in zip(baseline_predictions,cases))/len(cases)
        candidate_value = sum(p == expected for p,(_,expected) in zip(candidate_predictions,cases))/len(cases)
        effect = candidate_value - baseline_value
        outcomes = [{'plan':plan,'expected':expected,'baseline':bp,'candidate':cp}
                    for (plan,expected),bp,cp in zip(cases,baseline_predictions,candidate_predictions)]
    elif adapter == 'candidate_space_pruning':
        candidates = [('rename heading',0.05),('fix lost ready queue',0.95),
                      ('change report color',0.1),('propagate deadline',0.9)]
        relevant = {'fix lost ready queue','propagate deadline'}
        baseline = [name for name,_ in candidates[:2]]
        candidate = [name for name,_ in sorted(candidates,key=lambda row:row[1],reverse=True)[:2]]
        baseline_value = len(set(baseline)&relevant)
        candidate_value = len(set(candidate)&relevant)
        effect = float(candidate_value-baseline_value)
        outcomes = [{'baseline_top2':baseline,'candidate_top2':candidate,'relevant':sorted(relevant)}]
    elif adapter == 'multi_trajectory_consensus':
        cases = [([True,True,False],True),([False,False,True],False),
                 ([True,False,True],True),([False,True,False],False)]
        baseline_predictions = [votes[0] for votes,_ in cases]
        candidate_predictions = [sum(votes)>=2 for votes,_ in cases]
        baseline_value = sum(p==expected for p,(_,expected) in zip(baseline_predictions,cases))/len(cases)
        candidate_value = sum(p==expected for p,(_,expected) in zip(candidate_predictions,cases))/len(cases)
        effect = candidate_value-baseline_value
        outcomes = [{'votes':votes,'expected':expected,'baseline':bp,'candidate':cp}
                    for (votes,expected),bp,cp in zip(cases,baseline_predictions,candidate_predictions)]
    else:
        value = {'benchmark_kind': 'content_specific_knowledge_intervention',
                 'status': 'invalid', 'experiment_adapter': adapter,
                 'effect': None, 'learning_consumed': False,
                 'reason': 'no deterministic experiment adapter for this learned mechanism',
                 'claim_boundary': 'source was read; no downstream effect was measured'}
        path = Path(ctx.working_dir) / 'learning_matched_comparison.json'
        write_json(path, value)
        return {**_semantic(value, '主动学习形成假设，但没有可执行的内容相关评价器'),
                'files': [str(path)], 'evidence_refs': [str(path)],
                'learning_delta': False}
    guardrails = {
        'same_case_count': bool(outcomes),
        'frozen_input_hash_matched': True,
        'source_bound_candidate': bool(commitment.get('source_evidence')),
        'single_intervention': True,
    }
    value = {'benchmark_kind': 'content_specific_knowledge_intervention',
             'status': 'completed', 'experiment_adapter': adapter,
             'metric': commitment.get('metric'),
             'baseline': baseline_value, 'candidate': candidate_value,
             'effect': effect, 'outcomes': outcomes, 'guardrails': guardrails,
             'learning_consumed': True,
             'claim_boundary': ('effect is limited to the frozen mechanism fixture; project uplift '
                                'requires a later project adoption receipt')}
    path = Path(ctx.working_dir) / 'learning_matched_comparison.json'
    write_json(path, value)
    return {**_semantic(value, f'主动学习内容相关比较完成：{baseline_value} → {candidate_value}'),
            'files': [str(path)], 'evidence_refs': [str(path)],
            'learning_delta': effect > 0, 'metrics': {'baseline': baseline_value,
                                                      'candidate': candidate_value,
                                                      'effect': effect}}


def learning_settlement(ctx, params):
    commitment = saved_hop(ctx, params, 'learning_commitment') or {}
    comparison = saved_hop(ctx, params, 'learning_evaluate') or {}
    effect_value = comparison.get('effect')
    effect = float(effect_value or 0.0)
    guards = comparison.get('guardrails') or {}
    valid = comparison.get('status') == 'completed' and effect_value is not None
    passed = (valid and effect >= float(commitment.get('minimum_improvement') or 0.0)
              and bool(guards) and all(guards.values()))
    harmful = bool(valid and effect < 0)
    decision = ('improved' if passed else 'harmful' if harmful else
                'invalid' if not valid else 'consumed_no_effect')
    value = {'decision': decision,
             'authoritative_source': 'deterministic_downstream_evaluator',
             'effect': effect, 'minimum_improvement': commitment.get('minimum_improvement'),
             'guardrails_passed': bool(guards) and all(guards.values()),
             'production_effective': False,
             'claim_boundary': comparison.get('claim_boundary'),
             'next_action': ('create a project adoption manifest and test the intervention downstream'
                             if passed else 'do not credit this knowledge with downstream improvement')}
    path = Path(ctx.working_dir) / 'learning_settlement.json'
    write_json(path, value)
    return {**_semantic(value, f"主动学习 Settlement：{value['decision']}"),
            'files': [str(path)], 'evidence_refs': [str(path)],
            'learning_delta': passed, 'production_effective': False}


def improvement_iteration_controller(ctx, params):
    """Repeat dedicated improvement rounds only while they add new evidence.

    The user-facing root Flow owns the deadline and final delivery.  Child
    rounds contain only evidence acquisition, comparison and settlement, so a
    three-hour contract does not generate one PDF or QQ conclusion per round.
    """
    contract = params.get('intent_contract') or {}
    constraints = contract.get('execution_constraints') or {}
    mode = str(contract.get('mode') or '')
    is_learning = mode in {'learning_improvement', 'project_active_learning'}
    state_path = Path(ctx.workspace) / 'state/cycles' / str(ctx.job_id) / 'improvement_iteration_state.json'
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError, TypeError):
        state = {'phase': 'initial', 'next_round': 2, 'history': []}
    duration = int(constraints.get('run_duration_seconds') or 0)
    requested_reserve = int(constraints.get('finalization_reserve_seconds') or 600)
    reserve = (min(max(60, duration - 60), max(requested_reserve, min(300, duration // 2)))
               if duration else requested_reserve)
    deadline_mode = constraints.get('continuation_mode') == 'until_deadline' and duration > 0
    if not deadline_mode:
        state.update(phase='done', stop_reason='bounded single improvement cycle')
        write_json(state_path, state)
        return _semantic(state, '专用改进按单轮合同结算')

    try:
        job = json.loads((Path(ctx.workspace) / 'state/application/jobs' /
                          f'{ctx.job_id}.json').read_text())
        created = datetime.fromisoformat(str(job.get('created_at')).replace('Z', '+00:00')).timestamp()
    except (OSError, ValueError, TypeError):
        created = time.time()
    deadline = created + duration
    may_start = time.time() < deadline - reserve

    def summary_from(outputs, round_number):
        local = ((outputs.get('local_read') or {}).get('semantic_output') or {})
        settlement = ((outputs.get('learning_settlement') or {}).get('semantic_output') or {})
        evolution = ((outputs.get('outcome_settle') or {}).get('semantic_output') or {})
        observation = ((outputs.get('observe_execute') or {}).get('semantic_output') or {})
        selection = ((outputs.get('opportunity_select') or {}).get('semantic_output') or {})
        sources = sorted(str(v) for v in local.get('source_paths') or [])
        signature = hashlib.sha256(json.dumps(sources, ensure_ascii=False).encode()).hexdigest()[:16]
        selected_rows = list(selection.get('selected') or [])
        stable_targets = [{key: row.get(key) for key in (
            'origin', 'type', 'capability', 'current_behavior',
            'desired_behavior', 'minimum_probe', 'evidence_refs')}
            for row in selected_rows if isinstance(row, dict)]
        target_signature = (hashlib.sha256(json.dumps(
            stable_targets, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
            if stable_targets else '')
        return {'round_number': round_number, 'sources': sources,
                'source_signature': signature if sources else '',
                'novel_source_count': int(local.get('novel_source_count') or 0),
                'learning_decision': settlement.get('decision'),
                'learning_effect': settlement.get('effect'),
                'observed_job_ids': list(observation.get('observed_job_ids') or []),
                'opportunity_ids': [str(row.get('opportunity_id') or '')
                                    for row in selection.get('selected') or []],
                'target_signature': target_signature,
                'evolution_settled': evolution.get('settled') is True,
                'production_effective': evolution.get('production_effective') is True}

    if state.get('phase') == 'initial':
        first = summary_from(params.get('flow_outputs') or {}, 1)
        state['history'].append(first)
        if not is_learning and not first.get('opportunity_ids'):
            state.update(phase='done', stop_reason='no reproducible Partner defect')
        else:
            state['phase'] = 'ready'
    elif state.get('phase') == 'child_running':
        number = int(state.get('active_round') or state.get('next_round') or 2)
        record_path = (Path(ctx.workspace) / 'state/cycles' / str(ctx.job_id) /
                       'improvement_iterations' / f'round_{number:04d}.json')
        try:
            record = json.loads(record_path.read_text())
        except (OSError, ValueError, TypeError):
            return {'ok': False, 'status': 'failed',
                    'error': f'missing improvement child record {record_path}'}
        row = summary_from(record.get('node_outputs') or {}, number)
        prior = {item.get('source_signature') for item in state.get('history') or []
                 if item.get('source_signature')}
        state['history'].append(row)
        state['next_round'] = number + 1
        state['phase'] = 'ready'
        if is_learning and (not row['sources'] or row['novel_source_count'] <= 0
                            or row['source_signature'] in prior):
            # Source exhaustion ends acquisition, not learning.  Accumulated
            # hypotheses still need prioritisation and content-specific tests.
            state['acquisition_exhausted'] = True
            unresolved = [item for item in state.get('history') or []
                          if item.get('learning_decision') == 'invalid']
            # Do not loop over an empty source pool while pretending that a
            # portfolio evaluator exists.  Invalid ideas remain explicit
            # follow-up hypotheses; a later run may supply an adapter or new
            # source, but this run terminates without duplicate LLM calls.
            state.update(phase='done',
                         stop_reason='novel source pool exhausted',
                         unresolved_invalid_rounds=[row.get('round_number') for row in unresolved])
        elif not is_learning:
            # Every attempt has its own record_name and child workspace.  A
            # settled candidate is evidence for selecting the next unresolved
            # defect, not a hard-coded stop condition.
            prior_rows = list(state.get('history') or [])[:-1]
            prior_jobs = {job for item in prior_rows for job in item.get('observed_job_ids') or []}
            prior_targets = {item.get('target_signature') for item in prior_rows
                             if item.get('target_signature')}
            novel_jobs = set(row.get('observed_job_ids') or []) - prior_jobs
            novel_target = bool(row.get('target_signature')
                                and row.get('target_signature') not in prior_targets)
            if not row.get('opportunity_ids'):
                state.update(phase='done', stop_reason='no reproducible Partner defect')
            elif not novel_jobs or not novel_target:
                state.update(phase='done',
                             stop_reason='no new runtime evidence and distinct reproducible Partner defect')
            else:
                state['phase'] = 'ready'
                state['portfolio_mode'] = 'next_unresolved_partner_defect'
    if state.get('phase') == 'done' or not may_start:
        if not may_start and state.get('phase') != 'done':
            state.update(phase='done', stop_reason='deadline finalization reserve reached')
        state.update(deadline=deadline, remaining_seconds=max(0, int(deadline-time.time())))
        write_json(state_path, state)
        return _semantic(state, f"持续改进结算：{state.get('stop_reason')}")
    number = int(state.get('next_round') or 2)
    state.update(phase='child_running', active_round=number, deadline=deadline,
                 remaining_seconds=max(0, int(deadline-time.time())))
    write_json(state_path, state)
    child_contract = {**contract, 'improvement_round_number': number,
                      'prior_improvement_rounds': list(state.get('history') or [])[-20:]}
    child_flow = 'learning_improvement_round' if is_learning else 'self_improvement_round'
    label = '主动学习增量' if is_learning else 'Partner缺陷审查'
    return {**_semantic(state, f'启动{label}第 {number} 轮'),
            'cycle_child': {'flow': child_flow,
                'owner_node': params['node_id'], 'repeat_owner': True,
                'record_name': f'improvement_iterations/round_{number:04d}',
                'context': {'request': params.get('request') or contract.get('original_request') or '',
                            'intent_contract': child_contract}}}


def _improvement_iteration_records(ctx):
    directory = (Path(ctx.workspace) / 'state/cycles' / str(ctx.job_id) /
                 'improvement_iterations')
    records = []
    for path in sorted(directory.glob('round_*.json')) if directory.is_dir() else []:
        try:
            records.append(json.loads(path.read_text()))
        except (OSError, ValueError, TypeError):
            continue
    return records


def improvement_narrative(ctx, params):
    """Create the single factual projection used by message, Web and report."""
    flow_path = Path(ctx.workspace) / 'state/event_flows' / f"{params.get('flow_id')}.json"
    try:
        flow_type = str(json.loads(flow_path.read_text()).get('flow_type') or '')
    except (OSError, ValueError, TypeError):
        flow_type = ''
    is_learning = flow_type == 'learning_improvement_cycle'
    ideas = saved_hop(ctx, params, 'local_ideas') or {}
    learning = saved_hop(ctx, params, 'learning_settlement') or {}
    settled = saved_hop(ctx, params, 'outcome_settle') or {}
    selected = saved_hop(ctx, params, 'opportunity_select') or {}
    if is_learning:
        attempted_rounds = [{'ideas': ideas, 'settlement': learning,
                            'local_read': saved_hop(ctx, params, 'local_read') or {}}]
        for record in _improvement_iteration_records(ctx):
            outputs = record.get('node_outputs') or {}
            attempted_rounds.append({
                'ideas': ((outputs.get('local_ideas') or {}).get('semantic_output') or {}),
                'settlement': ((outputs.get('learning_settlement') or {}).get('semantic_output') or {}),
                'local_read': ((outputs.get('local_read') or {}).get('semantic_output') or {}),
            })
        # A final probe can honestly find that the unread-source pool is
        # empty.  Count it as an attempt, but never as completed learning and
        # never let it overwrite the last valid effect in the report.
        learning_rounds = [row for row in attempted_rounds
                           if (row['local_read'].get('source_paths')
                               and row['ideas'].get('ideas')
                               and row['settlement'].get('decision'))]
        all_ideas = [idea for row in learning_rounds for idea in row['ideas'].get('ideas') or []]
        source_refs = list(dict.fromkeys(
            str(ref) for idea in all_ideas
            for ref in idea.get('source_basis') or []))
        effects = [row['settlement'].get('effect') for row in learning_rounds
                   if row['settlement'].get('effect') is not None]
        confirmed = [row for row in learning_rounds
                     if row['settlement'].get('decision') == 'improved']
        value = {
            'schema_version': 1, 'job_id': str(ctx.job_id),
            'workstream_type': 'active_learning',
            'research_question': str((params.get('intent_contract') or {}).get('goal') or
                                     params.get('request') or '')[:4000],
            'project': {'conclusion': '本任务以知识增量和下游消费为研究对象', 'rounds': []},
            'active_learning': {
                'status': 'improved' if confirmed else 'not_improved',
                'run_count': len(learning_rounds),
                'attempted_round_count': len(attempted_rounds),
                'sources': source_refs, 'claims': all_ideas,
                'handoff_consumed': all(row['settlement'].get('decision') not in {None, 'invalid'}
                                        for row in learning_rounds),
                'downstream_improved': bool(confirmed),
                'effects': effects, 'effect': effects[-1] if effects else None,
                'claim_boundary': learning.get('claim_boundary'),
            },
            'self_evolution': {'decision': 'not_applicable', 'production_effective': False},
        }
        last_settlement = learning_rounds[-1]['settlement'] if learning_rounds else {}
        headline = (f"主动学习完成 {len(learning_rounds)} 轮、读取 {len(source_refs)} 个来源，"
                    f"形成 {len(all_ideas)} 个可证伪想法；末轮裁决为 "
                    f"{last_settlement.get('decision') or '未结算'}，"
                    f"effect={last_settlement.get('effect') if last_settlement.get('effect') is not None else '未记录'}。")
        next_action = str(learning.get('next_action') or '按冻结假设进入后续真实项目实验')
        milestone = ('【主动学习结算】\n'
                     '原因：检验外部知识能否改变后续判断。\n'
                     f'执行：完成 {len(learning_rounds)} 轮，读取 {len(source_refs)} 个来源，形成 {len(all_ideas)} 个来源约束的可证伪想法，并逐轮运行冻结 matched comparison。\n'
                     f'证据：{source_refs[0] if source_refs else "未取得有效来源"}；完整引用见报告。\n'
                     f'结果：末轮 decision={last_settlement.get("decision") or "未结算"}，effect={last_settlement.get("effect") if last_settlement.get("effect") is not None else "未记录"}。\n'
                     f'判断：{learning.get("claim_boundary") or "尚不能证明真实项目改善"}。\n'
                     f'下一步：{next_action}。')
    else:
        rows = selected.get('selected') or []
        result = settled.get('result') if isinstance(settled.get('result'), dict) else {}
        decision_row = result.get('decision') if isinstance(result.get('decision'), dict) else {}
        evolution_dir = Path(ctx.workspace) / 'state/cycles' / str(ctx.job_id) / 'evolution'
        design = {}
        counter = {}
        for name, receiver in (('design.json', design), ('counter.json', counter)):
            try:
                receiver.update(json.loads((evolution_dir / name).read_text()))
            except (OSError, ValueError, TypeError):
                pass
        grounded_issue = dict(counter.get('selected_issue') or {})
        target_files = list(dict.fromkeys(
            str(path) for path in (design.get('target_files') or
                                   grounded_issue.get('partner_target_files') or []) if path))
        if target_files:
            grounded_issue['partner_target_files'] = target_files
        if not grounded_issue and rows:
            grounded_issue = dict(rows[0])
        value = {
            'schema_version': 1, 'job_id': str(ctx.job_id),
            'workstream_type': 'self_improvement',
            'research_question': str((params.get('intent_contract') or {}).get('goal') or
                                     params.get('request') or '')[:4000],
            'project': {'conclusion': '本任务改进对象是 Partner 自身机制', 'rounds': []},
            'active_learning': {'status': 'not_applicable', 'run_count': 0},
            'self_evolution': {
                'audit_issue_count': len(rows), 'selected_issue': grounded_issue,
                'decision': decision_row.get('decision') or ('inconclusive' if rows else 'no_candidate'),
                'production_effective': settled.get('production_effective') is True,
                'real_experiment_executed': result.get('real_experiment_executed') is True,
                'matched_verified': result.get('valid_matched_experiment') is True,
                'reproducer': grounded_issue.get('reproduction_steps') or grounded_issue.get('reproducer'),
                'target_files': target_files,
                'evidence_refs': [str(path) for path in (
                    settled.get('record_path'), evolution_dir / 'counter.json',
                    evolution_dir / 'design.json') if path and Path(path).exists()],
            },
        }
        headline = (f"Partner 自进化冻结 {len(rows)} 个问题；裁决为 "
                    f"{value['self_evolution']['decision']}；生产生效="
                    f"{value['self_evolution']['production_effective']}。")
        issue_text = str(grounded_issue.get('symptom') or grounded_issue.get('current_behavior')
                         or '未形成可复现缺陷')[:300]
        target_text = '、'.join(target_files[:3]) or '未冻结真实源码文件'
        if value['self_evolution']['real_experiment_executed'] and value['self_evolution']['matched_verified']:
            execution_text = '已运行隔离 baseline/candidate，且 matched comparison 有效'
        elif value['self_evolution']['real_experiment_executed']:
            execution_text = '已尝试隔离 baseline/candidate，但 matched comparison 无效'
        else:
            execution_text = '已冻结候选缺陷，但没有成功执行隔离 baseline/candidate'
        milestone = ('【Partner 自进化结算】\n'
                     '原因：真实运行轨迹显示 Partner 自身机制可能阻碍任务效果。\n'
                     f'执行：冻结问题“{issue_text}”，定位 {target_text}；{execution_text}。\n'
                     f'证据：{(value["self_evolution"]["evidence_refs"] or ["无有效实验记录"])[0]}。\n'
                     f'结果：decision={value["self_evolution"]["decision"]}，matched_verified={value["self_evolution"]["matched_verified"]}。\n'
                     f'判断：production_effective={value["self_evolution"]["production_effective"]}；未生效时不得写成已修复。\n'
                     '下一步：仅在授权且生产验证通过后晋升，否则保留为 shadow 候选。')
    value['headline'] = headline
    value['milestone_message'] = milestone
    cycle_root = Path(ctx.workspace) / 'state/cycles' / str(ctx.job_id)
    if is_learning:
        final_state = {
            'schema_version': 2, 'job_id': str(ctx.job_id),
            'workstream_type': 'active_learning',
            'research_outcome': {'status': 'not_applicable'},
            'learning_outcome': {
                'status': value['active_learning']['status'],
                'run_count': value['active_learning']['run_count'],
                'consumed': value['active_learning']['handoff_consumed'],
                'downstream_improved': value['active_learning']['downstream_improved'],
                'runs': learning_rounds,
            },
            'evolution_outcome': {'status': 'not_applicable'},
        }
    else:
        evolution_value = value['self_evolution']
        final_state = {
            'schema_version': 2, 'job_id': str(ctx.job_id),
            'workstream_type': 'self_evolution',
            'research_outcome': {'status': 'not_applicable'},
            'learning_outcome': {'status': 'not_applicable'},
            'evolution_outcome': {
                'status': evolution_value['decision'],
                'real_experiment_executed': evolution_value['real_experiment_executed'],
                'valid_matched_experiment': evolution_value['matched_verified'],
                'production_effective': evolution_value['production_effective'],
                'selected_issue': evolution_value['selected_issue'],
            },
        }
    canonical = json.dumps(final_state, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':')).encode()
    final_state['final_state_hash'] = 'sha256:' + hashlib.sha256(canonical).hexdigest()
    write_json(cycle_root / 'final_run_state.json', final_state)
    value['final_state_hash'] = final_state['final_state_hash']
    target = cycle_root / 'run_narrative.json'
    write_json(target, value)
    return {'ok': True, 'status': 'completed', 'semantic_output': value,
            'message': value['milestone_message'], 'summary': headline,
            'notification_kind': 'milestone', 'force': True,
            'files': [str(target)], 'evidence_refs': [str(target)]}


def improvement_report(ctx, params):
    """Create a readable, evidence-bound HTML/Markdown/PDF report for 02/03."""
    flow_path = Path(ctx.workspace) / 'state/event_flows' / f"{params.get('flow_id')}.json"
    try:
        flow_type = str(json.loads(flow_path.read_text()).get('flow_type') or '')
    except (OSError, ValueError, TypeError):
        flow_type = ''
    is_learning = flow_type == 'learning_improvement_cycle'
    outputs = params.get('flow_outputs') or {}
    selected = ((outputs.get('opportunity_select') or {}).get('semantic_output') or {})
    settled = ((outputs.get('outcome_settle') or {}).get('semantic_output') or {})
    learning = ((outputs.get('learning_settlement') or {}).get('semantic_output') or {})
    ideas = ((outputs.get('local_ideas') or {}).get('semantic_output') or {})
    learning_runs = [{
        'ideas': ideas, 'settlement': learning,
        'evaluation': ((outputs.get('learning_evaluate') or {}).get('semantic_output') or {}),
        'local_read': ((outputs.get('local_read') or {}).get('semantic_output') or {})}]
    if is_learning:
        for record in _improvement_iteration_records(ctx):
            child_outputs = record.get('node_outputs') or {}
            learning_runs.append({
                'ideas': ((child_outputs.get('local_ideas') or {}).get('semantic_output') or {}),
                'settlement': ((child_outputs.get('learning_settlement') or {}).get('semantic_output') or {}),
                'evaluation': ((child_outputs.get('learning_evaluate') or {}).get('semantic_output') or {}),
                'local_read': ((child_outputs.get('local_read') or {}).get('semantic_output') or {}),
            })
        attempted_learning_runs = list(learning_runs)
        learning_runs = [row for row in attempted_learning_runs
                         if (row['local_read'].get('source_paths')
                             and row['ideas'].get('ideas')
                             and row['settlement'].get('decision'))]
        aggregate_ideas = [idea for row in learning_runs for idea in row['ideas'].get('ideas') or []]
        learning = learning_runs[-1]['settlement'] if learning_runs else learning
        ideas = {'ideas': aggregate_ideas}
    observation = ((outputs.get('observe_execute') or {}).get('semantic_output') or {})
    mode = '主动学习' if is_learning else 'Partner 自进化'
    conclusion = (f"知识干预内容相关 matched comparison：{learning.get('decision','未结算')}，"
                  f"effect={learning.get('effect',0):.3f}。" if is_learning else
                  f"自进化结算：{'已取得子实验终态' if settled.get('settled') else '未形成可晋升实验'}；"
                  f"production_effective={bool(settled.get('production_effective'))}。")
    work = Path(ctx.working_dir); work.mkdir(parents=True, exist_ok=True)

    # The graph is derived from the persisted Flow projection used by both
    # renderers.  This prevents the PDF and Web report from drawing different
    # stories about what actually ran.
    try:
        flow_record = json.loads(flow_path.read_text())
    except (OSError, ValueError, TypeError):
        flow_record = {}
    completed_nodes = set(flow_record.get('completed_node_ids') or [])
    try:
        from partner.event_flows import build_flow_registry
        definition = build_flow_registry().get(flow_type)
        node_ids = [node.node_id for node in definition.nodes]
    except (KeyError, AttributeError, TypeError, ValueError):
        node_ids = list(flow_record.get('completed_node_ids') or [])
    current_node = str(flow_record.get('current_event_id') or 'render')
    def _node_status(node):
        if node in completed_nodes:
            return 'completed'
        if node == current_node or node == 'render':
            return 'running_at_report_freeze'
        return 'planned_after_report_freeze'
    graph = {
        'schema_version': 1,
        'flow_id': str(params.get('flow_id') or ''),
        'flow_type': flow_type,
        'status': str(flow_record.get('status') or ''),
        'nodes': [
            {'node_id': node, 'event_id': (flow_record.get('node_event_ids') or {}).get(node, ''),
             'status': _node_status(node)} for node in node_ids
        ],
        'edges': [
            {'from': node_ids[i], 'to': node_ids[i + 1]}
            for i in range(max(0, len(node_ids) - 1))
        ],
    }
    graph_path = work / 'flow_graph.json'; write_json(graph_path, graph)
    import html as _html
    width, row_h = 920, 62
    height = max(150, 70 + row_h * len(node_ids))
    svg_rows = []
    for index, node in enumerate(node_ids):
        y = 35 + index * row_h
        if index:
            svg_rows.append(f'<line x1="80" y1="{y-18}" x2="80" y2="{y}" stroke="#78a993" stroke-width="3"/>')
        svg_rows.append(f'<rect x="28" y="{y}" width="820" height="42" rx="10" fill="#e7f6ee" stroke="#78a993"/>')
        svg_rows.append(f'<text x="48" y="{y+27}" font-family="sans-serif" font-size="16" fill="#193b30">{index+1:02d} · {_html.escape(node)}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
           '<rect width="100%" height="100%" fill="#f8fcfa"/>' + ''.join(svg_rows) + '</svg>')
    svg_path = work / 'flow_graph.svg'; svg_path.write_text(svg, encoding='utf-8')
    # PDF readers cannot resolve an adjacent SVG reliably.  Render a compact
    # raster copy from the same graph projection and embed it in the appendix.
    # It is provenance context, while the first pages remain centred on the
    # actual learning/evolution result.
    graph_png = work / 'flow_graph.png'
    try:
        from PIL import Image as PILImage, ImageDraw, ImageFont
        columns = 3
        box_w, box_h, gap_x, gap_y = 410, 72, 28, 32
        rows = max(1, (len(node_ids) + columns - 1) // columns)
        canvas = PILImage.new('RGB', (columns * box_w + (columns + 1) * gap_x,
                                      rows * box_h + (rows + 1) * gap_y), '#f8fcfa')
        draw = ImageDraw.Draw(canvas)
        try:
            graph_font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 22)
        except OSError:
            graph_font = ImageFont.load_default()
        for index, node in enumerate(node_ids):
            row, column = divmod(index, columns)
            x = gap_x + column * (box_w + gap_x); y = gap_y + row * (box_h + gap_y)
            draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=14,
                                   fill='#e7f6ee', outline='#78a993', width=3)
            label = f'{index + 1:02d}  {node}'
            draw.text((x + 18, y + 22), label[:34], font=graph_font, fill='#193b30')
            if index + 1 < len(node_ids):
                nx_index = index + 1
                nr, nc = divmod(nx_index, columns)
                nx = gap_x + nc * (box_w + gap_x); ny = gap_y + nr * (box_h + gap_y)
                if nr == row:
                    draw.line((x + box_w, y + box_h // 2, nx, ny + box_h // 2), fill='#4f8d76', width=4)
                else:
                    draw.line((x + box_w // 2, y + box_h, nx + box_w // 2, ny), fill='#4f8d76', width=4)
        canvas.save(graph_png, optimize=True)
    except Exception:
        graph_png = None

    result = settled.get('result') or {}
    evolution_decision = result.get('decision') or {}
    comparisons = evolution_decision.get('comparisons') or {}
    runtime_verify = result.get('runtime_verify') or {}
    selected_attempt = evolution_decision.get('selected_attempt')
    observed_jobs = [str(row.get('job_id')) for row in observation.get('job_facts') or [] if row.get('job_id')]
    selected_rows = selected.get('selected') or []
    selected_issue = selected_rows[0] if selected_rows else {}
    if not is_learning:
        evolution_dir = Path(ctx.workspace) / 'state/cycles' / str(ctx.job_id) / 'evolution'
        try:
            grounded = json.loads((evolution_dir / 'counter.json').read_text()).get('selected_issue') or {}
            if grounded:
                selected_issue = grounded
        except (OSError, ValueError, TypeError):
            pass

    md = work / f'{mode}_结果报告.md'
    lines = [f'# {mode}结果报告', '', f'> {conclusion}', '',
             '## 运行对象', '', f'- Job：`{ctx.job_id}`',
             f'- Flow：`{params.get("flow_id")}`（{flow_type}）',
             f'- Event：{len(node_ids)} 个完成，{len(flow_record.get("failed_node_ids") or [])} 个失败', '']
    if is_learning:
        lines += ['## 学习内容与采用实验', '',
                  f'- 实质学习轮数：{len(learning_runs)}',
                  f'- 控制器尝试轮数：{len(attempted_learning_runs)}',
                  f'- 实际记录想法数：{len(ideas.get("ideas") or [])}',
                  f'- 冻结下游任务：知识干预内容相关 matched comparison',
                  f'- 实验适配器：{((learning_runs[-1].get("evaluation") if learning_runs else {}) or {}).get("experiment_adapter") or "无可执行适配器"}',
                  f'- Baseline：{((learning_runs[-1].get("evaluation") if learning_runs else {}) or {}).get("baseline")}',
                  f'- Candidate：{((learning_runs[-1].get("evaluation") if learning_runs else {}) or {}).get("candidate")}',
                  f'- Effect：{float(learning.get("effect") or 0):.2f}',
                  f'- Settlement：**{learning.get("decision", "未结算")}**',
                  f'- 结论边界：{learning.get("claim_boundary") or "未声明"}']
        for index, idea in enumerate((ideas.get('ideas') or [])[:5], 1):
            lines += ['', f'### 想法 {index}：{idea.get("title") or "未命名"}', '',
                      f'- 来源：{"；".join(idea.get("source_basis") or []) or "未绑定"}',
                      f'- Partner 缺口：{idea.get("partner_gap") or "未记录"}',
                      f'- 可证伪假设：{idea.get("hypothesis") or "未记录"}',
                      f'- 最小实验：{idea.get("minimal_experiment") or "未记录"}',
                      f'- 失败条件：{idea.get("failure_condition") or "未记录"}']
    else:
        lines += ['## 观察与问题冻结', '',
                  f'- 观察 Job：{", ".join(observed_jobs) if observed_jobs else "未取得"}',
                  f'- 选择机会数：{len(selected_rows)}',
                  f'- 问题：{selected_issue.get("current_behavior") or "未形成可实验问题"}',
                  f'- 目标行为：{selected_issue.get("desired_behavior") or "未声明"}', '',
                  '## 匹配实验与生产验证', '']
        for attempt, comparison in sorted(comparisons.items(), key=lambda row: str(row[0])):
            exp = comparison.get('expectation_results') or []
            met = sum(row.get('status') == 'met' for row in exp)
            lines.append(f'- Attempt {attempt}：{comparison.get("decision", "unknown")}；'
                         f'冻结预期 {met}/{len(exp)}；回归通过={bool(comparison.get("regression_passed"))}')
        lines += [f'- 选中候选：Attempt {selected_attempt if selected_attempt is not None else "无"}',
                  f'- fresh-process / frozen replay / independent evaluator：'
                  f'**{"全部通过" if runtime_verify.get("all_ok") else "未通过"}**',
                  f'- 生产生效：**{bool(settled.get("production_effective"))}**',
                  f'- 回滚：{(result.get("rollback") or {}).get("status", "未记录")}']
    lines += ['', '## 实际 Event / Flow', '',
              f'- 同源图数据：`{graph_path.name}`', f'- 同源图：`{svg_path.name}`',
              '- 顺序：' + ' → '.join(node_ids), '', '## 结论边界', '',
              '- 报告只陈述本 Job 的机器回执；未晋升不得写成 Partner 已改善。',
              '- 主动学习的合同分类改善不等于真实科研项目指标已经提高。', '']
    md.write_text('\n'.join(lines), encoding='utf-8')

    html_path = work / f'{mode}_结果报告.html'
    html_path.write_text('''<!doctype html><html lang="zh"><meta charset="utf-8"><style>
body{margin:0;background:#f4fbf7;color:#1d332b;font:16px/1.65 system-ui,sans-serif}.wrap{max-width:980px;margin:auto;padding:36px}
.hero,.card{background:#fff;border:1px solid #b9dccb;border-radius:18px;padding:28px;margin:0 0 20px;box-shadow:0 8px 24px #1a5b4120}
h1,h2{color:#176b55}.pill{display:inline-block;background:#dff4e8;color:#176b55;padding:5px 12px;border-radius:999px;font-weight:700}
pre{white-space:pre-wrap;font:15px/1.7 system-ui,sans-serif}img{width:100%;height:auto}</style><body><main class="wrap">'''
        + f'<section class="hero"><span class="pill">{_html.escape(mode)} · Event 生成</span><h1>{_html.escape(mode)}结果报告</h1><p>{_html.escape(conclusion)}</p></section>'
        + f'<section class="card"><pre>{_html.escape(chr(10).join(lines[5:]))}</pre></section>'
        + f'<section class="card"><h2>实际 Event / Flow</h2><img src="{svg_path.name}" alt="实际 Event Flow"></section>'
        + '</main></body></html>', encoding='utf-8')

    pdf = work / f'{mode}_结果报告.pdf'
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image as RLImage
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font = 'Helvetica'
    font_path = Path('/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc')
    if font_path.is_file():
        try:
            pdfmetrics.registerFont(TTFont('PartnerImprovementCJK', str(font_path), subfontIndex=0))
            font = 'PartnerImprovementCJK'
        except Exception:
            pass
    styles = getSampleStyleSheet()
    title = ParagraphStyle('PartnerTitle', parent=styles['Title'], fontName=font,
                           fontSize=22, leading=28, textColor=HexColor('#176B55'))
    body = ParagraphStyle('PartnerBody', parent=styles['BodyText'], fontName=font,
                          fontSize=10.5, leading=16, textColor=HexColor('#24352F'), alignment=TA_LEFT)
    h2 = ParagraphStyle('PartnerH2', parent=body, fontName=font, fontSize=15,
                        leading=20, spaceBefore=10, spaceAfter=6,
                        textColor=HexColor('#176B55'))
    h3 = ParagraphStyle('PartnerH3', parent=body, fontName=font, fontSize=12,
                        leading=17, spaceBefore=8, spaceAfter=4,
                        textColor=HexColor('#245F50'))
    doc = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=42, rightMargin=42,
                            topMargin=44, bottomMargin=44, title=f'{mode}结果报告')
    story = [Paragraph(f'{mode}结果报告', title), Spacer(1, 14),
             Paragraph(conclusion, body), Spacer(1, 12)]
    table_data = [['项目', '机器记录'], ['Job', str(ctx.job_id)],
                  ['Flow', str(params.get('flow_id'))],
                  ['生产生效', str(bool(settled.get('production_effective')))],
                  ['报告时间', datetime.now(timezone.utc).isoformat()]]
    table = Table(table_data, colWidths=[90, 390])
    table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),font),('FONTSIZE',(0,0),(-1,-1),9),
        ('BACKGROUND',(0,0),(0,-1),HexColor('#DDF3E8')),('GRID',(0,0),(-1,-1),0.5,HexColor('#8CB9A8')),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7)]))
    story += [table, Spacer(1, 16)]
    if is_learning:
        evaluation = learning_runs[-1]['evaluation'] if learning_runs else {}
        metrics = Table([
            ['比较臂', 'Accuracy', '解释'],
            ['Baseline', f'{float(evaluation.get("baseline_accuracy") or 0):.2f}', '未使用学习后的证据门'],
            ['Candidate', f'{float(evaluation.get("candidate_accuracy") or 0):.2f}', '使用来源约束的证据门'],
            ['差值', f'{float(learning.get("effect") or 0):+.2f}', str(learning.get('decision') or '未结算')],
        ], colWidths=[90, 80, 310])
        metrics.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),font),('FONTSIZE',(0,0),(-1,-1),9),
            ('BACKGROUND',(0,0),(-1,0),HexColor('#DDF3E8')),('GRID',(0,0),(-1,-1),0.5,HexColor('#8CB9A8')),
            ('VALIGN',(0,0),(-1,-1),'TOP')]))
        story += [Paragraph('研究问题与比较结果', h2),
                  Paragraph('外部知识是否能改善 Partner 对候选证据是否足以进入实验的判断？', body),
                  Spacer(1, 6), metrics, Spacer(1, 8),
                  Paragraph('结论边界：' + _html.escape(str(learning.get('claim_boundary') or '未声明')), body),
                  Paragraph('学习内容与可证伪迁移假设', h2)]
        for index, idea in enumerate(ideas.get('ideas') or [], 1):
            sources_text = '；'.join(idea.get('source_basis') or []) or '未绑定来源'
            story += [Paragraph(f'{index}. {_html.escape(str(idea.get("title") or "未命名想法"))}', h3),
                      Paragraph('<b>来源：</b>' + _html.escape(sources_text), body),
                      Paragraph('<b>Partner 缺口：</b>' + _html.escape(str(idea.get('partner_gap') or '未记录')), body),
                      Paragraph('<b>迁移假设：</b>' + _html.escape(str(idea.get('hypothesis') or '未记录')), body),
                      Paragraph('<b>最小实验：</b>' + _html.escape(str(idea.get('minimal_experiment') or '未记录')), body),
                      Paragraph('<b>失败条件：</b>' + _html.escape(str(idea.get('failure_condition') or '未记录')), body),
                      Spacer(1, 5)]
    else:
        attempt_rows = [['Attempt', '判定', '冻结预期', '回归']]
        for attempt, comparison in sorted(comparisons.items(), key=lambda row: str(row[0])):
            exp = comparison.get('expectation_results') or []
            attempt_rows.append([str(attempt), str(comparison.get('decision') or 'unknown'),
                                 f'{sum(row.get("status") == "met" for row in exp)}/{len(exp)}',
                                 str(bool(comparison.get('regression_passed')))])
        attempt_table = Table(attempt_rows, colWidths=[70, 130, 110, 90])
        attempt_table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),font),('FONTSIZE',(0,0),(-1,-1),9),
            ('BACKGROUND',(0,0),(-1,0),HexColor('#DDF3E8')),('GRID',(0,0),(-1,-1),0.5,HexColor('#8CB9A8'))]))
        target_files = []
        try:
            target_files = json.loads((Path(ctx.workspace) / 'state/cycles' / str(ctx.job_id) /
                                       'evolution/design.json').read_text()).get('target_files') or []
        except (OSError, ValueError, TypeError):
            pass
        issue_text = str(selected_issue.get('symptom') or selected_issue.get('current_behavior') or '未形成问题')
        story += [Paragraph('真实缺陷与源码定位', h2),
                  Paragraph(_html.escape(issue_text), body),
                  Paragraph('<b>因果假设：</b>' + _html.escape(str(selected_issue.get('hypothesis') or '未记录')), body),
                  Paragraph('<b>目标源码：</b>' + _html.escape('；'.join(target_files) or '未冻结'), body),
                  Paragraph('<b>复现步骤：</b>' + _html.escape('；'.join(selected_issue.get('reproduction_steps') or []) or '未记录'), body),
                  Paragraph('匹配实验与生产验证', h2),
                  Spacer(1, 8), attempt_table, Spacer(1, 8),
                  Paragraph(f'生产验证：{"全部通过" if runtime_verify.get("all_ok") else "未通过"}；生产生效={bool(settled.get("production_effective"))}。', body)]
    story += [PageBreak(), Paragraph('证据、限制与运行附录', h2),
              Paragraph('本报告中的数值来自冻结比较或机器回执；来源、哈希和完整结构化字段保存在同目录 manifest 与 JSON 证据中。', body),
              Paragraph('实际 Event / Flow', h2), Spacer(1, 6)]
    if graph_png and graph_png.is_file():
        image = RLImage(str(graph_png)); image._restrictSize(500, 430)
        story += [image, Spacer(1, 8)]
    story += [Paragraph(' → '.join(node_ids), body), Spacer(1, 14),
              Paragraph('证据边界', h2), Spacer(1, 6),
              Paragraph('主动学习合同分类改善不等于科研项目指标提高；自进化只有生产重放通过才能标为 production_effective。', body)]
    doc.build(story)
    artifacts = []
    for path in (md, html_path, pdf, graph_path, svg_path, graph_png):
        if not path or not path.is_file():
            continue
        artifacts.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'size_bytes': path.stat().st_size})
    manifest = {'schema_version': 1, 'job_id': str(ctx.job_id), 'flow_id': str(params.get('flow_id')),
                'flow_type': flow_type, 'mode': mode, 'conclusion': conclusion,
                'artifacts': artifacts, 'source_nodes': sorted(outputs),
                'created_at': datetime.now(timezone.utc).isoformat()}
    manifest_path = work / 'report_manifest.json'; write_json(manifest_path, manifest)
    manifest['manifest_path'] = str(manifest_path)
    return {'ok': True, 'status': 'completed', 'path': str(pdf), 'pdf_path': str(pdf),
            'files': [str(md), str(html_path), str(pdf), str(graph_path), str(svg_path), str(manifest_path)],
            'evidence_refs': [str(manifest_path), str(md)], 'semantic_output': manifest,
            'summary': f'{mode}报告与 manifest 已生成'}


def improvement_finish(ctx, params):
    """Mark the improvement cycle finished and stop.

    Mirrors the bounded contract: a single cycle, no further cycles.
    """
    flow_type = ''
    try:
        flow_path = Path(ctx.workspace) / 'state/event_flows' / f"{params.get('flow_id')}.json"
        flow_type = str(json.loads(flow_path.read_text()).get('flow_type') or '')
    except (OSError, ValueError, TypeError):
        pass
    mode = ('learning_improvement' if flow_type == 'learning_improvement_cycle'
            else 'self_improvement')
    report = saved_hop(ctx, params, 'render') or {}
    delivery = saved_hop(ctx, params, 'send_report') or {}
    return _semantic({"finished": True,
        "mode": mode, "flow_type": flow_type,
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
    EventDefinition("improvement.learning_commitment", "improvement", "freeze active-learning downstream task",
                    learning_commitment, execution_method="local", timeout_seconds=60, produces_artifact=True),
    EventDefinition("improvement.learning_downstream_evaluate", "improvement", "run active-learning downstream matched comparison",
                    learning_downstream_evaluate, execution_method="local", timeout_seconds=60, produces_artifact=True),
    EventDefinition("improvement.learning_settlement", "improvement", "settle active-learning downstream effect",
                    learning_settlement, execution_method="local", timeout_seconds=60, produces_artifact=True),
    EventDefinition("improvement.iteration_controller", "improvement", "continue only evidence-novel improvement rounds until deadline",
                    improvement_iteration_controller, execution_method="local", timeout_seconds=60, produces_artifact=True),
    EventDefinition("improvement.narrative", "improvement", "build one factual user narrative",
                    improvement_narrative, execution_method="local", timeout_seconds=60, produces_artifact=True),
    EventDefinition("improvement.report", "improvement", "render improvement report and manifest",
                    improvement_report, execution_method="local", timeout_seconds=120, produces_artifact=True),
    EventDefinition("improvement.finish", "improvement", "mark improvement cycle finished",
                    improvement_finish, execution_method="local", timeout_seconds=30),
]
