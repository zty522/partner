"""Event Fabric binding for the commitment kernel.

Division of responsibility, deliberately strict:

* **Event Fabric** owns scheduling, retry, recovery and delivery.  It decides
  *when* something runs.
* **The commitment kernel** owns semantics: what a bet means, when it may be
  measured, what a result settles to.  It decides *what is true*.

So the Events here are thin.  They load a declarative bet spec, hand it to the
kernel runner, and project the resulting terminal state back into the Event
summary.  They never write a lifecycle state themselves -- the state machine is
the only writer -- and they never seed a follow-up Event: a bet that reaches a
terminal state ends, and starting another one is a separate, explicit decision.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Mapping

from partner.event_fabric.catalog import EventDefinition

from partner.commitment import state_machine as sm
from partner.commitment.state_machine import is_terminal
from partner.application.commitment_adapter import build_runner

#: Kernel lifecycle states projected onto Event names.  Used by callers (and by
#: a future readiness gate) to decide which Event is meaningful next; it is a
#: mapping, not a scheduler.
KERNEL_STATE_EVENTS: Mapping[str, str] = {
    sm.DRAFT: "commitment.bet_open",
    sm.PROPOSED: "commitment.bet_commit",
    sm.COMMITTED: "commitment.bet_execute",
    sm.EXECUTING: "commitment.bet_execute",
    sm.MEASURED: "commitment.bet_settle",
    sm.SETTLED: "commitment.bet_settle",
    sm.CLOSED: "commitment.bet_state",
    sm.INVALID: "commitment.bet_state",
    sm.BLOCKED: "commitment.bet_state",
    sm.BUDGET_EXHAUSTED: "commitment.bet_state",
    sm.CANCELLED: "commitment.bet_state",
}


def map_kernel_state(state: str) -> str:
    """The Event name that would advance a bet in ``state``."""
    return KERNEL_STATE_EVENTS.get(state, "commitment.bet_state")


def _load_spec(params: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(params.get("bet_spec"), dict):
        return dict(params["bet_spec"])
    path = params.get("bet_spec_path")
    if not path:
        raise ValueError("commitment Event requires params.bet_spec or params.bet_spec_path")
    return json.loads(Path(str(path)).read_text(encoding="utf-8"))


def _project(ctx, bet_id: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    from partner.commitment.store import CommitmentStore
    store = CommitmentStore(Path(ctx.workspace), str(spec["run_id"]), bet_id)
    lifecycle = store.load_lifecycle()
    return {"state": lifecycle.state, "revision": lifecycle.revision,
            "settled": lifecycle.settled, "settled_class": lifecycle.settled_class,
            "experience_emitted": lifecycle.experience_emitted,
            "chain": store.verify_chain().to_dict(),
            "next_event": map_kernel_state(lifecycle.state)}


def commitment_bet_run(ctx, params):
    """Run one bounded bet to a terminal state (idempotent on replay)."""
    spec = _load_spec(params)
    bet_id = str(spec["bet_id"])
    runner = build_runner(Path(ctx.workspace), spec, use_llm=bool(params.get("use_llm")),
                          llm_max_calls=int(params.get("llm_max_calls") or 2))
    result = runner.run()
    projection = _project(ctx, bet_id, spec)
    settlement = result.settlement
    return {
        "ok": sm.is_terminal(result.state),
        "status": result.state,
        "summary": f"commitment bet {bet_id} -> {result.state}: {result.reason}",
        "semantic_output": {
            "bet_id": bet_id,
            "state": result.state,
            "settlement_class": None if settlement is None else settlement.settlement_class,
            "improvement_observed": None if settlement is None else settlement.improvement_observed,
            "publish_eligible": None if settlement is None else settlement.publish_eligible,
            "replayed": result.replayed,
            "artifacts": dict(result.paths),
            "projection": projection,
        },
        "files": [path for path in result.paths.values() if isinstance(path, str)],
        "evidence_refs": [] if settlement is None else [settlement.settlement_id],
        "token_usage": dict(runner.proposer.transcript[-1].get("usage") or {})
                        if getattr(runner.proposer, "transcript", None) else {},
    }


def commitment_bet_state(ctx, params):
    """Read-only projection of one bet's lifecycle.  Writes nothing."""
    spec = _load_spec(params)
    projection = _project(ctx, str(spec["bet_id"]), spec)
    return {
        "ok": projection["chain"]["ok"],
        "status": projection["state"],
        "summary": f"commitment bet {spec['bet_id']} is {projection['state']}",
        "semantic_output": projection, "files": [], "evidence_refs": [], "token_usage": {},
    }


