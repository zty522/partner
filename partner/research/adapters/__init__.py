"""External-method adapters for Partner continual improvement (M2 / Section 6).

Each adapter is a *small, self-implemented* module that exposes the shape
Partner expects. They do NOT replace Partner with the upstream framework;
they consume the upstream paper / repo as data and re-implement the small
subset we need so the rest of the partner code never takes an opaque
dependency.

State honesty: static_implemented. Adapters are not exercised tonight.

External refs (this session only — paper/repo URLs, not clones):
  - ACE: https://github.com/ace-agent/ace
  - GEPA: https://github.com/gepa-ai/gepa
  - DGM: https://arxiv.org/abs/2505.22954
  - Evo-Memory: https://arxiv.org/abs/2511.20857
  - EvoAgentBench: https://arxiv.org/abs/2607.05202
  - HarnessDev: https://arxiv.org/abs/2609.01437
  - Evo-Bench: https://evobench.org/

These are recorded in docs/dependencies/external_sources.yaml. None of
the upstream code was downloaded, installed, or executed in this pass.
"""
