# Task: the bar is above what the task can deliver (an honest loss)

## Attempt 1 -- a deliberately unattainable bar

    cd /mnt/e/work/partner && PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider \
        tests/test_adapter_contracts.py -k test_ace_append_to_memory_writes_real_file
    → 1 passed, 10 deselected

The declared test target selects exactly **one** test, and it already passes in the
baseline.  The frozen expectation is `tests_passed >= threshold (2.0)` with
`delta_over_baseline(min_delta=1.0)`, so no candidate can satisfy it: one additional
passing test does not exist in this target, whatever the patch does.

The candidate patch adds a paragraph to the module docstring (`patch_abstention_attempt.diff`).
It is real (the file changes, its sha256 changes, the test runner really re-runs) and
behaviour-neutral (the metric cannot move).

| arm | module | result |
|---|---|---|
| control | unmodified | 1 passed, 0 failed |
| candidate | with the declared patch | 1 passed, 0 failed |

Settled **falsified** -- repeatedly, which is what made this class the abstention case:
after three real falsified settlements the rule declined to wager on it.

## Attempt 2 -- the same class, a real failing target and its real fix

    cd /mnt/e/work/partner && PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider \
        tests/test_adapter_contracts.py -k gepa
    → 2 passed, 3 failed

The declared patch (`patch.diff`, the real gepa contract fix) makes the three failing
tests pass:

| arm | result |
|---|---|
| control | 2 passed, 3 failed |
| candidate | 5 passed, 0 failed |

Settled **supported** (expectations met by real measurement; the prior had raised
`min_delta` to 8.0, so the settlement honestly claims `absolute_attainment`, not
`improvement_over_baseline`).  Attempt 2 is the reversal run: it is the same task class, so
the abstention had to be re-evaluated against it -- and it was refused, because an
abstention sitting at the tail means no new evidence arrived.

## Why this task exists

It is the honest-loss half of the class: a task whose declared bar cannot be reached, used
to give the harness a real refuted history, and then to give it a real success so the
abstention cannot become a permanent lock.
