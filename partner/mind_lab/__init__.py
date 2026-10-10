"""Partner Mind Lab (L4: Cognition & Learning) — logical aggregation entry.

L4 owns everything the partner *knows and learns*:

  - Unified note store (Mind Notes):  ``partner/memory/notes.py``
    (logical home: mind_lab.notes) — every durable LLM-usable memory,
    including pending items, insights, lessons, habits, growth, beliefs,
    preferences, user insights and issue notes.  Ledger:
    ``<workspace>/share/mind/notes/notes.jsonl``.
  - LLM doors on the note store: ``partner/events/notes.py``
    (notes.recall / notes.judge / notes.promote / notes.evolution_sync).
  - Learning sources & external knowledge: ``partner/events/active_learning.py``,
    ``partner/events/local_learning.py``, ``partner/events/learning_source_recovery.py``.
  - Event memory (observations/lessons/preferences/habits/beliefs/growth):
    ``partner/memory/event_memory.py`` — storage unified into the note ledger.
  - World model / JEV channels: ``partner/world_model/``, ``partner/events/core.py``.
  - Candidate registry: ``partner/mind_lab/registry.py`` — implemented vs
    blueprint components (the 8 reserved EVENT.md slots are registered there
    as instantiate-on-evidence blueprints).

Invariant: physical paths above are transitional.  Logical ownership is
declared here; code moves here only when imports are updated in the same
commit.  New L4 code MUST land under ``partner/mind_lab/``.
"""
