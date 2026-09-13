"""Orthogonal completion semantics for every Partner work item.

Execution, verification, notification and production activation are different
facts.  In particular, a deliberately quiet background Event must not become
an execution failure merely because no user-channel acknowledgement exists.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class OutcomeDimensions:
    execution: str
    verification: str
    notification: str
    publication: str
    work_accepted: bool
    user_delivery_satisfied: bool
    background: bool
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_outcome(
    *,
    execution_ok: bool,
    verification_ok: bool,
    has_artifacts: bool,
    delivery_confirmed: bool,
    local_observation_confirmed: bool,
    background: bool,
    requires_user_delivery: bool,
    production_effective: bool = False,
    publication_attempted: bool = False,
) -> OutcomeDimensions:
    """Return one deterministic, non-overloaded completion contract."""
    execution = "succeeded" if execution_ok else "failed"
    verification = (
        "accepted" if execution_ok and verification_ok
        else "rejected" if execution_ok
        else "not_run"
    )
    if delivery_confirmed:
        notification = "acknowledged"
    elif background and local_observation_confirmed:
        notification = "skipped_by_policy"
    elif requires_user_delivery and has_artifacts:
        notification = "failed"
    else:
        notification = "not_required"
    if production_effective:
        publication = "effective"
    elif publication_attempted:
        publication = "rejected"
    else:
        publication = "not_requested"
    work_accepted = bool(execution_ok and verification_ok and (
        not requires_user_delivery or delivery_confirmed or (
            background and local_observation_confirmed
        )
    ))
    return OutcomeDimensions(
        execution=execution,
        verification=verification,
        notification=notification,
        publication=publication,
        work_accepted=work_accepted,
        user_delivery_satisfied=bool(delivery_confirmed or not requires_user_delivery),
        background=bool(background),
    )
