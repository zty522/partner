# Task: the bar is above what the task can deliver (an honest loss)

    cd /mnt/e/work/partner && PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider \
        tests/test_adapter_contracts.py -k test_ace_append_to_memory_writes_real_file
    → 1 passed, 10 deselected

The declared test target selects exactly **one** test, and it already passes in the
baseline.  The frozen expectation is `tests_passed >= threshold (2.0)` with
`delta_over_baseline(min_delta=1.0)`, so no candidate can satisfy it: one additional
passing test does not exist in this target, whatever the patch does.

The candidate patch adds a paragraph to the module docstring.  It is real (the file
changes, its sha256 changes, the test runner really re-runs) and behaviour-neutral (the
metric cannot move).

| arm | module | result |
|---|---|---|
| control | unmodified (project history state) | 1 passed, 0 failed |
| candidate | with the declared patch | 1 passed, 0 failed |

Expected settlement: `falsified` -- and if the instance finds a way to make an extra test
pass, that is reported as `supported` instead, honestly.
