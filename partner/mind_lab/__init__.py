"""Partner Mind Lab (L4: Cognition & Learning) — logical aggregation entry.

L4 owns everything the partner *knows and learns*:

  - Unified note store (Mind Notes):  ``partner/memory/notes.py``
    (logical home: mind_lab.notes) — every durable LLM-usable memory,
    including pending items, insights, lessons, habits, growth, beliefs,
    preferences, user insights and issue notes.  Ledger:
    ``<workspace>/share/mind/notes/notes.jsonl``.
  - LLM doors on the note store: ``partner/events/notes.py``
    (notes.recall / notes.judge / notes.promote).
  - Learning sources & external knowledge: ``partner/events/active_learning.py``,
    ``partner/events/local_learning.py``, ``partner/events/curiosity.py``,
    ``partner/events/knowledge.py`` (logical home: mind_lab.learning).
  - Event memory (observations/lessons/preferences/habits/beliefs/growth):
    ``partner/memory/`` (logical home: mind_lab.memory).
  - Experience engine & procedural memory: ``partner/evolution/experience_engine.py``,
    ``partner/memory/procedural_memory.py``.
  - World model / cognition: ``partner/events/world_model.py``,
    ``partner/events/cognition.py``, ``partner/events/curation.py``.
  - Reserved idea/literature slots: ``partner/events/`` (cross-pollination,
    deep-analysis, exploration, idea-exploration, literature-deep-dive,
    method-learning, synthesis-review).

Invariant: physical paths above are transitional.  Logical ownership is
declared here; code moves here only when imports are updated in the same
commit.  New L4 code MUST land under ``partner/mind_lab/``.
"""
