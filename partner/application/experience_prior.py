"""Experience consumption: same-class settlements become a prior for the next bet.

This module is what makes the harness bet *with its own history*.  Before a bet is
frozen, the same-class settlements and ExperienceRecords already on disk are read, and a
deterministic, pure-Python rule turns them into an adjustment of a declared decision
variable.  Nothing here calls an LLM, and nothing here writes history: the read side is
strictly read-only and the adjustment is a total function of (declared values, prior).

Class definition -- minimal, computable, no semantic matching
-------------------------------------------------------------

    class_key = "cls_" + sha256("|".join([project_id, action_id, metric_signature]))[:16]

* ``project_id``        the project the work belongs to
* ``action_id``         the declared action (``real_task:<task_id>`` / ``bounded_metric``)
* ``metric_signature``  sorted ``metric:kind:direction`` of the declared expectations

The flow type is deliberately *not* in the key: intent routing is the instance's own
decision and must not fork a task class.  The key is frozen into the bet as
``data_version = "class:<key>"``, so any later round retrieves history by reading
``bet.json`` -- no classifier, no LLM, no fuzzy match.

Adjustment rules -- ordered, total, auditable
---------------------------------------------

1. ``refuted_ratio >= REFUTED_RATIO_THRESHOLD`` (refuted == the kernel's ``falsified``;
   ties count as refuted) -> ``min_delta = min(MAX_MIN_DELTA, base * (1 + refuted))``.
   Raising is bounded by ``MAX_MIN_DELTA`` and is *not* blocked by ``MIN_DELTA_FLOOR``:
   the floor only bounds the relaxing direction, so a class sitting on the floor can
   still be tightened.
2. else any ``supported`` -> ``min_delta = max(MIN_DELTA_FLOOR, base / 2**supported)``
   (the class has demonstrated real improvements; lower the bar)
3. else (only inconclusive/blocked) -> ``min_delta`` unchanged
4. any prior row with ``improvement_over_baseline=False`` -> ``require_baseline_rerun``
   forced True
5. any prior row blocked with ``single_episode_only`` -> ``replicates = 2``, i.e. one
   more *really executed* repetition of the candidate arm

An empty prior never fills in a value: it returns the declared values untouched and
records ``prior_adjusted=false`` with an explicit ``prior_empty`` reason.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PRIOR_VERSION = "experience-prior/1"
PRIOR_FILENAME = "prior.json"
CLASS_PREFIX = "cls_"
DATA_VERSION_PREFIX = "class:"
MIN_DELTA_FLOOR = 0.25
BLOCKER_SINGLE_EPISODE = "single_episode_only"

#: The kernel's authoritative settlement classes.  Source of truth:
#: ``partner/commitment/models.py::SETTLEMENT_CLASSES`` -- the kernel calls a refuted
#: bet **falsified**, and it never emits the word "refuted".  Counting a class the
#: kernel does not emit is how a rule silently never fires, so this tuple is copied
#: from the kernel rather than invented here.
SETTLEMENT_CLASSES = ("supported", "falsified", "inconclusive", "invalid", "blocked",
                      "abstained")
#: Older notes and transcripts call the same outcome "refuted".  Both words are counted
#: into the ``falsified`` bucket so a settlement can never be invisible to the rule.
REFUTED_ALIASES = ("falsified", "refuted")

#: Share of same-class settlements that must be refuted before the bar goes up.
#: A tie (exactly half) counts as refuted: the stricter rule wins ties.
REFUTED_RATIO_THRESHOLD = 0.5
#: Upper bound on ``min_delta`` so a long refuted history can never raise the bar
#: without limit (the lower bound is :data:`MIN_DELTA_FLOOR`).
MAX_MIN_DELTA = 8.0

#: -- abstention --------------------------------------------------------------
#: How many *evidence-bearing* same-class settlements must exist before declining to wager
#: is even allowed (one bad run is not a trend).
ABSTAIN_MIN_EVIDENCE = 2
#: Share of those that must be refuted (the kernel word: ``falsified``).
ABSTAIN_REFUTED_RATIO = 0.75
#: The rule's name, recorded in the frozen prior and in the settlement.
ABSTAIN_RULE = "abstention_rule_refuted_history_without_success"


# ---------------------------------------------------------------------------
# the class key: one pure function, no LLM
# ---------------------------------------------------------------------------

def metric_signature(expected_effects: Iterable[Mapping[str, Any]]) -> str:
    """A stable, sorted ``metric:kind:direction`` signature of the declared effects."""
    parts = sorted(f"{e.get('metric')}:{e.get('kind') or 'absolute_threshold'}:"
                   f"{e.get('direction') or 'increase'}" for e in expected_effects)
    return ";".join(parts)


def task_class_key(*, project_id: str, action_id: str, metric_signature: str) -> str:
    """The class key: a pure function of three declared strings.

    Same project + same action + same metric shape == same class.  Nothing about the
    message text, the trace token or the routing participates.
    """
    body = "|".join(str(x or "") for x in (project_id, action_id, metric_signature))
    return CLASS_PREFIX + hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def class_key_from_bet(bet: Mapping[str, Any]) -> str:
    """Recover the class key from a frozen BetRecord (``data_version``)."""
    value = str(bet.get("data_version") or "")
    return value[len(DATA_VERSION_PREFIX):] if value.startswith(DATA_VERSION_PREFIX) else ""


def class_components(*, project_id: str, action_id: str, metric_signature: str) -> dict[str, str]:
    return {"project_id": str(project_id or ""), "action_id": str(action_id or ""),
            "metric_signature": str(metric_signature or "")}


# ---------------------------------------------------------------------------
# read side: strictly read-only over what earlier bets already left on disk
# ---------------------------------------------------------------------------

def commitments_root(workspace: str | os.PathLike) -> Path:
    return Path(workspace) / "state" / "commitments"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- a partial write is skipped, never guessed at
        return None


def _mtime_epoch(path) -> float:
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return 0.0


def scan_settled_bets(workspace: str | os.PathLike, *,
                      exclude_bet_id: str = "") -> list[dict[str, Any]]:
    """Every bet under ``state/commitments`` that has a settlement, oldest first.

    Read-only.  A directory that is missing a settlement, or that cannot be parsed, is
    skipped rather than invented -- an unreadable row must never look like evidence.
    """
    rows: list[dict[str, Any]] = []
    root = commitments_root(workspace)
    if not root.is_dir():
        return rows
    for run_dir in sorted(os.listdir(root)):
        run_path = root / run_dir
        if not run_path.is_dir():
            continue
        for bet_dir in sorted(os.listdir(run_path)):
            bet_path = run_path / bet_dir
            if not bet_path.is_dir() or bet_dir == exclude_bet_id:
                continue
            bet = _read_json(bet_path / "bet.json")
            if not isinstance(bet, dict) or not bet:
                continue
            settlement_dir = bet_path / "settlement"
            settlement = None
            if settlement_dir.is_dir():
                names = sorted(os.listdir(settlement_dir))
                if names:
                    settlement = _read_json(settlement_dir / names[-1])
            if not isinstance(settlement, dict) or not settlement:
                continue
            state = _read_json(bet_path / "state.json") or {}
            experience = None
            experience_dir = bet_path / "experience"
            if experience_dir.is_dir():
                names = sorted(os.listdir(experience_dir))
                if names:
                    experience = _read_json(experience_dir / names[-1])
            snapshot = _read_json(bet_path / "context" / "snapshot.json") or {}
            rows.append({
                "bet_id": str(bet.get("bet_id") or bet_dir),
                "run_id": str(bet.get("run_id") or run_dir),
                "class_key": class_key_from_bet(bet),
                "project_id": str(bet.get("project_id") or ""),
                "question": str(bet.get("question") or ""),
                "selected_action": str(bet.get("selected_action") or ""),
                "selection_reason": str(bet.get("selection_reason") or ""),
                "trace_token": str(snapshot.get("trace_token") or ""),
                "created_at": str(bet.get("created_at") or ""),
                "lifecycle_state": str(state.get("state") or ""),
                "settlement_id": str(settlement.get("settlement_id") or ""),
                "settlement_class": str(settlement.get("settlement_class") or ""),
                "expectations_met": bool(settlement.get("expectations_met")),
                "improvement_over_baseline": bool(settlement.get("improvement_over_baseline")),
                "supported_claim": str(settlement.get("supported_claim") or ""),
                "publish_eligible": bool(settlement.get("publish_eligible")),
                "publish_blockers": [str(b) for b in (settlement.get("publish_blockers") or [])],
                # The kernel settlement carries ``created_at`` (there is no ``settled_at``
                # field); the lifecycle keeps ``updated_at``; the settlement file's mtime is
                # the last-resort monotone fallback.  ``settled_epoch`` is what makes an
                # ordering possible even when every timestamp field is empty.
                "settled_at": str(settlement.get("settled_at")
                                  or settlement.get("created_at")
                                  or state.get("updated_at") or ""),
                "settled_epoch": _mtime_epoch(settlement_dir / names[-1]),
                "experience_id": str((experience or {}).get("experience_id") or ""),
                "settlement_ref": str(settlement_dir / sorted(os.listdir(settlement_dir))[-1]),
                "store_dir": str(bet_path),
            })
    rows.sort(key=lambda r: (r["created_at"], r["bet_id"]))
    return rows


def select_same_class(rows: Sequence[Mapping[str, Any]], class_key: str) -> list[dict[str, Any]]:
    """Only the rows whose frozen class key equals ``class_key`` -- never the rest."""
    if not class_key:
        return []
    return [dict(r) for r in rows if str(r.get("class_key")) == class_key]


def summarize_prior(rows: Sequence[Mapping[str, Any]], *, class_key: str,
                    components: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The prior: the same-class rows plus machine-counted statistics."""
    counts = {name: 0 for name in SETTLEMENT_CLASSES}
    for row in rows:
        cls = str(row.get("settlement_class") or "")
        if cls in REFUTED_ALIASES:
            counts["falsified"] += 1
        elif cls in counts:
            counts[cls] += 1
    total = len(rows)
    # The ratio is over *evidence-bearing* settlements: an abstention is not evidence about
    # the task, so it must neither soften nor sharpen the measured failure rate.  (The two
    # rules -- raising the bar and declining to wager -- therefore read the same ratio.)
    evidence_total = max(total - counts["abstained"], 0)
    refuted_ratio = (counts["falsified"] / evidence_total) if evidence_total else 0.0
    supporting = [{"bet_id": r.get("bet_id"), "run_id": r.get("run_id"),
                   "trace_token": r.get("trace_token"),
                   "settlement_class": r.get("settlement_class"),
                   "expectations_met": r.get("expectations_met"),
                   "improvement_over_baseline": r.get("improvement_over_baseline"),
                   "supported_claim": r.get("supported_claim"),
                   "selected_action": r.get("selected_action"),
                   "selection_reason": r.get("selection_reason"),
                   "trace_token_of_settlement": r.get("settlement_id"),
                   "publish_blockers": r.get("publish_blockers")}
                  for r in rows]
    summary = {
        "prior_version": PRIOR_VERSION,
        "class_key": str(class_key),
        "class_definition": dict(components or {}),
        "empty": not rows,
        "row_count": len(rows),
        "counts": {
            "total": len(rows),
            "evidence_total": evidence_total,
            **counts,
            "refuted_ratio": round(refuted_ratio, 6),
            "refuted_ratio_threshold": REFUTED_RATIO_THRESHOLD,
            "improvement_true": sum(1 for r in rows if r.get("improvement_over_baseline")),
            "improvement_false": sum(1 for r in rows if not r.get("improvement_over_baseline")),
            "single_episode_only": sum(1 for r in rows
                                       if BLOCKER_SINGLE_EPISODE in (r.get("publish_blockers") or [])),
        },
        "rows": supporting,
        "sources": [str(r.get("settlement_ref") or "") for r in rows],
    }
    # The abstention decision is part of the prior, not a separate later step: it is frozen
    # into ``prior.json`` (and from there into the bet's context snapshot) with the counts
    # and the reason it was taken or refused.
    summary["abstention"] = decide_abstention(summary)
    return summary


