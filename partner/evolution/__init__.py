"""Evolution Engine — self-evolving closed loop for Partner.

Extracts patterns from execution history and feeds them back
into the Planner's decision-making process.

Modules:
  - architecture_improver: Apply architecture improvements (config/tool/code)
  - architecture_mapper: Map external architectures → Partner module gaps
  - auto_integrate: Auto-integrate external tools into Partner
  - behavior_tuner: Format and inject evolution rules into planner prompts
  - capability_discovery: Discover new capabilities from external sources
  - self_evolve_engine: Five-step self-evolution cycle
  - skill_evolver: Skills auto-evolution (create→use→evolve closed loop)
"""
