"""Canonical executable Event definitions.

Markdown playbooks formerly stored below this directory are legacy flow
descriptions.  New runtime capabilities are Python definitions with typed
contracts and explicit registration here.
"""
from __future__ import annotations

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
    return [*interaction, *memory, *decision, *presentation, *project,
            *active_learning, *self_evolution, *delivery, *social_video, *figure_assets]


__all__ = ["builtin_definitions"]
