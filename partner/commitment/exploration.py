"""Bounded exploration after an abstention.

An abstention says: this class carries only failure evidence, there is no machine-derivable
next step, and the harness declines to wager again.  That must not be a dead end.  The
escape hatch is *bounded exploration*: try ONE action variant of the same class that no
settled bet has tried yet, under its own smaller budget, and let the resulting settlement
count as ordinary same-class evidence for every later decision.

Everything here is pure Python and deterministic -- the variant comes from the task's
declared candidate space, never from a model, and every threshold is a module constant.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

EXPLORATION_VERSION = "exploration/1"

#: An exploration gets a fraction of the declared budget.  It is deliberately too small to
#: run a full search: the point is one cheap piece of new evidence, not a second campaign.
EXPLORE_BUDGET_RATIO = 0.25
#: ... floored at what a single bounded action actually needs: two arms (control and
#: candidate), one round, one model call.  A budget below this cannot produce evidence and
#: is therefore treated as no budget at all.
EXPLORE_MIN_ACTIONS = 2
EXPLORE_MIN_ROUNDS = 1
EXPLORE_MIN_MODEL_CALLS = 1
#: At most this many explorations per class per window, and never while one is pending.
EXPLORE_MAX_PER_WINDOW = 1
EXPLORE_WINDOW_SECONDS = 7200

#: Why an exploration was not triggered.  Declared, recorded, and never silently dropped.
EXPLORE_TRIGGERED = "exploration_triggered"
EXPLORE_SKIPPED_DISABLED = "explore_disabled"
EXPLORE_SKIPPED_NO_BUDGET = "no_budget"
EXPLORE_SKIPPED_PENDING = "explore_already_pending"
EXPLORE_SKIPPED_WINDOW_LIMIT = "window_limit"
EXPLORE_SKIPPED_NO_VARIANT = "explore_no_variant_available"

#: The exploration job is recognisable on the timeline and in the store: by its token
#: prefix and by the channel it was submitted on.  The token keeps the declared shape
#: ``explore_<class_key>_<ts>`` but carries the literal ``trace`` that the repo's trace
#: matcher requires -- without it the exploration's own flow would fail with "no trace token
#: found in the triggering message" and the exploration could never be recorded.
EXPLORE_TOKEN_PREFIX = "explore_trace_"
#: The exploration is submitted on a channel the delivery layer actually supports
#: (``partner.events.delivery.channel_route`` accepts qq/gui/tui/local/log/file); a novel
#: channel name makes the exploration's own flow fail at its send node with "unsupported
#: delivery channel" -- after the bet has already settled.  The exploration is therefore
#: identified by its ``sender_id`` prefix, not by a private channel.
EXPLORE_CHANNEL = "log"
EXPLORE_SENDER_PREFIX = "commitment_explore:"
#: The delivery channels the flow may send on (kept in sync with channel_route).
DELIVERY_CHANNELS = ("qq", "gui", "tui", "local", "log", "file")
#: The Event-flow node that may decline to wager is asked not to: an exploration bet exists
#: to produce evidence, so abstaining on it would defeat the only way out of the dead end.
EXPLORE_ABSTENTION_SUPPRESSED = "exploration_bet_declines_no_wager"


def _int_or(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_or(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def exploration_budget(budget: Mapping[str, Any] | None, *,
                       ratio: float = EXPLORE_BUDGET_RATIO) -> dict[str, Any]:
    """The exploration budget: a fraction of the declared one, floored at one action.

    Never borrowed from: the declared budget of the bet that triggered the exploration is
    not touched, so an exploration cannot erode the wager it was born from.
    """
    declared = dict(budget or {})
    wall = _int_or(declared.get("wall_clock_seconds"), 0)
    actions = _int_or(declared.get("actions"), 0)
    calls = _int_or(declared.get("model_calls"), 0)
    rounds = _int_or(declared.get("rounds"), 0)
    return {
        "wall_clock_seconds": int(math.floor(wall * float(ratio))),
        "model_calls": max(EXPLORE_MIN_MODEL_CALLS, int(math.floor(calls * float(ratio)))),
        "actions": max(EXPLORE_MIN_ACTIONS, int(math.floor(actions * float(ratio)))),
        "rounds": max(EXPLORE_MIN_ROUNDS, int(math.floor(rounds * float(ratio)))),
        "ratio": float(ratio), "declared": declared,
    }


def explore_trace_token(class_key: str, *, epoch: float) -> str:
    """``explore_<class_key>_<epoch>`` -- the shape the round declares."""
    return f"{EXPLORE_TOKEN_PREFIX}{str(class_key or 'unknown')}_{int(float(epoch))}"


def is_exploration(*, trace_token: str = "", exploration: Any = False) -> bool:
    return bool(exploration) or str(trace_token or "").startswith(EXPLORE_TOKEN_PREFIX)


def tried_actions(rows: Iterable[Mapping[str, Any]], *, class_key: str) -> list[str]:
    """Every action the class has already put to the test (sorted, deduplicated)."""
    return sorted({str(row.get("selected_action") or "") for row in rows
                   if str(row.get("class_key") or "") == str(class_key)
                   and str(row.get("selected_action") or "")})


def choose_variant(candidates: Sequence[Mapping[str, Any]],
                   tried: Iterable[str]) -> dict[str, Any] | None:
    """The first declared variant whose action the class has not tried yet, in declared order.

    ``None`` means there is nothing new to try: an exploration must not repeat an action that
    already produced evidence, and it must never invent one.
    """
    done = {str(action) for action in tried}
    for candidate in candidates or ():
        if not isinstance(candidate, Mapping):
            continue
        variant = dict(candidate)
        if str(variant.get("variant_id") or "").strip() and \
                str(variant.get("selected_action") or "") not in done:
            return variant
    return None


def exploration_window(rows: Iterable[Mapping[str, Any]], *, class_key: str, now: float,
                       window_seconds: int = EXPLORE_WINDOW_SECONDS,
                       max_per_window: int = EXPLORE_MAX_PER_WINDOW) -> dict[str, Any]:
    """How much exploration this class has already had inside the window."""
    inside = []
    for row in rows:
        if str(row.get("class_key") or "") != str(class_key):
            continue
        if not is_exploration(trace_token=str(row.get("trace_token") or ""),
                              exploration=row.get("exploration")):
            continue
        if float(row.get("settled_epoch") or 0.0) >= float(now) - float(window_seconds):
            inside.append(str(row.get("bet_id") or ""))
    return {"explored_in_window": len(inside), "bet_ids": sorted(inside),
            "window_seconds": int(window_seconds), "max_per_window": int(max_per_window),
            "now": float(now)}


def should_explore(*, enabled: bool = True, budget: Mapping[str, Any] | None = None,
                   explored_in_window: int = 0, pending: int = 0,
                   variant: Mapping[str, Any] | None = None,
                   max_per_window: int = EXPLORE_MAX_PER_WINDOW) -> dict[str, Any]:
    """Decide whether an abstention may trigger an exploration.

    Order is fixed so the recorded reason is stable: disabled, budget, already pending,
    window exhausted, no variant.
    """
    derived = exploration_budget(budget)
    detail = {"enabled": bool(enabled), "max_per_window": int(max_per_window),
              "explored_in_window": int(explored_in_window), "pending": int(pending),
              "budget": derived}
    if not enabled:
        return {"explore": False, "reason": EXPLORE_SKIPPED_DISABLED, "detail": detail}
    if derived["wall_clock_seconds"] <= 0 or derived["actions"] < EXPLORE_MIN_ACTIONS:
        return {"explore": False, "reason": EXPLORE_SKIPPED_NO_BUDGET, "detail": detail}
    if int(pending) > 0:
        return {"explore": False, "reason": EXPLORE_SKIPPED_PENDING, "detail": detail}
    if int(explored_in_window) >= int(max_per_window):
        return {"explore": False, "reason": EXPLORE_SKIPPED_WINDOW_LIMIT, "detail": detail}
    if not variant:
        return {"explore": False, "reason": EXPLORE_SKIPPED_NO_VARIANT,
                "detail": {**detail, "declared_variants": 0}}
    return {"explore": True, "reason": EXPLORE_TRIGGERED,
            "detail": {**detail, "variant": dict(variant)}}


def exploration_spec(*, class_key: str, budget: Mapping[str, Any] | None,
                     variant: Mapping[str, Any], trace_token: str,
                     now: float) -> dict[str, Any]:
    """The frozen audit record of one exploration decision."""
    return {"schema_version": EXPLORATION_VERSION, "class_key": str(class_key),
            "trace_token": str(trace_token), "variant": dict(variant or {}),
            "budget": exploration_budget(budget),
            "window": {"max_per_window": int(EXPLORE_MAX_PER_WINDOW),
                       "window_seconds": int(EXPLORE_WINDOW_SECONDS), "now": float(now)},
            "abstention_suppressed_for_exploration": EXPLORE_ABSTENTION_SUPPRESSED}
