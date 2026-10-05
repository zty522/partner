"""Canonical executable Event definitions.

Markdown playbooks formerly stored below this directory are legacy flow
descriptions.  New runtime capabilities are Python definitions with typed
contracts and explicit registration here.
"""
from __future__ import annotations

import importlib

from partner.event_fabric.catalog import EventDefinition


def builtin_definitions() -> list[EventDefinition]:
    from .interaction import DEFINITIONS as interaction
    from .memory import DEFINITIONS as memory
    from .decision import DEFINITIONS as decision
    from .presentation import DEFINITIONS as presentation
    from .project import DEFINITIONS as project
    from .active_learning import DEFINITIONS as active_learning
    from .self_evolution import DEFINITIONS as self_evolution
    from .delivery import DEFINITIONS as delivery
    from .social_video import DEFINITIONS as social_video
    from .figure_assets import DEFINITIONS as figure_assets
    from .cross_cutting import DEFINITIONS as cross_cutting
    from .evolution_pipeline import DEFINITIONS as evolution_pipeline
    from .pre_iteration_reflect import DEFINITIONS as pre_iteration_reflect
    from .emit_progress import DEFINITIONS as emit_progress
    from .cycle import DEFINITIONS as cycle
    from .autonomous_evolution import DEFINITIONS as autonomous_evolution
    from .improvement import DEFINITIONS as improvement
    from .local_learning import DEFINITIONS as local_learning
    from .acceptance import DEFINITIONS as acceptance
    from .commitment import DEFINITIONS as commitment
    from .core_v1 import DEFINITIONS as core_v1
    from .benchmark import DEFINITIONS as benchmark
    from .candidate import DEFINITIONS as candidate
    from .loop_bench import DEFINITIONS as loop_bench
    from .v4_benchmark import DEFINITIONS as v4_benchmark
    from .v5_research import DEFINITIONS as v5_research
    from .meta_cycle import DEFINITIONS as meta_cycle
    from .long_run_controller import long_run_controller_event
    from .pdf_fact_adjudicator import pdf_fact_adjudicator_event
    from .corpus_eligibility import corpus_eligibility_event
    from .learning_source_recovery import learning_source_recovery_event
    from .evolution_candidate_merger import evolution_candidate_merger_event
    from .fair_scheduler import fair_scheduler_event
    from .token_aggregator import token_aggregator_event
    from .model_service_adapter import model_service_adapter_event
    from .pdf_five_section import pdf_five_section_event
    from .message_sanitizer import message_sanitize_event
    return [*interaction, *memory, *decision, *presentation, *project,
            *active_learning, *self_evolution, *delivery, *social_video,
            *figure_assets, *cross_cutting, *evolution_pipeline,
            *pre_iteration_reflect, *cycle, *autonomous_evolution,
            *emit_progress,
            *improvement, *acceptance, *local_learning, *commitment, *core_v1,
            *benchmark, *candidate, *loop_bench, *v4_benchmark, *v5_research,
            *meta_cycle,
            long_run_controller_event, pdf_fact_adjudicator_event,
            corpus_eligibility_event, learning_source_recovery_event,
            evolution_candidate_merger_event, fair_scheduler_event,
            token_aggregator_event, model_service_adapter_event,
            pdf_five_section_event,
            message_sanitize_event]


__all__ = ["builtin_definitions"]