def commitment_bet_settlement(ctx, params):
    """Read-only projection of a bet's settlement and experience."""
    from partner.commitment.store import CommitmentStore
    spec = _load_spec(params)
    store = CommitmentStore(Path(ctx.workspace), str(spec["run_id"]), str(spec["bet_id"]))
    lifecycle = store.load_lifecycle()
    settlement = store.load_settlement(lifecycle.settlement_id) if lifecycle.settlement_id else None
    experiences = [store.load_experience(identifier)
                   for identifier in store.list_artifacts("experience")]
    return {
        "ok": True,
        "status": lifecycle.state,
        "summary": (f"bet {spec['bet_id']} settled as "
                    f"{None if settlement is None else settlement.settlement_class}"),
        "semantic_output": {
            "settlement": None if settlement is None else settlement.to_dict(),
            "experiences": [e.to_dict() for e in experiences],
        },
        "files": [], "evidence_refs": [], "token_usage": {},
    }


#: A trace token is how an operator follows one message through the kernel.
# A trace token is whatever the operator wrote; do not dictate the prefix.  Match any
# single identifier-shaped word containing "trace" (runtime_trace_02_x, log_trace_02_x,
# trace_token=abc, canary_trace...), and only that word.
_TRACE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9_-]+[Tt]race[A-Za-z0-9_-]*"
    r"|[Tt]race[_-][A-Za-z0-9_-]+)")


def _job_request(ctx) -> str:
    try:
        from partner.index.job_repository import init as _init_jobs
        record = _init_jobs(Path(ctx.workspace)).get_record(str(getattr(ctx, "job_id", ""))) or {}
        return str(record.get("request") or "")
    except Exception:  # noqa: BLE001 - a missing request is reported, never invented
        return ""


def _trace_token(ctx, params: Mapping[str, Any]) -> str:
    direct = str(params.get("trace_token") or "").strip()
    if direct:
        return direct
    for candidate in (params.get("message"), params.get("goal"), _job_request(ctx)):
        found = _TRACE_RE.search(str(candidate or ""))
        if found:
            return found.group(0)
    return ""


#: A message may declare a real repository task instead of the built-in metric action:
#:   real_task:<task_id>   patch:<file>   (patch defaults to patch.diff)
_REAL_TASK_RE = re.compile(r"real_task[:=\s]+([A-Za-z0-9_\-]+)")
_PATCH_RE = re.compile(r"patch[:=\s]+([A-Za-z0-9_.\-]+)")


def _declared_real_task(message: str, params: Mapping[str, Any]) -> dict[str, str]:
    task_id = str(params.get("task_id") or "").strip()
    if not task_id:
        found = _REAL_TASK_RE.search(message or "")
        task_id = found.group(1) if found else ""
    if not task_id:
        return {}
    patch_file = str(params.get("patch_file") or "").strip()
    if not patch_file:
        found = _PATCH_RE.search(message or "")
        patch_file = found.group(1) if found else "patch.diff"
    return {"task_id": task_id, "patch_file": patch_file}


#: The counterfactual switch for the recall Event: ``prior=off`` in the message (or a
#: ``prior_disabled`` parameter) makes the recall node write no prior at all, so the
#: record Event falls back to the declared values.  Declared, not inferred.
_PRIOR_OFF_RE = re.compile(r"prior\s*[:=]\s*(off|disabled|empty|none)", re.IGNORECASE)


def _prior_disabled(message: str, params: Mapping[str, Any]) -> bool:
    if bool(params.get("prior_disabled")):
        return True
    return bool(_PRIOR_OFF_RE.search(str(message or "")))


def _class_key_for_spec(spec: Mapping[str, Any]) -> str:
    """The task class of a declared spec.  Pure: three declared strings, no LLM."""
    from partner.application.experience_prior import metric_signature, task_class_key
    return task_class_key(project_id=str(spec.get("project_id") or ""),
                          action_id=str(spec.get("action_id") or ""),
                          metric_signature=metric_signature(spec.get("expected_effects") or ()))