def decide_abstention(prior: Mapping[str, Any]) -> dict[str, Any]:
    """Decide whether this class should not be wagered on again -- pure, auditable.

    All four conditions must hold (thresholds are module constants, never inline):

    1. at least :data:`ABSTAIN_MIN_EVIDENCE` *evidence-bearing* settlements exist
    2. ``falsified / evidence_total >= ABSTAIN_REFUTED_RATIO``
    3. no supported settlement exists anywhere in the class (no success evidence at all)
    4. the most recent settlement in the class is ``falsified`` -- which is also what stops
       an abstention from firing twice in a row: an abstention at the tail means the class
       already declined and no new evidence has arrived since.

    Abstentions are excluded from the ratio's denominator on purpose: declining to wager is
    not evidence about the task, so it must neither raise nor lower the failure rate.
    """
    counts = dict(prior.get("counts") or {})
    rows = [dict(row) for row in (prior.get("rows") or [])]
    abstained = int(counts.get("abstained") or 0)
    total = int(counts.get("total") or 0)
    evidence_total = max(total - abstained, 0)
    refuted = int(counts.get("falsified") or 0)
    supported = int(counts.get("supported") or 0)
    ratio = (refuted / evidence_total) if evidence_total else 0.0
    ordered = sorted(rows, key=lambda row: (str(row.get("settled_at") or ""),
                                            float(row.get("settled_epoch") or 0.0)))
    latest = dict(ordered[-1]) if ordered else {}
    latest_class = str(latest.get("settlement_class") or "")
    blocked_by: list[str] = []
    if not str(prior.get("class_key") or ""):
        blocked_by.append("prior_has_no_class_key")
    if evidence_total < ABSTAIN_MIN_EVIDENCE:
        blocked_by.append(f"evidence_total {evidence_total} < {ABSTAIN_MIN_EVIDENCE}")
    if ratio < ABSTAIN_REFUTED_RATIO:
        blocked_by.append(f"refuted_ratio {round(ratio, 4)} < {ABSTAIN_REFUTED_RATIO}")
    if supported > 0:
        blocked_by.append(f"supported {supported} > 0 (success evidence exists)")
    if latest_class != "falsified":
        blocked_by.append(
            f"latest settlement is {latest_class or 'none'}, not falsified"
            + (" (an abstention is already the tail: no new evidence since)"
               if latest_class == "abstained" else ""))
    evidence = {
        "class_key": str(prior.get("class_key") or ""),
        "total": total, "evidence_total": evidence_total, "falsified": refuted,
        "supported": supported, "abstained": abstained,
        "refuted_ratio": round(ratio, 6), "refuted_ratio_threshold": ABSTAIN_REFUTED_RATIO,
        "min_evidence": ABSTAIN_MIN_EVIDENCE,
        "latest_settlement_class": latest_class,
        "latest_bet_id": str(latest.get("bet_id") or ""),
        "refuted_bet_ids": [str(r.get("bet_id") or "") for r in rows
                            if str(r.get("settlement_class") or "") in REFUTED_ALIASES],
    }
    triggered = not blocked_by
    if triggered:
        reason = (f"same-class history is {refuted}/{evidence_total} refuted "
                  f"(ratio {round(ratio, 4)} >= {ABSTAIN_REFUTED_RATIO}) with no supported "
                  f"settlement, and the latest settlement ({evidence['latest_bet_id']}) is "
                  "falsified: the harness declines to wager again without new evidence")
    else:
        reason = "abstention conditions not met: " + "; ".join(blocked_by)
    return {"abstain": triggered, "reason": reason, "blocked_by": blocked_by,
            "rule": ABSTAIN_RULE, "evidence": evidence}


