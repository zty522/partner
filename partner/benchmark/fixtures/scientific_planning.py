from pathlib import Path
"""Synthetic scientific-planning fixture (M3 / Domain 2) — v3.

Four-phase separation (Audit 5):
* ``prepare_initial_state`` — drop the input table, no answer
* ``reference_oracle``      — compute the expected CI in reference/
* ``score``                  — read Agent outputs, compare to reference
"""

FAMILY = "scientific_planning"
DOMAIN = "scientific_analysis_planning"


class StageError(AssertionError):
    pass


STAGES = [
    {"stage": 1, "name": "load_pk_table",
     "function": "load_pk_table", "input": "pk_table.csv",
     "output": "pk_table_loaded.json",
     "expected": {"row_count": 12}},
    {"stage": 2, "name": "bootstrap_ci",
     "function": "bootstrap_ci", "input": "pk_table_loaded.json",
     "output": "ci.json",
     "expected": {"ci_method": "bootstrap_paired_95",
                  "ci_keys": ["delta_lower", "delta_upper"]}},
    {"stage": 3, "name": "plan_next_experiment",
     "function": "plan_next_experiment", "input": "ci.json",
     "output": "next_plan.json",
     "expected": {"required_fields": ["hypothesis", "minimum_sample_size", "stop_rule"]}},
]


def _read_csv(path):
    import csv as _csv
    if not path.exists():
        raise StageError(f"missing: {path}")
    with path.open(encoding="utf-8") as f:
        return list(_csv.DictReader(f))


def load_pk_table(input_path, output_path):
    if not input_path.exists():
        raise StageError(f"missing: {input_path}")
    text = input_path.read_text(encoding="utf-8")
    n = max(0, len([l for l in text.splitlines() if l.strip() and not l.startswith("#")]) - 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json_dumps({"row_count": n}), encoding="utf-8")
    return {"row_count": n}


def bootstrap_ci(input_path, output_path):
    """Agent-side bootstrap.  Real impl computes from pk_table_loaded.json."""
    if not input_path.exists():
        raise StageError(f"missing: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json_dumps({
        "ci_method": "bootstrap_paired_95",
        "delta_lower": 0.0, "delta_upper": 0.0,
    }), encoding="utf-8")
    return {"ci_method": "bootstrap_paired_95"}


def plan_next_experiment(input_path, output_path):
    """Agent-side plan.  Real impl reads ci.json and writes plan."""
    if not input_path.exists():
        raise StageError(f"missing: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json_dumps({
        "hypothesis": "TBD",
        "minimum_sample_size": 0,
        "stop_rule": "TBD",
    }), encoding="utf-8")
    return {"required_fields": ["hypothesis", "minimum_sample_size", "stop_rule"]}


def json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


def prepare_initial_state(workspace, task_id: str) -> None:
    workspace = Path(workspace)
    inputs_dir = workspace / "inputs" / "fixtures" / FAMILY
    inputs_dir.mkdir(parents=True, exist_ok=True)
    # 12 rows of paired data: 6 treatment-A + 6 treatment-B (same as before)
    pk_csv = (
        "treatment,replicate,pk\n"
        "A,1,5.1\nA,2,5.3\nA,3,5.0\nA,4,5.2\nA,5,5.4\nA,6,5.1\n"
        "B,1,5.7\nB,2,5.9\nB,3,5.6\nB,4,5.8\nB,5,6.0\nB,6,5.7\n"
    )
    (inputs_dir / "pk_table.csv").write_text(pk_csv, encoding="utf-8")
    (inputs_dir / "README.md").write_text(
        "Scientific planning task (synthetic).\n\n"
        "Inputs:\n"
        "* pk_table.csv — paired A/B measurements.\n\n"
        "Required outputs (outputs/<task_id>/):\n"
        "* pk_table_loaded.json — {row_count}\n"
        "* ci.json             — bootstrap_paired_95 CI\n"
        "* next_plan.json      — hypothesis + minimum_sample_size + stop_rule\n\n"
        "Do NOT read from reference/.\n",
        encoding="utf-8",
    )


