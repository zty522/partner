# Bug report: dgm adapter contract test fails (real, reproducible)

    cd /mnt/e/work/partner && PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider \
        tests/test_adapter_contracts.py -k lineage

    tests/test_adapter_contracts.py:62:
    E   TypeError: lineage_edges() missing 1 required positional argument: 'run_id'
    1 failed, 1 passed, 9 deselected

The contract (`test_dgm_lineage_edges_dedup_sorted`) builds three nodes in memory and
calls ``lineage_edges([parent, child_a, child_b])``, then asserts the result is sorted
and contains both parent->child edges.  The shipped adapter only accepts
``lineage_edges(workspace, run_id)`` and reads the persisted archive, so the in-memory
shape raises ``TypeError`` before any assertion runs.

The declared patch keeps the archive call shape working and adds the in-memory one, and
deduplicates the edges.

| arm | module | result |
|---|---|---|
| control | unmodified (project history state) | 1 failed, 1 passed (exit 1) |
| candidate | with the declared patch | 2 passed (exit 0) |
