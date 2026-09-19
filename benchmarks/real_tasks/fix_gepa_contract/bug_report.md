# Bug report: gepa adapter contract tests fail (real, reproducible)

Command (run against the unmodified repository):

    cd /mnt/e/work/partner && PYTHONPATH=. python3 -m pytest -q -p no:cacheprovider \
        tests/test_adapter_contracts.py -k gepa

Observed:

    tests/test_adapter_contracts.py:27: ImportError: cannot import name 'propose' from
        'partner.research.adapters.gepa'
    tests/test_adapter_contracts.py:33: ImportError: cannot import name 'propose'
    tests/test_adapter_contracts.py:40: ImportError: cannot import name 'propose'
    3 failed, 2 passed, 6 deselected

Three separate contract violations:

1. ``partner.research.adapters.gepa`` exports only the ``GepaOptimizer`` class.  The
   contract tests (and the adapter's own docstring) call module-level ``propose``,
   ``record_fitness`` and ``pareto_front``.
2. ``record_fitness`` in the contract takes the keyword ``fitness_signal`` and must
   refuse a signal outside [0, 1] with ``ValueError``; the class method calls the same
   value ``score`` and raises ``GepaError``, which is not a ``ValueError``.
3. ``pareto_front`` does not exist at all; the contract requires the front ordered by
   fitness, best first (0.7 before 0.3).

The declared patch (``patch.diff``) adds the module-level helpers, makes ``GepaError``
a ``ValueError`` subclass (so both existing and contract callers catch it), and keeps
the class behaviour unchanged.

Measured with the real test runner:

| arm | module | result |
|---|---|---|
| control | unmodified (project history state) | 3 failed, 2 passed (exit 1) |
| candidate | with the declared patch | 5 passed (exit 0) |
