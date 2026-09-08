"""BDK ocamms promotion guard for Partner 05 evolution loop.

This module is a pre-promo hook that 05's `decide_experiment` (or any other
promotion path) can call before allowing a candidate to be promoted.  The
guard:

  1. Reads the candidate skill record from the registry.
  2. Parses its `intervention` field as JSON.
  3. If the intervention declares BDK FunctionPool kernel_logits/probs,
     runs `apply_ocamms_constraint` on them.
  4. Returns a verdict dict: allowed / blocked / not_applicable.

The guard never mutates state.  It is purely a check that 05 must consult
explicitly.  Promotion still flows through `decide_experiment`; this module
just adds an extra hard gate.

Honesty:
    - This is opt-in: callers must call `bdk_ocamms_promotion_guard()` and
      check the verdict before calling `decide_experiment` with
      decision="promoted".
    - If the intervention is not a BDK JSON, the guard returns
      `not_applicable=True` and lets the caller decide.  We do not block
      promotion of non-BDK skills.
    - The guard never produces a `promotion_decision_id` of its own.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np  # noqa: E402
from partner.learn.bdk_constraints import (
    apply_ocamms_constraint,
    apply_ocamms_constraint_with_advice,
)

from partner.governance.candidate_skills import load_candidate_skills  # noqa: E402
from partner.governance.storage import workspace_root  # noqa: E402


def _load_candidate(workspace: str, candidate_id: str) -> dict[str, Any] | None:
    """Find the candidate skill record by id (latest version)."""
    skills = load_candidate_skills(workspace)
    for skill in skills:
        if str(skill.get("candidate_id") or "") == candidate_id:
            return skill
    return None


def _extract_kernel_logits(candidate: dict[str, Any]) -> tuple[list[float] | None, str]:
    """Extract BDK kernel logits from a candidate's intervention field.

    Returns (logits, reason).  reason is non-empty when logits is None.
    """
    intervention = candidate.get("intervention")
    if not intervention:
        return None, "candidate has no intervention field"

    # intervention may be a JSON string (most common) or a dict.
    if isinstance(intervention, str):
        try:
            intervention = json.loads(intervention)
        except (TypeError, ValueError) as exc:
            return None, f"intervention is not valid JSON: {exc}"

    if not isinstance(intervention, dict):
        return None, "intervention is not a dict"

    # Only intervene when the intervention declares a BDK module we recognise.
    bdk_module = str(intervention.get("bdk_module") or "")
    if "bdk" not in bdk_module.lower():
        return None, f"intervention bdk_module={bdk_module!r} is not a BDK module"

    # Prefer kernel_probs (already softmax-normalised) over logits.
    probs = intervention.get("kernel_probs")
    if isinstance(probs, list) and probs:
        return [float(p) for p in probs], "using kernel_probs"
    logits = intervention.get("kernel_logits")
    if isinstance(logits, list) and logits:
        return [float(p) for p in logits], "using kernel_logits"
    return None, "intervention has no kernel_probs or kernel_logits"


def bdk_ocamms_promotion_guard(
    workspace: str,
    candidate_id: str,
    *,
    margin: float = 0.20,
    max_activated: int = 2,
) -> dict[str, Any]:
    """Check whether `candidate_id` can be promoted under BDK ocamms.

    Returns a verdict dict (always populated):
      - not_applicable: True if the candidate is not BDK-related
      - allowed: True if the BDK kernel distribution passes ocamms (or
                 not_applicable)
      - blocked: True if the candidate is BDK-related AND fails ocamms
      - reason: human-readable explanation
      - audit: structured detail (kernel probs, n_activated, activated_names)

    Caller's responsibility: do not promote if verdict["blocked"] is True.
    """
    candidate = _load_candidate(workspace, candidate_id)
    if candidate is None:
        return {
            "candidate_id": candidate_id,
            "not_applicable": True,
            "allowed": False,  # We can't allow promotion of an unknown candidate.
            "blocked": True,
            "reason": f"candidate_id {candidate_id!r} not found in registry",
            "audit": None,
        }

    logits, reason = _extract_kernel_logits(candidate)
    if logits is None:
        return {
            "candidate_id": candidate_id,
            "not_applicable": True,
            "allowed": True,  # don't block non-BDK skills
            "blocked": False,
            "reason": reason,
            "audit": None,
        }

    # Hand whatever we extracted (logits or probs) directly to ocamms.
    # ocamms has its own normalisation logic: if sum==1 and all values in
    # [0,1] it treats the input as probabilities; otherwise it softmaxes.
    # We do NOT pre-softmax because ocamms treats already-normalised
    # probabilities correctly, and double-softmax would distort the result.
    passed, suggested_probs, message = apply_ocamms_constraint_with_advice(
        logits, margin=margin, max_activated=max_activated
    )
    # Determine activated kernel names from the candidate record if available
    candidate_intervention = candidate.get("intervention")
    if isinstance(candidate_intervention, str):
        candidate_intervention = json.loads(candidate_intervention)
    kernel_names = candidate_intervention.get("kernel_names") or []
    kernel_probs = candidate_intervention.get("kernel_probs") or logits
    activated_names = [
        name for name, p in zip(kernel_names, kernel_probs) if p > margin
    ]
    audit = {
        "margin": margin,
        "max_activated": max_activated,
        "kernel_probs": [float(p) for p in kernel_probs],
        "kernel_names": list(kernel_names),
        "activated_names": activated_names,
        "n_activated": len(activated_names),
        "ocamms_message": message,
        "suggested_probs": [float(p) for p in suggested_probs] if suggested_probs is not None else None,
    }

    return {
        "candidate_id": candidate_id,
        "not_applicable": False,
        "allowed": passed,
        "blocked": not passed,
        "reason": (
            f"BDK ocamms {'passed' if passed else 'failed'}: "
            f"{len(activated_names)} activated kernels > max_activated={max_activated}"
            if not passed else
            f"BDK ocamms passed: {len(activated_names)} activated kernels"
        ),
        "audit": audit,
    }


if __name__ == "__main__":  # pragma: no cover
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--workspace", required=True)
    p.add_argument("--candidate-id", required=True)
    p.add_argument("--margin", type=float, default=0.20)
    p.add_argument("--max-activated", type=int, default=2)
    args = p.parse_args()
    out = bdk_ocamms_promotion_guard(
        args.workspace, args.candidate_id,
        margin=args.margin, max_activated=args.max_activated,
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
