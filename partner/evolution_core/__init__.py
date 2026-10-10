"""Partner Evolution Core (L5: Evolution & Governance) — logical aggregation entry.

L5 owns how the partner *improves itself and governs quality*:

  - Evolution: ``partner/events/evolution*.py``, ``partner/events/self_evolution.py``,
    ``partner/event_flows/cycle.py`` (autonomous_evolution / supervision_cycle /
    learning_improvement_cycle / self_improvement_cycle), ``partner/evolution/``
    (experience engine, candidate pipeline).
  - Improvement loops: ``partner/events/improvement.py`` (observe → evidence →
    experiment → settle → consolidate), ``partner/events/pre_iteration_reflect.py`.
  - Benchmark & evaluation: ``partner/events/benchmark.py``, ``evaluation.py``,
    ``v4_benchmark.py``, ``v5_research.py``, ``loop_bench.py``, ``meta_cycle.py``,
    ``partner/event_flows/benchmark.py``.
  - Acceptance & commitment: ``partner/events/acceptance.py``, ``commitment.py``,
    ``partner/events/goal/acceptance_criteria.py``, ``commitment/``.
  - Governance & rules: ``partner/events/governance/`` (experience policy,
    user experience), ``partner/events/context/layered_rules.py``, ``rules/``.
  - Supervision: ``partner/events/supervision.py``, ``partner/events/system_evolution.py``,
    ``partner/events/long_run_controller.py``, ``fair_scheduler.py``,
    ``token_aggregator.py``, ``model_service_adapter.py``.

Invariant: physical paths above are transitional.  Logical ownership is
declared here; code moves here only when imports are updated in the same
commit.  New L5 code MUST land under ``partner/evolution_core/``.
"""
