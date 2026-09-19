"""Choosing exactly one direction, and recording what was abandoned.

A selection that does not name the alternatives it discarded is not auditable,
and an alternating loop that re-litigates the same choice is the behaviour the
commitment kernel exists to prevent.  Therefore:

* exactly one candidate is selected, always
* every other candidate gets an explicit reason
* a candidate with no declared prior cannot be selected (nothing to justify it)
* a refusal is a first-class outcome: it leaves the bet uncommitted rather than
  picking something arbitrary
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .models import Candidate, ContractError, RejectedAlternative, canonical_json


class SelectionRefused(RuntimeError):
    """No candidate satisfied the selection guardrails."""


@dataclass(frozen=True)
class CandidateScore:
    candidate_id: str
    expected_gain: float
    risk: float
    acceptable: bool
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {"candidate_id": self.candidate_id, "expected_gain": self.expected_gain,
                "risk": self.risk, "acceptable": self.acceptable, "note": self.note}


@dataclass(frozen=True)
class SelectionResult:
    selected: Candidate
    rejected: tuple[RejectedAlternative, ...]
    reason: str
    scores: tuple[CandidateScore, ...]
    rule: str

    def to_dict(self) -> dict[str, Any]:
        return {"selected": self.selected.to_dict(),
                "rejected": [r.to_dict() for r in self.rejected],
                "reason": self.reason,
                "scores": [s.to_dict() for s in self.scores], "rule": self.rule}


PRIOR_IN_PRIOR = "expected_gain"
PRIOR_KEY_RISK = "risk"


class GuardedGainSelector:
    """Deterministic, auditable selection under a risk guardrail.

    The prior lives in the frozen snapshot's declared candidate space, so the
    selector cannot be talked into a direction by a persuasive rationale: only
    declared numbers select.
    """

    rule = "max_expected_gain_under_risk_guardrail"

    def __init__(self, *, max_risk: float = 0.5) -> None:
        self.max_risk = float(max_risk)

    def select(self, *, candidates: Sequence[Candidate], snapshot: Mapping[str, Any]) -> SelectionResult:
        if not candidates:
            raise SelectionRefused("no candidates were proposed")
        priors = {str(e.get("candidate_id")): dict(e.get("prior") or {})
                  for e in (snapshot.get("candidate_space") or ())}
        scores: list[CandidateScore] = []
        for candidate in candidates:
            prior = priors.get(candidate.candidate_id)
            if not prior:
                raise SelectionRefused(
                    f"candidate {candidate.candidate_id!r} has no declared prior; refusing to "
                    "select a direction that cannot be justified from the frozen snapshot")
            if PRIOR_IN_PRIOR not in prior or PRIOR_KEY_RISK not in prior:
                raise SelectionRefused(
                    f"candidate {candidate.candidate_id!r} declares no {PRIOR_IN_PRIOR}/"
                    f"{PRIOR_KEY_RISK}; a direction without declared numbers cannot be selected")
            gain = float(prior[PRIOR_IN_PRIOR])
            risk = float(prior[PRIOR_KEY_RISK])
            acceptable = risk <= self.max_risk
            scores.append(CandidateScore(
                candidate.candidate_id, gain, risk, acceptable,
                "within risk guardrail" if acceptable
                else f"risk {risk} exceeds guardrail {self.max_risk}"))
        acceptable = [s for s in scores if s.acceptable]
        if not acceptable:
            raise SelectionRefused(
                "every candidate violates the risk guardrail: "
                + canonical_json([s.to_dict() for s in scores]))
        selected_id = max(acceptable, key=lambda s: (s.expected_gain, s.candidate_id)).candidate_id
        selected = next(c for c in candidates if c.candidate_id == selected_id)
        chosen_score = next(s for s in scores if s.candidate_id == selected_id)
        rejected: list[RejectedAlternative] = []
        for candidate in candidates:
            if candidate.candidate_id == selected_id:
                continue
            score = next(s for s in scores if s.candidate_id == candidate.candidate_id)
            if not score.acceptable:
                reason = f"risk guardrail: {score.note}"
            else:
                reason = (f"lower declared expected gain ({score.expected_gain}) than the selected "
                          f"candidate ({chosen_score.expected_gain})")
            rejected.append(RejectedAlternative(candidate.candidate_id, reason))
        reason = (f"rule={self.rule}: selected {selected_id} with declared expected_gain="
                  f"{chosen_score.expected_gain} and risk={chosen_score.risk} <= {self.max_risk}")
        return SelectionResult(selected=selected, rejected=tuple(rejected), reason=reason,
                               scores=tuple(scores), rule=self.rule)


__all__ = ["GuardedGainSelector", "SelectionResult", "SelectionRefused", "CandidateScore"]
