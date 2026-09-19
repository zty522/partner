# Task: cross-class prior transfer probe

    cd /mnt/e/work/partner && PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider \
        tests/test_adapter_contracts.py -k "gepa or dgm"

| arm | result |
|---|---|
| control (unmodified) | 3 passed, 4 failed |
| candidate (declared patch) | 6 passed, 1 failed |

The declared patch is the real gepa contract fix; it makes the three failing gepa tests pass
and leaves the unrelated dgm contract failure in place.  The declared bar is
`tests_passed >= threshold (1.0)` with `delta_over_baseline(min_delta=4.0)`: the candidate
delivers +3 (3 -> 6 passing tests), so on its own the run cannot claim an improvement.

This task's class (`real_task:cross_class_transfer_probe` in project
`molecular_generation` with the standard `tests_passed` metric shape) has **no history of
its own**.  It shares its project and its metric shape with two real classes that do have
history, so the prior node borrows from them at weight 0.5 each:

* `cls_95bc4202aeeea132` (`real_task:refuted_green_baseline_ace`) -- 6 refuted, 2 supported
* `cls_fb8b1f2ced4bec6a` (`real_task:fix_dgm_lineage_contract`) -- 8 supported

The weighted view (falsified 3.5, supported 5.5, evidence_total 9.0, ratio 0.389) takes the
relaxing branch and lowers the bar from 4.0 to the floor 0.25, which is what lets this run
claim `improvement_over_baseline` honestly.  With `prior=off` the bar stays at 4.0 and the
same measurement settles with `absolute_attainment` instead.  Nothing about the message or
the trace token participates in either decision.
