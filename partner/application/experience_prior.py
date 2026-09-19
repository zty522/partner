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

1. any ``refuted`` settlement in the class -> ``min_delta = base * (1 + refuted)``
   (the class has burned evidence; raise the bar)
2. else any ``supported`` -> ``min_delta = base / 2**supported`` floor ``MIN_DELTA_FLOOR``
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

SETTLEMENT_CLASSES = ("supported", "refuted", "inconclusive", "blocked")


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
                "settled_at": str(settlement.get("settled_at") or ""),
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
        if cls in counts:
            counts[cls] += 1
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
    return {
        "prior_version": PRIOR_VERSION,
        "class_key": str(class_key),
        "class_definition": dict(components or {}),
        "empty": not rows,
        "row_count": len(rows),
        "counts": {
            "total": len(rows),
            **counts,
            "improvement_true": sum(1 for r in rows if r.get("improvement_over_baseline")),
            "improvement_false": sum(1 for r in rows if not r.get("improvement_over_baseline")),
            "single_episode_only": sum(1 for r in rows
                                       if BLOCKER_SINGLE_EPISODE in (r.get("publish_blockers") or [])),
        },
        "rows": supporting,
        "sources": [str(r.get("settlement_ref") or "") for r in rows],
    }


def empty_prior(*, class_key: str, components: Mapping[str, str] | None = None,
                reason: str = "prior_empty") -> dict[str, Any]:
    """An explicitly empty prior.  It carries no value for anything."""
    return {"prior_version": PRIOR_VERSION, "class_key": str(class_key),
            "class_definition": dict(components or {}), "empty": True, "row_count": 0,
            "counts": {"total": 0, **{name: 0 for name in SETTLEMENT_CLASSES},
                       "improvement_true": 0, "improvement_false": 0,
                       "single_episode_only": 0},
            "rows": [], "sources": [], "reason": reason}


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
    refuted = int(counts.get("refuted") or 0)
    supported = int(counts.get("supported") or 0)
    if refuted > 0:
        after = base * (1 + refuted)
        adjusted["min_delta"] = after
        parameters.append({
            "name": "min_delta", "before": base, "after": after,
            "rule": "refuted_history_raises_the_bar",
            "evidence": {"refuted": refuted, "supported": supported},
            "why": f"{refuted} refuted settlement(s) in this class"})
    elif supported > 0 and base > 0:
        # only when there is a bar to lower: a declared 0.0 is already at the floor, and
        # "lowering" it to the floor would silently tighten the bet instead
        after = max(MIN_DELTA_FLOOR, base / (2 ** supported))
        if after < base:
            adjusted["min_delta"] = after
            parameters.append({
                "name": "min_delta", "before": base, "after": after,
                "rule": "supported_history_lowers_the_bar",
                "evidence": {"supported": supported, "floor": MIN_DELTA_FLOOR},
                "why": f"{supported} supported settlement(s) in this class"})
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
           "MIN_DELTA_FLOOR", "BLOCKER_SINGLE_EPISODE", "SETTLEMENT_CLASSES",
           "metric_signature", "task_class_key", "class_key_from_bet", "class_components",
           "commitments_root", "scan_settled_bets", "select_same_class", "summarize_prior",
           "empty_prior", "adjust_declared", "prior_path", "write_prior", "read_prior",
           "clear_prior"]
