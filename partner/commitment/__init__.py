"""The commitment kernel: a bounded, falsifiable bet loop.

Public surface::

    from partner.commitment import (
        models, state_machine, ports, proposer, selector, freezer,
        evaluator, settlement, policy, store, runner,
    )

The kernel owns one closed loop and nothing else::

    read frozen state -> identify a falsifiable question -> propose a finite
    candidate set -> select one and record what was abandoned -> freeze
    expectations, failure conditions, evaluation protocol and budget -> run one
    bounded action -> measure independently -> compare expectation, baseline and
    actual -> supported/falsified/inconclusive/invalid/blocked -> record one
    experience and stop

It does not schedule, does not recover other work, and does not seed follow-up
jobs.  Those are Event Fabric's responsibility.
"""
from __future__ import annotations

from . import (  # noqa: F401
    evaluator, freezer, models, policy, ports, proposer, runner, selector,
    settlement, state_machine, store,
)

__all__ = ["models", "state_machine", "ports", "proposer", "selector", "freezer",
           "evaluator", "settlement", "policy", "store", "runner"]
