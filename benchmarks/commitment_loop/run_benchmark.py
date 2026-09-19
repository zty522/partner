#!/usr/bin/env python3
"""Run the three-arm benchmark skeleton on the deterministic fixture.

This is **infrastructure verification**, and the output says so.  It answers
"does the harness run end to end and produce comparable records?", not "is the
commitment loop better?".  One synthetic episode per seed cannot support the
second question, and nothing here should be quoted as if it could.

Usage::

    python3 benchmarks/commitment_loop/run_benchmark.py \
        --workspace <isolated dir> --out <out dir> [--seeds 11,12,13] [--use-llm]

Every arm receives the same Episode: identical snapshot, budget, evaluator,
protocol and seeds.  The manifest records that parity proof.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.commitment_loop import arms as arms_module  # noqa: E402
from benchmarks.commitment_loop import fixtures, metrics  # noqa: E402
from partner.commitment.models import canonical_json, sha256_of  # noqa: E402

INFRASTRUCTURE_ONLY = True


def _parity_proof(episodes: Sequence[fixtures.Episode]) -> dict[str, Any]:
    """Evidence that the arms were given identical conditions."""
    from partner.commitment.settlement import budget_hash, protocol_hash
    from benchmarks.commitment_loop.arms import _synthetic_bet
    entry = episodes[0].snapshot["candidate_space"][1]
    bet = _synthetic_bet(episodes[0], entry, revision=1)
    return {
        "evaluator": {"id": "benchmark-deterministic-evaluator", "version": "1.0.0"},
        "protocol_hash": protocol_hash(bet),
        "budget": episodes[0].budget_spec,
        "budget_hash": budget_hash(bet),
        "shared_seeds": [e.seed for e in episodes],
        "snapshot_hashes": {e.seed: sha256_of(e.snapshot) for e in episodes},
        "max_rounds_per_arm": episodes[0].max_rounds,
        "turns_allowed": episodes[0].turns_allowed,
        "note": "every arm receives the same Episode object; see test_benchmark_parity",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True,
                        help="isolated benchmark workspace (never the production workspace)")
    parser.add_argument("--out", required=True, help="output directory for manifest and results")
    parser.add_argument("--seeds", default=",".join(str(s) for s in fixtures.SEEDS))
    parser.add_argument("--arms", default=",".join(cls.name for cls in arms_module.ARMS))
    parser.add_argument("--use-llm", action="store_true",
                        help="allow the free_llm_loop arm to call the configured provider")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    seeds = [int(s) for s in str(args.seeds).split(",") if str(s).strip()]
    wanted = [name.strip() for name in str(args.arms).split(",") if name.strip()]

    episodes = [fixtures.Episode(workspace=workspace / f"seed_{seed}", seed=seed,
                                 snapshot=fixtures.episode_snapshot(seed=seed))
                for seed in seeds]
    for episode in episodes:
        episode.workspace.mkdir(parents=True, exist_ok=True)

    llm_client = None
    dependency_notes: list[str] = []
    if args.use_llm:
        try:
            from partner.commitment.proposer import resolve_provider_config
            llm_client = resolve_provider_config(workspace)
        except Exception as exc:  # noqa: BLE001 -- a missing provider is reported, not hidden
            dependency_notes.append(f"llm_chooser unavailable: {type(exc).__name__}")

    results: list[fixtures.ArmResult] = []
    defining_behaviour: dict[str, Any] = {}
    started = time.time()
    for arm_name in wanted:
        for episode in episodes:
            kwargs: dict[str, Any] = {}
            if arm_name == "free_llm_loop":
                kwargs["llm_client"] = llm_client
            arm = arms_module.build_arm(arm_name, **kwargs)
            results.append(arm.run(episode))
        # Record honestly what each arm actually did, rather than assuming the arm
        # name implies its defining behaviour ran.
        if arm_name == "free_llm_loop":
            defining_behaviour[arm_name] = {
                "requires_llm": True,
                "llm_client_present": llm_client is not None,
                "defining_behaviour_executed": llm_client is not None,
                "note": ("free next-step choice was replayed by the deterministic greedy rule; "
                         "the arm's defining LLM behaviour did NOT execute")
                        if llm_client is None else "free choice executed against a real client",
            }
        else:
            defining_behaviour[arm_name] = {"requires_llm": False,
                                            "defining_behaviour_executed": True,
                                            "note": "deterministic arm; nothing was substituted"}

    payload = {
        "benchmark_id": fixtures.BENCHMARK_ID,
        "infrastructure_only": INFRASTRUCTURE_ONLY,
        "fixture_label": fixtures.FIXTURE_LABEL,
        "research_claim": "none -- this run does not support a comparison or a hypothesis",
        "started_at": started,
        "finished_at": time.time(),
        "workspace": str(workspace),
        "parity": _parity_proof(episodes),
        "defining_behaviour": defining_behaviour,
        "limitations": ([defining_behaviour["free_llm_loop"]["note"]]
                        if "free_llm_loop" in defining_behaviour
                        and not defining_behaviour["free_llm_loop"]["defining_behaviour_executed"]
                        else []),
        "dependency_notes": dependency_notes,
        "runs": [r.to_dict() for r in results],
        "metrics": {f"{r.arm}|seed{r.seed}": metrics.all_metrics(r) for r in results},
    }
    (out / "manifest.json").write_text(
        json.dumps({k: payload[k] for k in ("benchmark_id", "infrastructure_only", "fixture_label",
                                            "research_claim", "parity", "workspace",
                                            "defining_behaviour", "limitations")},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(payload["runs"], ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(payload["metrics"], ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (out / "metrics.csv").write_text(_as_csv(payload["metrics"]), encoding="utf-8")

    print(json.dumps({
        "event": "commitment_benchmark_skeleton_finished",
        "infrastructure_only": INFRASTRUCTURE_ONLY,
        "arms": wanted, "seeds": seeds, "runs": len(results),
        "out": str(out),
        "note": payload["research_claim"],
        "limitations": payload["limitations"],
    }, ensure_ascii=False))
    for r in results:
        print(f"  {r.arm:18s} seed={r.seed:3d} rounds={r.rounds} "
              f"settlements={r.settlement_classes or ['none']} notes={list(r.notes)}")
    return 0


def _as_csv(metrics_payload: dict[str, dict[str, Any]]) -> str:
    names = sorted({name for values in metrics_payload.values() for name in values})
    lines = ["run," + ",".join(names)]
    for run_id in sorted(metrics_payload):
        row = [run_id] + [("" if metrics_payload[run_id][n]["value"] is None
                           else str(metrics_payload[run_id][n]["value"])) for n in names]
        lines.append(",".join(row))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
