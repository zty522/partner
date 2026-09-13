"""Small, governed production surface for autonomously generated policies.

Only the bounded candidate synthesizer may add entries to ``POLICIES``.  The
rest of Partner consumes this module as ordinary code, which makes every
candidate diff reviewable, testable and reversible without allowing an LLM to
rewrite arbitrary runtime modules.
"""
from __future__ import annotations

from typing import Any


POLICIES: dict[str, str] = {
    "literature_github_learning": "turn_source_rotation_v1",
}


def enrich_project_params(project_id: str, strategy_id: str, native_turn: int,
                          params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    recipe = POLICIES.get(project_id, "")
    if recipe == "duplicate_forces_verified_maxmin_v2":
        result["candidate_recipe"] = recipe
        # Fresh seeds supported MaxMin under the same-data fingerprint/QED
        # contract while scaffold round-robin was falsified. Keep the narrower
        # supported intervention; replicate_seed still changes across turns.
        result["candidate_variant"] = 3
        result["force_method_family_expansion"] = True
    elif recipe == "turn_parameter_sweep_v1":
        # The variant is deterministic/replayable but differs across turns.
        # Domain Events decide how it maps to safe numerical parameters.
        result["candidate_recipe"] = recipe
        result.setdefault("candidate_variant", max(1, int(native_turn)))
    elif recipe == "turn_source_rotation_v1":
        result["candidate_recipe"] = recipe
        result.setdefault("source_variant", max(1, int(native_turn)))
    elif recipe == "turn_code_surface_rotation_v1":
        result["candidate_recipe"] = recipe
        result.setdefault("code_variant", max(1, int(native_turn)))
    return result
