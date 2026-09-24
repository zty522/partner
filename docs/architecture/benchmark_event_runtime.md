# Benchmark Event Runtime v1

## Purpose

Partner benchmarks use the same Event engine as production work while keeping
the evaluator outside the system under test. A structured test flag selects a
`benchmark_experiment` parent Flow. The parent freezes a protocol, starts one
isolated `benchmark_subject` child Flow for each arm, observes checkpoints,
runs independent evaluators, settles the comparison, delivers a report and
closes an immutable run manifest.

The benchmark does not call a subject Event handler directly. Every meaningful
state transition is an Event and every execution order is a versioned Flow.

## Entry contract

API and web clients submit `mode="benchmark"` with structured constraints.
QQ and terminal clients may use the explicit command prefix:

```text
/benchmark pk_target_feature_v1
研究加入 target-level feature 是否改善 pK 预测
```

The slash command is a transport marker. Natural-language mentions of testing
do not activate a benchmark.

```python
from partner.benchmark.wrapper import PartnerBenchmarkWrapper

submission = PartnerBenchmarkWrapper("/mnt/e/work/partner_workspace").submit(
    protocol_id="pk_target_feature_v1",
    request="运行冻结的 target feature 实验",
    instance_id="01",
    project_id="molecular_generation",
    inputs={
        "dataset_path": "/absolute/path/to/frozen.csv",
        "declared_feature": "target_family",
    },
    guardrail_results={
        "no_target_leakage": True,
        "official_test_not_used_for_tuning": True,
        "within_budget": True,
        "artifacts_complete": True,
        "secondary_metrics_not_materially_worse": True,
    },
)
```

The CLI uses the same wrapper:

```bash
python benchmarks/event_runtime/run.py \
  --workspace /mnt/e/work/partner_workspace \
  --protocol pk_target_feature_v1 \
  --project molecular_generation \
  --instance 01 \
  --request '运行冻结的 target feature 实验' \
  --input dataset_path=/absolute/path/to/frozen.csv \
  --input declared_feature=target_family \
  --guardrail no_target_leakage=true
```

Omit `--wait` when production workers are already running. `--wait` starts a
bounded local worker for the submitted root Job.

## Parent Flow

`benchmark_experiment@1.0.0` executes:

1. `benchmark.signal_validate`
2. `benchmark.protocol_resolve`
3. `benchmark.environment_preflight`
4. `benchmark.run_freeze`
5. `benchmark.variant_plan`
6. `benchmark.variant_parity_check`
7. baseline child submission, collection and evaluation
8. candidate child submission, collection and evaluation
9. post-execution parity, paired, expectation and guardrail comparison
10. advisory JEV and blind LLM evaluation
11. deterministic aggregate and settlement
12. routing, report verification, delivery verification and run close

Child requests are data returned by `benchmark.variant_submit`. The runtime
controller performs start/suspend/resume. No Event invokes another Event.

## Subject Flow and isolation

`benchmark_subject@1.0.0` runs the real Partner project spine and captures six
checkpoints: state, candidate, commitment, execution, verification and
settlement. The subject receives only `benchmark_subject_view` containing its
arm, public task, declared inputs and budget. Expected effects, hidden labels,
guardrail results and evaluator outputs remain in the parent run.

Candidate reasoning is also decomposed: `candidate.propose`,
`candidate.critic` and `candidate.select` are separate Events. Their inputs,
model calls, outputs and failures therefore remain independently observable.

Each Event envelope carries:

- `run_mode`
- `benchmark_run_id`
- `benchmark_protocol_id`
- `benchmark_arm_id`
- `checkpoint_policy_ref`
- `evaluation_visibility`

These fields are persisted both in the Flow state and the authoritative SQLite
Job repository, including schema migration from older workspaces.

## Evaluation authority

Deterministic integrity checks, frozen domain metrics and hard guardrails are
authoritative. JEV and the LLM judge are advisory and may abstain. External
judges run only when explicitly enabled; their output cannot override a hard
failure. Missing metrics produce an inconclusive or invalid result rather than
a fabricated score.

The first bundled protocol is `pk_target_feature_v1`. It freezes HGB,
target-group folds, one declared feature, five folds, 1000 bootstrap samples
and a minimum RMSE improvement of 0.03. The baseline exposes no added feature;
the candidate exposes exactly the declared feature. Variant parity rejects any
undeclared difference before either arm starts.

Each arm must produce `benchmark_evidence.json`. It contains numeric metrics,
sample-level predictions, independently checkable guardrail facts, actual
`run_config` and provenance. The runtime compares actual model, folds, seeds
and budget after execution. Baseline must record a null declared feature and
candidate must record the exact frozen feature. Missing execution provenance
makes the run invalid even when an RMSE value is present.

## Artifacts

Every run writes under:

```text
state/benchmarks/runs/<benchmark_run_id>/
```

The directory contains the manifest, frozen protocol, parity proof, arm child
records, checkpoint snapshots, metric records, judge records, comparison,
settlement and JSON/Markdown reports. A failed child is still collected and
settled honestly; it does not strand the suspended parent Flow.
