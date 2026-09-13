"""Declarative Event Flow catalog.

Flows compose semantic Event checkpoints.  They contain no business logic and
are safe to snapshot into a running Task.
"""
from .registry import EventFlowRegistry, build_flow_registry

__all__ = ["EventFlowRegistry", "build_flow_registry"]