def reference_oracle(workspace, task_id: str) -> dict:
    workspace = Path(workspace)
    inputs_root = workspace / "inputs"
    ref_root = workspace / "reference" / task_id
    ref_root.mkdir(parents=True, exist_ok=True)
    rows = _read_csv(inputs_root / "fixtures" / FAMILY / "pk_table.csv")
    a_vals = [float(r["pk"]) for r in rows if r["treatment"] == "A"]
    b_vals = [float(r["pk"]) for r in rows if r["treatment"] == "B"]
    if len(a_vals) != len(b_vals):
        raise StageError("paired table unbalanced")
    diffs = [b - a for a, b in zip(a_vals, b_vals)]
    import random as _r
    rng = _r.Random(0)
    deltas = []
    for _ in range(1000):
        sample = [diffs[rng.randrange(len(diffs))] for _ in range(len(diffs))]
        deltas.append(sum(sample) / len(sample))
    deltas.sort()
    delta_lower = deltas[int(1000 * 0.025)]
    delta_upper = deltas[int(1000 * 0.975)]
    reference = {
        "row_count": len(rows),
        "ci": {
            "ci_method": "bootstrap_paired_95",
            "delta_lower": delta_lower,
            "delta_upper": delta_upper,
        },
        "plan": {
            "hypothesis": "Treatment B raises pk by ~0.6",
            "minimum_sample_size": 30,
            "stop_rule": "ci_lower > 0.05 and n >= 30",
        },
    }
    (ref_root / "pk_table_loaded.json").write_text(json_dumps({"row_count": reference["row_count"]}),
                                                  encoding="utf-8")
    (ref_root / "ci.json").write_text(json_dumps(reference["ci"]), encoding="utf-8")
    (ref_root / "next_plan.json").write_text(json_dumps(reference["plan"]), encoding="utf-8")
    return {
        "pk_table_loaded.json": ref_root / "pk_table_loaded.json",
        "ci.json": ref_root / "ci.json",
        "next_plan.json": ref_root / "next_plan.json",
    }


def score(workspace, task_id: str, agent_dir, reference_paths: dict) -> dict:
    workspace = Path(workspace)
    agent_dir = Path(agent_dir)
    result = {"stages": {}, "matched": 0, "expected": 3}
    for stage in STAGES:
        output_name = stage["output"]
        agent_path = agent_dir / output_name
        reference_path = reference_paths.get(output_name)
        ok = False
        if agent_path.exists() and reference_path and reference_path.exists():
            ok = (agent_path.read_text(encoding="utf-8").strip() ==
                  reference_path.read_text(encoding="utf-8").strip())
        result["stages"][output_name] = "pass" if ok else "fail"
        if ok:
            result["matched"] += 1
    result["score"] = result["matched"] / result["expected"] if result["expected"] else 0.0
    return result


TASK_DEFINITION = {
    "task_id": "sci_plan_synth_v3",
    "family": FAMILY, "domain": DOMAIN,
    "schema_version": "benchmark/v1",
    "inputs": [f"fixtures/{FAMILY}/pk_table.csv",
               f"fixtures/{FAMILY}/README.md"],
    "initial_state": {"working_dir": "episodes/sci_plan_synth_v3/workspace"},
    "stages": STAGES,
    "success_criteria": [
        "outputs/pk_table_loaded.json matches reference row_count",
        "outputs/ci.json matches reference CI",
        "outputs/next_plan.json matches reference plan",
    ],
    "annotations": [{
        "annotation_id": "ann_sci_plan_synth_v3_01", "task_id": "sci_plan_synth_v3",
        "label": "needs_human", "evidence_refs": [f"fixtures/{FAMILY}/pk_table.csv"],
        "annotation_pending": True, "schema_version": "benchmark/v1",
        "notes": "Synthetic v3; needs_human until a domain expert reviews the oracle.",
    }],
    "notes": "Synthetic v3 fixture; Agent executes; reference produces expected under reference/.",
}


ORACLE_INPUTS = {
    f"fixtures/{FAMILY}/pk_table.csv":
    "treatment,replicate,pk\nA,1,5.1\nA,2,5.3\nA,3,5.0\nA,4,5.2\nA,5,5.4\nA,6,5.1\n"
    "B,1,5.7\nB,2,5.9\nB,3,5.6\nB,4,5.8\nB,5,6.0\nB,6,5.7\n",
    f"fixtures/{FAMILY}/README.md":
    "Scientific planning task (synthetic). See score() for expected outputs.\n",
}


ORACLE_EXPECTED = {
    "ci.json": "see reference/",
    "next_plan.json": "see reference/",
}
