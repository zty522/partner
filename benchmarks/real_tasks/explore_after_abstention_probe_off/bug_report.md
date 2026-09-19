# Task: abstention -> bounded exploration

    cd /mnt/e/work/partner && PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider \
        tests/test_adapter_contracts.py -k "gepa or dgm"

| variant | patch | result |
|---|---|---|
| partial_propose | `patch_partial.diff` | 4 passed, 3 failed |
| full_contract | `patch_full.diff` | 6 passed, 1 failed |

Both are real patches to `partner/research/adapters/gepa.py`; the module-level contract helpers
are missing, so the gepa tests fail on import.  The declared bar is `tests_passed >= 6.0` with
`delta_over_baseline(min_delta=2.0)`: the partial variant cannot meet it (4 < 6), the complete
one can (6 >= 6, +3 over the unmodified 3).

The task exists to answer an abstention: when the class has only failure evidence, the
harness declines to wager and instead tries ONE variant the class has never tried, under a
quarter of the declared budget.  Nothing about the variant is generated: both are declared
here, and which one an exploration carries is decided by what the class already tried.
