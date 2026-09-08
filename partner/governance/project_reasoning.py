"""Shared reasoning contract for substantive LLM-guided project decisions."""
from __future__ import annotations


def project_reasoning_contract() -> str:
    """Return operational cognitive moves; philosophical labels are not evidence."""
    return """
Use this six-part reasoning contract:
1. PERCEIVE / 现实接触：list only observed files, logs, tool results and missing facts.
2. ASSOCIATE / 联想：retrieve analogous history or external knowledge, but explicitly state why similarity is not causality.
3. CONSTRAIN / 先验约束：apply safety, provenance, resource, domain and Event capability boundaries before imagining actions.
4. DIALECTIC / 多人式讨论：state a main hypothesis, a strongest opposing hypothesis, and a critic's cheapest counterexample.
5. ACCOMMODATE / 顺应：if the existing action grammar cannot explain the residual failure, request external active learning or a Partner self-evolution Candidate; never relabel either as business progress.
6. ACT-REFLECT / 行动与自反：choose one smallest executable Event whose declared measurements can falsify the hypothesis; after the terminal result, say what belief or next action must change.

Keep three ledgers distinct:
- business_progress changes a project's real artifact or verified domain metric;
- external_active_learning acquires and reads new outside evidence, then creates a cited adoption proposal;
- partner_self_evolution changes Partner code/policy only through isolated baseline/candidate verification and a reversible promotion gate.
Narrative novelty, report generation, message delivery and elapsed time are not progress by themselves.
""".strip()


__all__ = ["project_reasoning_contract"]
