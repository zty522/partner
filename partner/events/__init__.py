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
    return [*interaction, *memory, *decision, *presentation, *project,
            *active_learning, *self_evolution, *delivery, *social_video,
            *figure_assets, *cross_cutting, *evolution_pipeline,
            *pre_iteration_reflect, *cycle, *autonomous_evolution,
            *emit_progress,
            *improvement, *acceptance, *local_learning, *commitment, *core_v1]


__all__ = ["builtin_definitions"]