def _declared_values(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The decision variables as DECLARED, before any prior is applied."""
    effects = list(spec.get("expected_effects") or [])
    primary = next((e for e in effects if str(e.get("kind")) == "delta_over_baseline"),
                   effects[0] if effects else {})
    protocol = dict(spec.get("evaluation_protocol") or {})
    return {"min_delta": float(primary.get("min_delta") or 0.0),
            "replicates": int(protocol.get("replicates") or 1),
            "primary_metric": str(primary.get("metric") or ""),
            "require_baseline_rerun": True}


def _apply_prior_to_spec(spec: Mapping[str, Any], *, class_key: str, prior: Mapping[str, Any],
                         audit: Mapping[str, Any], adjusted: Mapping[str, Any],
                         action: str) -> dict[str, Any]:
    """Freeze the prior's effect into the spec.

    Only declared *values* move -- ``min_delta`` and the protocol's ``replicates`` -- so
    the kernel keeps parsing exactly the same shape.  The adjustment record itself lives
    at the spec's top level and inside the context snapshot, whose digest the bet freezes
    as ``context_snapshot_hash``: the before/after values are therefore bound to the
    frozen record without inventing a kernel field.
    """
    declared = _declared_values(spec)
    effects: list[dict[str, Any]] = []
    for effect in (spec.get("expected_effects") or []):
        row = dict(effect)
        if "min_delta" in row:
            row["min_delta"] = float(adjusted["min_delta"])
        effects.append(row)
    protocol = dict(spec.get("evaluation_protocol") or {})
    replicates = int(adjusted["replicates"])
    note = ""
    if action != "real_task" and replicates > 1:
        # The bounded metric action executes one arm run; declaring otherwise would
        # assert a repetition that never happened.
        replicates = 1
        note = "bounded_metric executes a single arm run; repetition rule not applied"
    protocol["replicates"] = replicates
    frozen_audit = dict(audit)
    if note:
        frozen_audit["replicates_note"] = note
    frozen_audit["declared"] = {k: declared[k] for k in ("min_delta", "replicates")}
    frozen_audit["applied"] = {"min_delta": float(adjusted["min_delta"]),
                              "replicates": replicates}
    return {**dict(spec), "expected_effects": effects, "evaluation_protocol": protocol,
            "data_version": f"class:{class_key}", "class_key": class_key,
            "prior": dict(prior), "prior_adjusted_parameter": frozen_audit,
            # The abstention decision the prior reached (or refused).  Frozen into the spec
            # and the context snapshot so the bet records *why* it did or did not wager.
            "abstention": dict(prior.get("abstention") or {}),
            "declared_values": {k: declared[k] for k in ("min_delta", "replicates")}}


def _resolve_declared(ctx, params):
    """The declared bet: spec with declared values, store, and the declared class key.

    Shared by the recall Event and the record Event so both derive byte-identical
    declared inputs (and therefore the same class key) from the same frozen message.
    """
    from partner.application.commitment_bounded_adapter import bounded_spec
    from partner.commitment.store import CommitmentStore
    token = _trace_token(ctx, params)
    job_id = str(getattr(ctx, "job_id", "") or "")
    if not token:
        return None, {"ok": False, "status": "failed",
                      "summary": "no trace token found in the triggering message",
                      "semantic_output": {"trace_token": "", "job_id": job_id},
                      "files": [], "evidence_refs": [], "token_usage": {}}
    message = _job_request(ctx) or token
    instance_id = str(getattr(ctx, "instance_id", "") or "")
    project_id = str(getattr(ctx, "project_id", "") or "unassigned")
    declared = _declared_real_task(message, params)
    if declared:
        from partner.application.real_task_adapter import (
            RealTaskError, load_task, real_task_spec,
        )
        task_root = Path(__file__).resolve().parents[2]
        try:
            task = load_task(task_root, declared["task_id"])
        except RealTaskError as exc:
            return None, {"ok": False, "status": "failed",
                          "summary": f"declared real task is unavailable: {exc}",
                          "semantic_output": {"trace_token": token, "job_id": job_id,
                                              "task_id": declared["task_id"]},
                          "files": [], "evidence_refs": [], "token_usage": {}}
        spec = real_task_spec(job_id=job_id, trace_token=token, task_id=declared["task_id"],
                              project_root=str(task_root), patch_file=declared["patch_file"],
                              task=task, instance_id=instance_id, project_id=project_id)
        action = "real_task"
        action_id = f"real_task:{declared['task_id']}"
        task_id, patch_file = declared["task_id"], declared["patch_file"]
    else:
        spec = bounded_spec(job_id=job_id, trace_token=token, message=message,
                            instance_id=instance_id, project_id=project_id)
        action, action_id = "bounded_metric", "bounded_metric"
        task, task_root, task_id, patch_file = None, None, "", ""
    spec = {**spec, "action_id": action_id}
    class_key = _class_key_for_spec(spec)
    spec = {**spec, "data_version": f"class:{class_key}", "class_key": class_key}
    store = CommitmentStore(Path(ctx.workspace), str(spec["run_id"]), str(spec["bet_id"]))
    from partner.application.experience_prior import class_components, metric_signature
    return {"token": token, "job_id": job_id, "message": message, "spec": spec, "store": store,
            "action": action, "action_id": action_id, "class_key": class_key,
            "components": class_components(project_id=str(spec.get("project_id") or ""),
                                           action_id=action_id,
                                           metric_signature=metric_signature(spec["expected_effects"])),
            "task": task, "task_root": task_root, "task_id": task_id,
            "patch_file": patch_file, "instance_id": instance_id,
            "project_id": project_id, "prior_disabled": _prior_disabled(message, params)}, None


def commitment_prior_recall(ctx, params):
    """Read same-class past settlements and freeze them into a prior for this bet.

    Strictly read-only over history: this Event never rewrites a settlement, an
    ExperienceRecord or an earlier bet.  Its one output is ``context/prior.json`` inside
    the store of the bet it was called for, plus a summary that lands in the flow's node
    outputs and therefore in the ledger.  When there is no same-class history the prior is
    explicitly empty -- no value is filled in.
    """
    from partner.application.experience_prior import (
        clear_prior, decide_abstention, empty_prior, scan_settled_bets, select_same_class,
        summarize_prior, write_prior,
    )
    bundle, failure = _resolve_declared(ctx, params)
    if failure is not None:
        return failure
    job_id, spec, store = bundle["job_id"], bundle["spec"], bundle["store"]
    class_key = bundle["class_key"]
    if bundle["prior_disabled"]:
        cleared = clear_prior(store.root)
        # prior=off never abstains: the decision needs a prior, and there is none.
        refused = {"abstain": False, "reason": "prior disabled: no prior was read",
                   "blocked_by": ["prior_disabled"], "rule": "", "evidence": {}}
        return {"ok": True, "status": "completed",
                "summary": f"prior disabled for {class_key}: declared values stand",
                "semantic_output": {"prior_class_key": class_key, "prior_row_count": 0,
                                    "prior_empty": True, "prior_disabled": True,
                                    "prior_cleared": cleared, "abstention": refused,
                                    "trace_token": bundle["token"],
                                    "job_id": job_id, "action": bundle["action"],
                                    "next_event": "commitment.bet_record"},
                "files": [], "evidence_refs": [], "token_usage": {}}
    rows = scan_settled_bets(Path(ctx.workspace), exclude_bet_id=str(spec["bet_id"]))
    same_class = select_same_class(rows, class_key)
    prior = summarize_prior(same_class, class_key=class_key, components=bundle["components"])
    path = write_prior(store.root, prior)
    noted = False
    try:
        from partner.index.job_repository import init as _init_jobs
        _init_jobs(Path(ctx.workspace)).note(
            job_id, actor="commitment.prior_recall", kind="commitment_prior_recalled",
            detail={"class_key": class_key, "prior_row_count": prior["row_count"],
                    "prior_empty": prior["empty"], "counts": prior["counts"],
                    "prior_bet_ids": [r.get("bet_id") for r in prior["rows"]],
                    "local_prior_suppressed": len(rows) - len(same_class),
                    "prior_path": str(path), "trace_token": bundle["token"]})
        noted = True
    except Exception:  # noqa: BLE001 -- the prior is the deliverable, the note is the trail
        noted = False
    return {"ok": True, "status": "completed",
            "summary": (f"prior for {class_key}: {prior['row_count']} same-class "
                        f"settlement(s) of {len(rows)} settled bet(s)"),
            "semantic_output": {"prior_class_key": class_key,
                                "prior_row_count": prior["row_count"],
                                "prior_empty": prior["empty"], "prior_disabled": False,
                                "prior_counts": prior["counts"],
                                "prior_bet_ids": [r.get("bet_id") for r in prior["rows"]],
                                "prior_sources": prior["sources"],
                                "scanned_settled_bets": len(rows),
                                "abstention": dict(prior.get("abstention") or {}),
                                "prior_path": str(path), "noted_on_timeline": noted,
                                "trace_token": bundle["token"], "job_id": job_id,
                                "action": bundle["action"],
                                "next_event": "commitment.bet_record"},
            "files": [str(path)], "evidence_refs": [], "token_usage": {}}


#: Declared draft tokens.  The contradiction check is a literal substring test, not an
#: LLM judgement: the draft either makes a claim the settlement refutes, or it does not.
_NEGATIVE_DRAFT_TOKENS = ("未取得进展", "执行失败", "没有留下终态回执", "无法判断", "未完成",
                          "被证伪", "未通过", "失败")
_POSITIVE_DRAFT_TOKENS = ("已取得进展", "已通过", "已完成", "成功", "优于基线", "达成")
#: With a supported verdict that *did* beat its baseline, a draft claiming there was no
#: improvement contradicts it just as loudly as one claiming outright failure.
_NEGATIVE_IMPROVEMENT_TOKENS = ("未取得改善", "没有取得改善", "没有改善", "未见改善", "无改善",
                                "未优于基线", "没有提升", "未见提升")


def draft_contradicts(draft: str, settlement: Mapping[str, Any]) -> tuple[bool, list[str]]:
    if str(settlement.get("settlement_class") or "") == "abstained":
        # An abstention claims nothing, so a draft that claims progress contradicts it.
        # A draft that reports a blockage does not: nothing ran, so "no progress" agrees.
        hits = [token for token in _POSITIVE_DRAFT_TOKENS if token in draft]
        return (bool(hits), hits)
    return _draft_contradicts_classified(draft, settlement)


def _draft_contradicts_classified(draft: str, settlement: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Pure, declared-token check: does the draft's prose contradict the settlement?"""
    text = str(draft or "")
    settlement_class = str(settlement.get("settlement_class") or "")
    hits: list[str] = []
    if settlement_class == "supported":
        hits += [t for t in _NEGATIVE_DRAFT_TOKENS if t in text]
        if settlement.get("improvement_over_baseline"):
            hits += [t for t in _NEGATIVE_IMPROVEMENT_TOKENS if t in text]
        else:
            hits += [t for t in ("优于基线", "改善", "提升") if t in text]
    elif settlement_class == "refuted":
        hits += [t for t in _POSITIVE_DRAFT_TOKENS if t in text]
    return (bool(hits), hits)


def _abstention_reason(settlement: Mapping[str, Any]) -> tuple[str, list[str]]:
    """The reason and the evidence lines of an abstention, read from its machine rules."""
    rules = [str(rule) for rule in (settlement.get("machine_rules") or [])]
    reason = next((rule.split("=", 1)[1] for rule in rules
                   if rule.startswith("abstain_reason=")), "")
    evidence = [rule for rule in rules if rule.startswith("abstain_evidence[")]
    return reason, evidence


def _settlement_block(settlement: Mapping[str, Any]) -> str:
    """The settlement as the reply's factual header -- every field spelled out."""
    if str(settlement.get("settlement_class") or "") == "abstained":
        reason, evidence = _abstention_reason(settlement)
        blockers = ", ".join(str(b) for b in (settlement.get("publish_blockers") or [])) or "none"
        lines = [
            f"[commitment] settlement_class=abstained publish_eligible={settlement.get('publish_eligible')} "
            f"publish_blockers={blockers}",
            f"bet={settlement.get('bet_id')} settlement={settlement.get('settlement_id')}",
            "本次选择不下注，原因如下：" + (reason or "（未记录原因）"),
        ]
        lines.extend(f"不下注证据：{line}" for line in evidence)
        lines.append(
            "（本次未执行任何动作：未生成补丁、未调用 LLM、未测量；"
            "expectations_met / improvement_over_baseline 一律不作声明）")
        return "\n".join(lines)
    blockers = ", ".join(str(b) for b in (settlement.get("publish_blockers") or [])) or "none"
    return ("[commitment] settlement_class={cls} improvement_over_baseline={imp} "
            "supported_claim={claim} publish_eligible={pub} publish_blockers={blockers}\n"
            "bet={bet} settlement={sid} expectations_met={met}").format(
        cls=settlement.get("settlement_class"), imp=settlement.get("improvement_over_baseline"),
        claim=settlement.get("supported_claim"), pub=settlement.get("publish_eligible"),
        blockers=blockers, bet=settlement.get("bet_id"), sid=settlement.get("settlement_id"),
        met=settlement.get("expectations_met"))


def _reconcile_draft(params: Mapping[str, Any]) -> str:
    outputs = params.get("flow_outputs") if isinstance(params.get("flow_outputs"), dict) else {}
    for key in ("deduplicate", "message_critic", "critic", "compose"):
        value = outputs.get(key)
        if isinstance(value, dict):
            text = str(value.get("message") or value.get("model_output") or "").strip()
            if text:
                return text
    return str(params.get("message") or params.get("text") or "").strip()


def commitment_reply_reconcile(ctx, params):
    """Make the commitment settlement the source of truth for the outgoing reply.

    The draft prose and the machine verdict are produced by different parts of the flow,
    and they can disagree (that happened for real: a ``supported`` settlement went out
    with a reply that said the task made no progress).  This Event runs before the send
    step: when the flow carries a settlement, the reply body is rebuilt from it, a
    contradicting draft is dropped and the contradiction is recorded on the timeline.
    """
    from partner.application.experience_prior import _read_json as _read
    job_id = str(getattr(ctx, "job_id", "") or "")
    store_root = (Path(str(getattr(ctx, "workspace", ""))) / "state" / "commitments"
                  / f"event_flow_{job_id}" / f"bet_{job_id}")
    state = _read(store_root / "state.json") or {}
    settlement = None
    settlement_dir = store_root / "settlement"
    if settlement_dir.is_dir():
        names = sorted(_p.name for _p in settlement_dir.iterdir() if _p.is_file())
        if names:
            settlement = _read(settlement_dir / names[-1]) or None
    if not isinstance(settlement, dict) or not settlement:
        return {"ok": True, "status": "completed",
                "summary": "no commitment settlement in this flow; the draft stands",
                "semantic_output": {"settlement_present": False, "job_id": job_id,
                                    "store": str(store_root)},
                "files": [], "evidence_refs": [], "token_usage": {}}
    draft = _reconcile_draft(params)
    contradiction, hits = draft_contradicts(draft, settlement)
    body = _settlement_block(settlement)
    if contradiction:
        # the contradiction and the tokens that triggered it are recorded in the event
        # log and in reply_reconciliation.json -- the reply itself carries the verdict,
        # not a repetition of what the draft got wrong
        body += "\n\n（回复草稿与本裁决矛盾，已按裁决改写；草稿表述被丢弃，矛盾已记入日志。）"
    elif draft:
        # The draft is the flow's own prose, not the verdict's: label it so a reader never
        # mistakes an unvetted narrative for something the settlement endorses.
        body += "\n\n【流内草稿·未经裁决背书】\n" + draft
    mentions = any(str(settlement.get(field)) in draft
                    for field in ("settlement_class", "supported_claim")) or \
        str(settlement.get("settlement_class")) in draft
    artifact_error = ""
    try:
        (store_root / "context").mkdir(parents=True, exist_ok=True)
        (store_root / "reply_reconciliation.json").write_text(json.dumps({
            "job_id": job_id, "settlement_id": settlement.get("settlement_id"),
            "settlement_class": settlement.get("settlement_class"),
            "improvement_over_baseline": settlement.get("improvement_over_baseline"),
            "supported_claim": settlement.get("supported_claim"),
            "publish_eligible": settlement.get("publish_eligible"),
            "publish_blockers": settlement.get("publish_blockers"),
            "contradiction": contradiction, "contradiction_tokens": hits,
            "draft_digest": __import__("hashlib").sha256(draft.encode("utf-8")).hexdigest(),
            "draft_mentions_verdict": mentions,
            "body_digest": __import__("hashlib").sha256(body.encode("utf-8")).hexdigest(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 -- the reply body is the deliverable, but a
        # failed audit write must be visible instead of silently dropped
        artifact_error = f"{type(exc).__name__}: {exc}"
    noted = False
    try:
        from partner.index.job_repository import init as _init_jobs
        if contradiction:
            _init_jobs(Path(str(getattr(ctx, "workspace", "")))).note(
                job_id, actor="commitment.reply_reconcile",
                kind="commitment_reply_contradiction",
                detail={"settlement_id": settlement.get("settlement_id"),
                        "settlement_class": settlement.get("settlement_class"),
                        "tokens": hits, "draft_digest": __import__("hashlib").sha256(
                            draft.encode("utf-8")).hexdigest()})
            noted = True
    except Exception:  # noqa: BLE001
        noted = False
    return {"ok": True, "status": "completed",
            # ``settlement_message`` at the top level as well: the delivery Event reads
            # node outputs by node id, and the message-compose Event already sets the
            # convention of exposing the outgoing text directly.
            "settlement_message": body,
            "draft_mentions_verdict": mentions,
            "summary": (f"reply rebuilt from settlement {settlement.get('settlement_id')}"
                        + ("; draft contradicted it" if contradiction else "")),
            "semantic_output": {"settlement_present": True,
                                "settlement_message": body,
                                "settlement_id": settlement.get("settlement_id"),
                                "settlement_class": settlement.get("settlement_class"),
                                "improvement_over_baseline": settlement.get(
                                    "improvement_over_baseline"),
                                "supported_claim": settlement.get("supported_claim"),
                                "publish_eligible": settlement.get("publish_eligible"),
                                "publish_blockers": settlement.get("publish_blockers"),
                                "contradiction": contradiction,
                                "contradiction_tokens": hits,
                                "contradiction_noted_on_timeline": noted,
                                "draft_replaced": contradiction, "job_id": job_id,
                                "draft_mentions_verdict": mentions,
                                "artifact_error": artifact_error},
            "files": ([str(store_root / "reply_reconciliation.json")]
                      if not artifact_error else []),
            "evidence_refs": [str(settlement.get("settlement_id") or "")], "token_usage": {}}


def _prepare_bounded(ctx, params):
    """Resolve the bounded bet this Event acts on: spec, store, snapshot, runner.

    Both commitment Events derive the same spec from the same frozen inputs, so the
    bet the second Event runs is byte-identical to the one the first Event recorded.
    """
    from partner.application.commitment_bounded_adapter import (
        build_bounded_runner, write_bounded_snapshot,
    )
    from partner.application.experience_prior import (
        adjust_declared, empty_prior, read_prior,
    )
    bundle, failure = _resolve_declared(ctx, params)
    if failure is not None:
        return None, failure
    token, job_id = bundle["token"], bundle["job_id"]
    message = bundle["message"]
    instance_id = bundle["instance_id"]
    spec = bundle["spec"]
    # The prior the recall Event left, if any.  A prior for a different class must never
    # leak into this bet, and an absent one is explicitly empty: declared values stand.
    prior = read_prior(bundle["store"].root)
    if str(prior.get("class_key") or "") != bundle["class_key"]:
        # prior=off is not a mismatch: the recall Event deleted the file on purpose, and the
        # audit trail must say so rather than blaming a class difference.
        prior = empty_prior(class_key=bundle["class_key"],
                            reason=("prior_disabled" if bundle.get("prior_disabled")
                                    else "prior_class_mismatch"))
    adjusted, audit = adjust_declared(_declared_values(spec), prior)
    spec = _apply_prior_to_spec(spec, class_key=bundle["class_key"], prior=prior,
                                audit=audit, adjusted=adjusted, action=bundle["action"])
    bundle = {**bundle, "spec": spec}
    if bundle["action"] == "real_task":
        # A real repository task: the input and the patch are on disk, and the project's
        # own test runner decides the outcome.
        from partner.application.real_task_adapter import (build_real_task_runner,
                                                           write_real_task_snapshot)
        snapshot_path = write_real_task_snapshot(
            store=bundle["store"], spec=spec, job_id=job_id, trace_token=token,
            task=bundle["task"], project_root=str(bundle["task_root"]),
            patch_file=bundle["patch_file"], instance_id=instance_id,
            prior=spec.get("prior"), prior_audit=spec.get("prior_adjusted_parameter"),
            abstention=spec.get("abstention"))
        runner = build_real_task_runner(Path(ctx.workspace), spec, snapshot_path=snapshot_path)
        return {"token": token, "job_id": job_id, "spec": spec, "store": bundle["store"],
                "runner": runner, "message": message, "action": "real_task",
                "task_id": bundle["task_id"], "patch_file": bundle["patch_file"],
                "class_key": bundle["class_key"],
                "prior_adjusted_parameter": spec.get("prior_adjusted_parameter")}, None
    snapshot_path = write_bounded_snapshot(
        store=bundle["store"], spec=spec, job_id=job_id, trace_token=token, message=message,
        instance_id=instance_id, prior=spec.get("prior"),
        prior_audit=spec.get("prior_adjusted_parameter"), abstention=spec.get("abstention"))
    runner = build_bounded_runner(Path(ctx.workspace), spec, snapshot_path=snapshot_path)
    return {"token": token, "job_id": job_id, "spec": spec, "store": bundle["store"],
            "runner": runner, "message": message, "action": "bounded_metric",
            "class_key": bundle["class_key"],
            "prior_adjusted_parameter": spec.get("prior_adjusted_parameter")}, None


def commitment_bet_record(ctx, params):
    """Record one BetRecord for the triggering message.  Executes nothing.

    The bet is frozen with its expectation, failure conditions, protocol, budget and
    treatment; execution, measurement and settlement happen in the later
    ``commitment.bet_execute`` Event.  Recording is idempotent: a replay re-freezes
    identical semantics, which the store accepts without touching the record.
    """
    prepared, failure = _prepare_bounded(ctx, params)
    if failure is not None:
        return failure
    token, job_id = prepared["token"], prepared["job_id"]
    spec, store = prepared["spec"], prepared["store"]
    result = prepared["runner"].freeze_only()
    # "recorded" means COMMITTED (the bet exists and is frozen) or already terminal on
    # a replay; anything else means the freeze stopped the bet and nothing was recorded
    if result.state != sm.COMMITTED and not is_terminal(result.state):
        return {"ok": False, "status": result.state,
                "summary": f"bet was not recorded: {result.reason}",
                "semantic_output": {"trace_token": token, "job_id": job_id,
                                    "state": result.state, "reason": result.reason},
                "files": [], "evidence_refs": [], "token_usage": {}}
    record = store.load_bet()
    if not store.has_event(f"{record.bet_id}:bet_recorded"):
        store.append("bet_recorded",
                     {"bet_id": record.bet_id, "trace_token": token, "job_id": job_id,
                      "state": record.status, "freeze_hash": record.freeze_hash(),
                      "recorded_by": "commitment.bet_record"},
                     event_id=f"{record.bet_id}:bet_recorded")
    store.write_manifest(extra={"trace_token": token, "bet_id": record.bet_id,
                               "recorded_by": "commitment.bet_record"})
    noted = False
    try:
        from partner.index.job_repository import init as _init_jobs
        if not any(row.get("kind") == "commitment_bet_recorded"
                   for row in _init_jobs(Path(ctx.workspace)).history(job_id)):
            _init_jobs(Path(ctx.workspace)).note(
                job_id, actor="commitment.bet_record", kind="commitment_bet_recorded",
                detail={"bet_id": record.bet_id, "trace_token": token,
                        "state": record.status, "run_id": spec["run_id"],
                        "store": str(store.root)})
        noted = True
    except Exception:  # noqa: BLE001 - the bet is the deliverable, the note is the trail
        noted = False
    return {
        "ok": True, "status": result.state,
        "summary": f"commitment bet {record.bet_id} recorded for {token} (not yet executed)",
        "semantic_output": {"bet_id": record.bet_id, "trace_token": token,
                            "state": result.state, "run_id": spec["run_id"],
                            "job_id": job_id, "noted_on_timeline": noted,
                            "freeze_hash": record.freeze_hash(), "store": str(store.root),
                            "next_event": map_kernel_state(result.state)},
        "files": [str(store.path("bet.json")), str(store.events_path)],
        "evidence_refs": [], "token_usage": {},
    }


def commitment_bet_execute(ctx, params):
    """Run the recorded bet to a terminal state: act, measure, compare, settle.

    The action is bounded and deterministic (text statistics under one declared
    transform), the baseline is executed and measured through the same instrument,
    and the verdict comes from the kernel's machine rules -- never from this Event.
    """
    prepared, failure = _prepare_bounded(ctx, params)
    if failure is not None:
        return failure
    token, job_id = prepared["token"], prepared["job_id"]
    spec, store, runner = prepared["spec"], prepared["store"], prepared["runner"]
    abstention = dict(spec.get("abstention") or {})
    if abstention.get("abstain"):
        # The harness declines to wager: freeze, record the decision, settle ABSTAINED.
        # Nothing is executed -- no proposer, no patch, no arm, no metric.
        result = runner.abstain()
    else:
        result = runner.run()
    settlement = result.settlement
    experience = result.experience
    noted = False
    try:
        from partner.index.job_repository import init as _init_jobs
        _init_jobs(Path(ctx.workspace)).note(
            job_id, actor="commitment.bet_execute", kind="commitment_bet_settled",
            detail={"bet_id": store.bet_id, "trace_token": token, "state": result.state,
                    "settlement_class": None if settlement is None else settlement.settlement_class,
                    "settlement_id": None if settlement is None else settlement.settlement_id,
                    "experience_id": None if experience is None else experience.experience_id,
                    "run_id": spec["run_id"], "store": str(store.root)})
        noted = True
    except Exception:  # noqa: BLE001
        noted = False
    abstained = settlement is not None and settlement.settlement_class == "abstained"
    payload = {
        "ok": is_terminal(result.state), "status": result.state,
        "summary": f"commitment bet {store.bet_id} -> {result.state}: {result.reason}",
        "semantic_output": {
            "bet_id": store.bet_id, "trace_token": token, "state": result.state,
            "reason": result.reason, "run_id": spec["run_id"], "job_id": job_id,
            "settlement_id": None if settlement is None else settlement.settlement_id,
            "settlement_class": None if settlement is None else settlement.settlement_class,
            "expectations_met": None if settlement is None else settlement.expectations_met,
            "improvement_over_baseline": (None if settlement is None
                                          else settlement.improvement_over_baseline),
            "publish_eligible": None if settlement is None else settlement.publish_eligible,
            "publish_blockers": [] if settlement is None else list(settlement.publish_blockers),
            "experience_id": None if experience is None else experience.experience_id,
            "noted_on_timeline": noted,
            "receipt_id": None if result.receipt is None else result.receipt.receipt_id,
            "measurement_id": (None if result.measurement is None
                               or not result.measurement.measurements
                               else result.measurement.measurements[0].measurement_id),
            "baseline_id": None if result.baseline_evidence is None
                           else result.baseline_evidence.baseline_id,
            "replayed": result.replayed,
            "artifacts": dict(result.paths),
            "store": str(store.root),
        },
        "files": [path for path in result.paths.values() if isinstance(path, str)],
        "evidence_refs": ([] if settlement is None else [settlement.settlement_id]),
        "token_usage": {},
    }
    if abstained:
        # An abstention is a decision the user is entitled to see: the harness declined to
        # wager on this class.  Without these two flags the notification gate would treat
        # the run as "nothing to report" and the whole message path (compose .. send) would
        # be skipped, so the reply line would never exist.  ``requires_human`` is the honest
        # flag: nothing machine-derivable moves this class forward -- only new evidence.
        reason, evidence = _abstention_reason(settlement.to_dict())
        payload["requires_human"] = True
        payload["notification_kind"] = "abstention"
        payload["abstention"] = {"reason": reason, "evidence": evidence,
                                 "rule": next((str(rule).split("=", 1)[1] for rule in
                                               settlement.machine_rules
                                               if str(rule).startswith("abstain_evidence[")),
                                              "")}
    return payload


DEFINITIONS = [
    EventDefinition(
        "commitment.prior_recall", "commitment",
        "Read same-class past settlements into a prior for this bet (read-only over history)",
        commitment_prior_recall, execution_method="local", produces_artifact=True,
        timeout_seconds=120, concurrency_scope="project"),
    EventDefinition(
        "commitment.reply_reconcile", "commitment",
        "Make the commitment settlement the source of truth for the outgoing reply",
        commitment_reply_reconcile, execution_method="local", produces_artifact=True,
        timeout_seconds=120, concurrency_scope="project"),
    EventDefinition(
        "commitment.bet_run", "commitment",
        "Run one bounded commitment bet to a terminal state (idempotent on replay)",
        commitment_bet_run, execution_method="local", produces_artifact=True,
        timeout_seconds=900, concurrency_scope="project"),
    EventDefinition(
        "commitment.bet_record", "commitment",
        "Record one BetRecord for the triggering message (record-only, no execution)",
        commitment_bet_record, execution_method="local", produces_artifact=True,
        timeout_seconds=120, concurrency_scope="project"),
    EventDefinition(
        "commitment.bet_execute", "commitment",
        "Run a recorded commitment bet to a terminal state (bounded action, machine settlement)",
        commitment_bet_execute, execution_method="local", produces_artifact=True,
        timeout_seconds=300, concurrency_scope="project"),
    EventDefinition(
        "commitment.bet_state", "commitment",
        "Read-only lifecycle projection of one commitment bet",
        commitment_bet_state, execution_method="local", timeout_seconds=60),
    EventDefinition(
        "commitment.bet_settlement", "commitment",
        "Read-only settlement and experience projection of one commitment bet",
        commitment_bet_settlement, execution_method="local", timeout_seconds=60),
]

__all__ = ["DEFINITIONS", "KERNEL_STATE_EVENTS", "map_kernel_state", "commitment_bet_run",
           "commitment_bet_record", "commitment_bet_execute",
           "commitment_bet_state", "commitment_bet_settlement",
           "commitment_prior_recall", "commitment_reply_reconcile", "draft_contradicts"]