def empty_prior(*, class_key: str, components: Mapping[str, str] | None = None,
                reason: str = "prior_empty") -> dict[str, Any]:
    """An explicitly empty prior.  It carries no value for anything."""
    return {"prior_version": PRIOR_VERSION, "class_key": str(class_key),
            "class_definition": dict(components or {}), "empty": True, "row_count": 0,
            "counts": {"total": 0, **{name: 0 for name in SETTLEMENT_CLASSES},
                       "refuted_ratio": 0.0,
                       "refuted_ratio_threshold": REFUTED_RATIO_THRESHOLD,
                       "improvement_true": 0, "improvement_false": 0,
                       "single_episode_only": 0},
            "rows": [], "sources": [], "reason": reason,
            # An empty prior refuses the abstention explicitly: declining to wager needs
            # same-class evidence, and there is none.
            "abstention": {"abstain": False, "blocked_by": ["prior_empty"], "rule": "",
                           "reason": f"prior is empty ({reason}): nothing to abstain on",
                           "evidence": {"prior_empty_reason": str(reason),
                                        "class_key": str(class_key)}}}


# ---------------------------------------------------------------------------
# the adjustment: deterministic, total, auditable
# ---------------------------------------------------------------------------

def adjust_declared(declared: Mapping[str, Any], prior: Mapping[str, Any]) -> tuple[dict, dict]:
    """Turn the prior into an adjustment of declared decision variables.

    ``declared`` carries ``min_delta``, ``replicates`` and ``require_baseline_rerun``.
    Returns ``(adjusted, audit)``; with an empty prior the adjusted values are the
    declared ones and the audit says so explicitly (``prior_adjusted=False``).
    """
    adjusted = {"min_delta": float(declared.get("min_delta") or 0.0),
                "replicates": int(declared.get("replicates") or 1),
                "require_baseline_rerun": bool(declared.get("require_baseline_rerun"))}
    counts = dict((prior or {}).get("counts") or {})
    class_key = str((prior or {}).get("class_key") or "")
    rows = list((prior or {}).get("rows") or [])
    parameters: list[dict[str, Any]] = []
    if (prior or {}).get("empty") or int(counts.get("total") or 0) < 1:
        return adjusted, {"prior_adjusted": False, "prior_class_key": class_key,
                          "prior_row_count": 0, "parameters": [],
                          "reason": "prior_empty", "prior_version": PRIOR_VERSION}
    base = adjusted["min_delta"]
    refuted = int(counts.get("falsified") or 0)
    supported = int(counts.get("supported") or 0)
    total = int(counts.get("total") or 0)
    ratio = float(counts.get("refuted_ratio") or 0.0)
    # -- priority: a refuted-dominated class outranks a supported one.  The bar goes up
    # whenever refuted/total >= REFUTED_RATIO_THRESHOLD (ties included); the relaxing
    # rule below only applies when the class is NOT refuted-dominated.
    if total >= 1 and ratio >= REFUTED_RATIO_THRESHOLD:
        after = min(MAX_MIN_DELTA, base * (1 + refuted))
        if after > base:
            adjusted["min_delta"] = after
            parameters.append({
                "name": "min_delta", "before": base, "after": after,
                "rule": "refuted_history_raises_the_bar",
                "evidence": {"falsified": refuted, "supported": supported, "total": total,
                             "evidence_total": int(counts.get("evidence_total") or 0),
                             "refuted_ratio": round(ratio, 6),
                             "refuted_ratio_threshold": REFUTED_RATIO_THRESHOLD,
                             "max_min_delta": MAX_MIN_DELTA,
                             "floor_note": ("raising is bounded by MAX_MIN_DELTA; the floor "
                                            "only bounds the relaxing direction")},
                "why": (f"{refuted}/{total} settlement(s) refuted "
                        f"(ratio {round(ratio, 4)} >= {REFUTED_RATIO_THRESHOLD})")})
    elif supported > 0 and base > 0:
        # only when there is a bar to lower: a declared 0.0 is already at the floor, and
        # "lowering" it to the floor would silently tighten the bet instead
        after = max(MIN_DELTA_FLOOR, base / (2 ** supported))
        if after < base:
            adjusted["min_delta"] = after
            parameters.append({
                "name": "min_delta", "before": base, "after": after,
                "rule": "supported_history_lowers_the_bar",
                # the refuted share is recorded here too: a reader must be able to see
                # that this branch was taken *because* it stayed below the threshold
                "evidence": {"supported": supported, "floor": MIN_DELTA_FLOOR,
                             "falsified": refuted, "total": total,
                             "refuted_ratio": round(ratio, 6),
                             "refuted_ratio_threshold": REFUTED_RATIO_THRESHOLD},
                "why": (f"{supported} supported settlement(s) and only {refuted}/{total} "
                        f"refuted (ratio {round(ratio, 4)} < {REFUTED_RATIO_THRESHOLD})")})
    if int(counts.get("improvement_false") or 0) > 0 and not adjusted["require_baseline_rerun"]:
        adjusted["require_baseline_rerun"] = True
        parameters.append({
            "name": "require_baseline_rerun", "before": bool(declared.get("require_baseline_rerun")),
            "after": True, "rule": "improvement_false_forces_a_real_baseline",
            "evidence": {"improvement_false": int(counts.get("improvement_false") or 0)},
            "why": "a same-class settlement did not beat its baseline"})
    if int(counts.get("single_episode_only") or 0) > 0 and adjusted["replicates"] < 2:
        adjusted["replicates"] = 2
        parameters.append({
            "name": "replicates", "before": int(declared.get("replicates") or 1), "after": 2,
            "rule": "single_episode_blocker_adds_one_executed_repetition",
            "evidence": {"single_episode_only": int(counts.get("single_episode_only") or 0)},
            "why": "a same-class settlement was blocked as single_episode_only"})
    audit = {"prior_adjusted": bool(parameters), "prior_class_key": class_key,
             "prior_row_count": int(counts.get("total") or 0), "parameters": parameters,
             "reason": "prior_applied" if parameters else "prior_present_no_rule_triggered",
             "prior_version": PRIOR_VERSION,
             "prior_bet_ids": [r.get("bet_id") for r in rows]}
    return adjusted, audit


