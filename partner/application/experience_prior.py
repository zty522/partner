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

#: -- related classes (cross-class prior transfer) ---------------------------
#: How much a related class's evidence is worth, by *how* it is related.  Highest
#: matching component wins; the weights never add up.
RELATED_WEIGHT_SAME_ACTION = 0.6     # same kind of task, different project/metric
RELATED_WEIGHT_SAME_METRIC = 0.5     # same measurement shape, different task/project
RELATED_WEIGHT_SAME_PROJECT = 0.4    # same project, different task/metric
#: The direct class' own weight, for the record: direct evidence is never discounted.
DIRECT_WEIGHT = 1.0


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


def bet_class_components(bet: Mapping[str, Any],
                         snapshot: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild a settled bet's class components from what is on disk.

    An earlier round stored the class key but not its components, so a related-class search
    has to reconstruct them: ``project_id`` from the bet, the metric signature from the
    frozen expectations, and the action id from the snapshot (a real-task snapshot names
    its ``task_id``; a bounded-action snapshot carries its ``candidate_space``).  When the
    rebuild does not reproduce the stored key the row is marked unverified -- a row whose
    class cannot be proven must not silently become another class' evidence.
    """
    bet = dict(bet or {})
    snapshot = dict(snapshot or {})
    project_id = str(bet.get("project_id") or "")
    task_id = str(snapshot.get("task_id") or "")
    if task_id:
        action_id = f"real_task:{task_id}"
    elif snapshot.get("candidate_space"):
        action_id = "bounded_metric"
    else:
        action_id = str(snapshot.get("action_id") or "")
    signature = metric_signature(bet.get("expected_effects") or ())
    components = class_components(project_id=project_id, action_id=action_id,
                                 metric_signature=signature)
    stored = class_key_from_bet(bet)
    rebuilt = task_class_key(**components) if action_id else ""
    return {**components, "stored_class_key": stored, "rebuilt_class_key": rebuilt,
            "verified": bool(stored) and rebuilt == stored}


def similarity_weight(own: Mapping[str, Any], other: Mapping[str, Any]) -> float:
    """How similar two classes are, by the declared components only.  Pure, no LLM.

    Same action id -> RELATED_WEIGHT_SAME_ACTION, same metric signature ->
    RELATED_WEIGHT_SAME_METRIC, same project -> RELATED_WEIGHT_SAME_PROJECT.  Two matching
    components take the highest of them (they never add), three matching components mean
    the same class (1.0) and are therefore not a *related* source at all.
    """
    if not own or not other:
        return 0.0
    matches: list[float] = []
    if own.get("action_id") and own.get("action_id") == other.get("action_id"):
        matches.append(RELATED_WEIGHT_SAME_ACTION)
    if (own.get("metric_signature")
            and own.get("metric_signature") == other.get("metric_signature")):
        matches.append(RELATED_WEIGHT_SAME_METRIC)
    if own.get("project_id") and own.get("project_id") == other.get("project_id"):
        matches.append(RELATED_WEIGHT_SAME_PROJECT)
    if len(matches) == 3:
        return 1.0
    return max(matches) if matches else 0.0


def similarity_reasons(own: Mapping[str, Any], other: Mapping[str, Any]) -> list[str]:
    reasons = []
    if own.get("action_id") and own.get("action_id") == other.get("action_id"):
        reasons.append("same_action_id")
    if own.get("metric_signature") and own.get("metric_signature") == other.get("metric_signature"):
        reasons.append("same_metric_signature")
    if own.get("project_id") and own.get("project_id") == other.get("project_id"):
        reasons.append("same_project_id")
    return reasons


def _known_class_components(all_known: Any) -> dict[str, dict[str, Any]]:
    """Normalise the caller's view of known classes into ``{class_key: components}``.

    Accepts ``{key: components}`` or ``{key: {"components": ..., "verified": bool}}`` (or a
    sequence of either shape).  A bare key carries no components -- and a key is a hash, so
    its similarity cannot be judged: those entries are dropped instead of guessed.
    """
    out: dict[str, dict[str, Any]] = {}
    items = all_known.items() if isinstance(all_known, Mapping) else \
        ((str(k), v) for k, v in (all_known or ()))
    for key, value in items:
        key = str(key)
        if isinstance(value, Mapping) and isinstance(value.get("components"), Mapping):
            components = dict(value["components"])
            verified = bool(value.get("verified", True))
        elif isinstance(value, Mapping):
            components, verified = dict(value), True
        else:
            continue
        if not components:
            continue
        out[key] = {**components, "verified": verified}
    return out


def related_class_keys(class_key: str, all_known_class_keys: Any, *,
                       components: Mapping[str, Any] | None = None
                       ) -> list[tuple[str, float]]:
    """The related classes of ``class_key``, with their similarity weights.

    Returns ``[(related_key, weight), ...]`` sorted by weight (descending) and then by key,
    so the list is stable and auditable.  The direct class itself is never included: three
    matching components mean "same class", which is not a related source.  An empty result
    is a normal answer.
    """
    known = _known_class_components(all_known_class_keys)
    own = dict(components or {})
    if not own:
        own = {k: v for k, v in (known.get(str(class_key)) or {}).items() if k != "verified"}
    if not own:
        return []
    pairs: list[tuple[str, float]] = []
    for candidate, info in known.items():
        if candidate == str(class_key) or not info.get("verified", True):
            continue
        weight = similarity_weight(own, info)
        if 0.0 < weight < DIRECT_WEIGHT:
            pairs.append((candidate, weight))
    pairs.sort(key=lambda pair: (-pair[1], pair[0]))
    return pairs


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
            rebuilt = bet_class_components(bet, snapshot)
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
                # Rebuilt from disk so a *related* class can be judged by its declared
                # components.  ``verified`` is True only when the rebuild reproduces the
                # stored class key: an unverifiable row is never borrowed as other-class
                # evidence.
                "class_components": {k: rebuilt[k] for k in
                                     ("project_id", "action_id", "metric_signature")},
                "class_components_verified": bool(rebuilt["verified"]),
                # An exploration bet marks itself; the window rule counts these.
                "exploration": bool(snapshot.get("exploration")),
                "variant_id": str(snapshot.get("variant_id") or ""),
            })
    rows.sort(key=lambda r: (r["created_at"], r["bet_id"]))
    return rows


def select_same_class(rows: Sequence[Mapping[str, Any]], class_key: str) -> list[dict[str, Any]]:
    """Only the rows whose frozen class key equals ``class_key`` -- never the rest."""
    if not class_key:
        return []
    return [dict(r) for r in rows if str(r.get("class_key")) == class_key]


def _class_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {name: 0 for name in SETTLEMENT_CLASSES}
    for row in rows:
        cls = str(row.get("settlement_class") or "")
        if cls in REFUTED_ALIASES:
            counts["falsified"] += 1
        elif cls in counts:
            counts[cls] += 1
    total = len(rows)
    evidence_total = max(total - counts["abstained"], 0)
    counts.update({"total": total, "evidence_total": evidence_total,
                   "refuted_ratio": round((counts["falsified"] / evidence_total)
                                          if evidence_total else 0.0, 6),
                   "improvement_true": sum(1 for r in rows if r.get("improvement_over_baseline")),
                   "improvement_false": sum(1 for r in rows if not r.get("improvement_over_baseline")),
                   "single_episode_only": sum(1 for r in rows
                                              if BLOCKER_SINGLE_EPISODE
                                              in (r.get("publish_blockers") or []))})
    return counts


def _related_pairs(related: Mapping[str, Any]) -> list[list[Any]]:
    """``[[class_key, weight], ...]`` for the audit, in the summary's stable order."""
    return [[str(c.get("class_key")), float(c.get("weight") or 0.0)]
            for c in (related.get("classes") or [])]


def effective_counts(prior: Mapping[str, Any]) -> dict[str, Any]:
    """The counts the rules must apply.

    Direct-only when the same class carried enough evidence; when related classes were
    consulted, the *weighted* view -- direct rows at weight 1.0 plus every related row at
    its similarity weight.  The two views are both kept in the prior, so an auditor can
    always see what the direct history said on its own.
    """
    counts = dict((prior or {}).get("counts") or {})
    related = dict((prior or {}).get("related") or {})
    if related.get("used"):
        merged = dict(counts)
        for key, value in dict((prior or {}).get("weighted") or {}).items():
            merged[key] = value
        return merged
    return counts


def summarize_prior(rows: Sequence[Mapping[str, Any]], *, class_key: str,
                    components: Mapping[str, str] | None = None,
                    related: Mapping[str, Mapping[str, Any]] | None = None,
                    suppress_abstention: str = "",
                    ) -> dict[str, Any]:
    """The prior: the same-class rows, every related class' rows, and machine-counted
    statistics in both views (direct and weight-adjusted).

    Related evidence is only ever *added*: the direct rows keep weight 1.0 and are never
    replaced, rescaled or dropped.  The related path opens only when the direct class does
    not carry enough evidence of its own (:data:`ABSTAIN_MIN_EVIDENCE`).
    """
    counts = _class_counts(rows)
    total = counts["total"]
    evidence_total = counts["evidence_total"]
    refuted_ratio = counts["refuted_ratio"]
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
        "counts": {**counts, "refuted_ratio_threshold": REFUTED_RATIO_THRESHOLD},
        "rows": supporting,
        "sources": [str(r.get("settlement_ref") or "") for r in rows],
    }
    # -- related classes: additive only, and only when the direct class is thin -------
    related_map = dict(related or {})
    related_classes: list[dict[str, Any]] = []
    contributing: list[dict[str, Any]] = [
        {**row, "_weight": DIRECT_WEIGHT, "_source": "direct"} for row in rows]
    weighted: dict[str, float] = {name: float(counts[name]) for name in SETTLEMENT_CLASSES}
    for name in ("total", "evidence_total", "improvement_true", "improvement_false",
                 "single_episode_only"):
        weighted[name] = float(counts[name])
    for key in sorted(related_map):
        info = dict(related_map[key] or {})
        weight = float(info.get("weight") or 0.0)
        if weight <= 0.0 or weight >= DIRECT_WEIGHT:
            continue
        class_rows = [dict(row) for row in (info.get("rows") or [])]
        class_counts = _class_counts(class_rows)
        for name in SETTLEMENT_CLASSES:
            weighted[name] += weight * float(class_counts[name])
        for name in ("total", "evidence_total", "improvement_true", "improvement_false",
                     "single_episode_only"):
            weighted[name] += weight * float(class_counts[name])
        contributing.extend({**row, "_weight": weight, "_source": f"related:{key}"}
                            for row in class_rows)
        related_classes.append({
            "class_key": str(key), "weight": weight,
            "reasons": [str(r) for r in (info.get("reasons") or [])],
            "components": dict(info.get("components") or {}),
            "row_count": int(class_counts["total"]), "counts": class_counts,
            "bet_ids": [str(r.get("bet_id") or "") for r in class_rows]})
    weighted["evidence_total"] = max(weighted["evidence_total"], 0.0)
    weighted["refuted_ratio"] = round(
        (weighted["falsified"] / weighted["evidence_total"])
        if weighted["evidence_total"] else 0.0, 6)
    triggered = evidence_total < ABSTAIN_MIN_EVIDENCE
    used = bool(triggered and related_classes)
    # The tail the abstention rule reads must belong to the view that was *applied*: with
    # direct-only evidence, a related class' latest settlement has no business deciding
    # whether this class may decline to wager.
    tail_rows = ([{**row, "_weight": DIRECT_WEIGHT, "_source": "direct"} for row in rows]
                 if not used else contributing)
    ordered = sorted(tail_rows, key=lambda row: (str(row.get("settled_at") or ""),
                                                 float(row.get("settled_epoch") or 0.0)))
    latest = dict(ordered[-1]) if ordered else {}
    summary["related"] = {
        "triggered": triggered, "used": used,
        "reason": ("related_class_history_used" if used else
                   ("no_related_class_history" if triggered else "direct_evidence_sufficient")),
        "direct_evidence_total": int(evidence_total),
        "direct_weight": DIRECT_WEIGHT,
        "min_direct_evidence": ABSTAIN_MIN_EVIDENCE,
        "classes": related_classes,
        "sorted_by": "weight_desc_then_class_key",
        "weights": {"same_action_id": RELATED_WEIGHT_SAME_ACTION,
                    "same_metric_signature": RELATED_WEIGHT_SAME_METRIC,
                    "same_project_id": RELATED_WEIGHT_SAME_PROJECT},
    }
    summary["weighted"] = weighted
    summary["applied_from"] = "weighted" if used else "direct"
    # ``empty`` means "no evidence was available from anywhere".  A class with an empty
    # *direct* history but borrowed related evidence is not empty: the weighted view below
    # carries real settlements.  ``row_count`` still counts the direct rows only, and
    # ``related.classes`` reports what was borrowed.
    summary["empty"] = bool(not rows and not related_classes)
    summary["related_row_count"] = sum(len(c.get("bet_ids") or []) for c in related_classes)
    summary["tail"] = {
        "tail_from": "weighted" if used else "direct",
        "settlement_class": str(latest.get("settlement_class") or ""),
        "bet_id": str(latest.get("bet_id") or ""),
        "class_key": str(latest.get("class_key") or class_key),
        "source": str(latest.get("_source") or ""),
        "weight": float(latest.get("_weight") or 0.0),
    }
    # The abstention decision is part of the prior, not a separate later step: it is frozen
    # into ``prior.json`` (and from there into the bet's context snapshot) with the counts
    # and the reason it was taken or refused.
    summary["abstention"] = decide_abstention(summary, suppress_reason=suppress_abstention)
    return summary


