"""Acceptance-only minimal task chain flow.

A single-node flow used to verify the JobRepository claim/release
chain end-to-end.  Runs `improvement.recall` (local-only Event that
records opportunities to the audit log) and then finishes.  No
external side effects, no LLM calls required, no QQ routing.

This flow is explicitly tagged as ``acceptance_only=true`` so it
cannot be confused with a production flow.
"""
from __future__ import annotations

from partner.event_fabric.flows import EventFlowDefinition as Flow, FlowNode


def build_acceptance_only_flow():
    """Return the minimal acceptance flow.

    Nodes:
      - accept_echo: low-side-effect Event that writes a local audit
        line.  Implementation lives in partner.events.acceptance.
    """
    nodes = (
        FlowNode("accept_echo", "acceptance.echo", ()),
    )
    return Flow("acceptance_minimal_chain", "1.0.0", tuple(nodes),
                 "Acceptance-only minimal task chain.")


DEFINITIONS = [build_acceptance_only_flow()]