# ---------------------------------------------------------------------------
# on-disk prior, next to the bet that will consume it
# ---------------------------------------------------------------------------

def prior_path(store_root: str | os.PathLike) -> Path:
    return Path(store_root) / "context" / PRIOR_FILENAME


def write_prior(store_root: str | os.PathLike, prior: Mapping[str, Any]) -> Path:
    path = prior_path(store_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(prior), ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")
    return path


def read_prior(store_root: str | os.PathLike) -> dict[str, Any]:
    """The prior the recall node left, or an explicit empty one when nothing is there."""
    payload = _read_json(prior_path(store_root))
    if isinstance(payload, dict) and payload:
        return payload
    return empty_prior(class_key="", reason="prior_absent")


def clear_prior(store_root: str | os.PathLike) -> bool:
    path = prior_path(store_root)
    if path.exists():
        path.unlink()
        return True
    return False


__all__ = ["PRIOR_VERSION", "PRIOR_FILENAME", "CLASS_PREFIX", "DATA_VERSION_PREFIX",
           "MIN_DELTA_FLOOR", "MAX_MIN_DELTA", "REFUTED_RATIO_THRESHOLD", "REFUTED_ALIASES",
           "ABSTAIN_MIN_EVIDENCE", "ABSTAIN_REFUTED_RATIO", "ABSTAIN_RULE", "decide_abstention",
           "BLOCKER_SINGLE_EPISODE", "SETTLEMENT_CLASSES",
           "metric_signature", "task_class_key", "class_key_from_bet", "class_components",
           "commitments_root", "scan_settled_bets", "select_same_class", "summarize_prior",
           "empty_prior", "adjust_declared", "prior_path", "write_prior", "read_prior",
           "clear_prior"]