def decide_abstention(prior: Mapping[str, Any], *,
                      suppress_reason: str = "") -> dict[str, Any]:
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
    counts = effective_counts(prior)
    rows = [dict(row) for row in (prior.get("rows") or [])]
    if str(suppress_reason or ""):
        # One caller may refuse to let this bet abstain: an *exploration* bet exists to
        # produce new evidence for a class that has none, so declining to wager on it would
        # defeat the only way out of the dead end.  The refusal is recorded, not hidden --
        # the class statistics that would have triggered the abstention are still reported.
        return {"abstain": False, "rule": "", "blocked_by": [str(suppress_reason)],
                "reason": f"abstention suppressed for this bet: {suppress_reason}",
                "suppressed_for": str(suppress_reason),
                "evidence": {"suppressed_for": str(suppress_reason),
                             "evidence_total": counts.get("evidence_total"),
                             "falsified": counts.get("falsified"),
                             "supported": counts.get("supported"),
                             "refuted_ratio": counts.get("refuted_ratio"),
                             "would_have_abstained": True}}
    abstained = float(counts.get("abstained") or 0.0)
    total = float(counts.get("total") or 0.0)
    evidence_total = float(counts.get("evidence_total") if counts.get("evidence_total") is not None
                           else max(total - abstained, 0.0))
    refuted = float(counts.get("falsified") or 0.0)
    supported = float(counts.get("supported") or 0.0)
    ratio = (refuted / evidence_total) if evidence_total else 0.0
    # The tail is the most recent *contributing* settlement: the direct rows, plus the
    # related rows when they were borrowed.  ``summarize_prior`` records it; a caller who
    # built the prior by hand falls back to the direct rows.
    tail = dict(prior.get("tail") or {})
    if tail:
        latest = tail
    else:
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
    counts = effective_counts(prior or {})
    direct_counts = dict((prior or {}).get("counts") or {})
    related = dict((prior or {}).get("related") or {})
    weighted = dict((prior or {}).get("weighted") or {})
    applied_from = str((prior or {}).get("applied_from") or "direct")
    class_key = str((prior or {}).get("class_key") or "")
    rows = list((prior or {}).get("rows") or [])
    parameters: list[dict[str, Any]] = []
    # "Nothing to adjust from" is judged on the *effective* evidence: an empty direct class
    # whose evidence was borrowed from related classes is not empty, and returning early
    # here would silently discard exactly the evidence the related path exists to bring in.
    available = (float(counts.get("evidence_total") or 0.0)
                 + float(counts.get("abstained") or 0.0))
    if available <= 0.0:
        return adjusted, {"prior_adjusted": False, "prior_class_key": class_key,
                          "prior_row_count": 0, "parameters": [],
                          "reason": "prior_empty", "prior_version": PRIOR_VERSION,
                          "applied_from": applied_from,
                          "used_direct_only": applied_from == "direct",
                          "direct_class_evidence": direct_counts,
                          "related_class_keys": ([] if applied_from == "direct" else
                                                 _related_pairs(related)),
                          "related_class_keys_considered": _related_pairs(related),
                          "weighted_evidence": dict(weighted),
                          "applied_evidence": dict(counts),
                          "adjustment_rule": "none",
                          "tail": dict((prior or {}).get("tail") or {})}
    base = adjusted["min_delta"]
    # ``float`` on purpose: with related classes the counts are weighted shares, and
    # truncating them to int would quietly drop borrowed evidence.
    refuted = float(counts.get("falsified") or 0.0)
    supported = float(counts.get("supported") or 0.0)
    total = float(counts.get("total") or 0.0)
    ratio = float(counts.get("refuted_ratio") or 0.0)
    # -- priority: a refuted-dominated class outranks a supported one.  The bar goes up
    # whenever refuted/total >= REFUTED_RATIO_THRESHOLD (ties included); the relaxing
    # rule below only applies when the class is NOT refuted-dominated.
    # "any evidence at all", not ">= 1 settlement": under the weighted view a borrowed row
    # contributes a fraction (one related row at weight 0.5 is 0.5 of a settlement), and a
    # guard of ``total >= 1`` would silently discard exactly that evidence.
    if total > 0.0 and ratio >= REFUTED_RATIO_THRESHOLD:
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
    if float(counts.get("improvement_false") or 0) > 0 and not adjusted["require_baseline_rerun"]:
        adjusted["require_baseline_rerun"] = True
        parameters.append({
            "name": "require_baseline_rerun", "before": bool(declared.get("require_baseline_rerun")),
            "after": True, "rule": "improvement_false_forces_a_real_baseline",
            "evidence": {"improvement_false": float(counts.get("improvement_false") or 0.0)},
            "why": "a same-class settlement did not beat its baseline"})
    if float(counts.get("single_episode_only") or 0) > 0 and adjusted["replicates"] < 2:
        adjusted["replicates"] = 2
        parameters.append({
            "name": "replicates", "before": int(declared.get("replicates") or 1), "after": 2,
            "rule": "single_episode_blocker_adds_one_executed_repetition",
            "evidence": {"single_episode_only": float(counts.get("single_episode_only") or 0.0)},
            "why": "a same-class settlement was blocked as single_episode_only"})
    audit = {"prior_adjusted": bool(parameters), "prior_class_key": class_key,
             "prior_row_count": int(counts.get("total") or 0), "parameters": parameters,
             "reason": "prior_applied" if parameters else "prior_present_no_rule_triggered",
             "prior_version": PRIOR_VERSION,
             "prior_bet_ids": [r.get("bet_id") for r in rows],
             # -- what was applied, and where every number came from -------------------
             "applied_from": applied_from,
             "used_direct_only": applied_from == "direct",
             "direct_class_evidence": direct_counts,
             # ``related_class_keys`` names only the classes whose evidence was *applied*
             # (empty when the direct class was sufficient); the full list that was
             # considered is kept next to it for the audit.
             "related_class_keys": ([] if applied_from == "direct" else
                                    _related_pairs(related)),
             "related_class_keys_considered": _related_pairs(related),
             "related_class_reasons": {str(c.get("class_key")): list(c.get("reasons") or [])
                                       for c in (related.get("classes") or [])},
             "related_evidence": {"triggered": bool(related.get("triggered")),
                                  "used": bool(related.get("used")),
                                  "reason": str(related.get("reason") or ""),
                                  "classes": list(related.get("classes") or [])},
             "weighted_evidence": dict(weighted),
             "applied_evidence": dict(counts),
             "adjustment_rule": (parameters[0]["rule"] if parameters else "none"),
             "tail": dict((prior or {}).get("tail") or {})}
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
