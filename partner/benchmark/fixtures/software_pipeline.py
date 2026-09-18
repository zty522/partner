from pathlib import Path
"""Synthetic software-pipeline fixture (M3 / Domain 1) — v3.

This fixture separates the four phases required by Audit 5:

* ``prepare_initial_state(workspace)`` — creates the task inputs but
  **not** the expected outputs.  Drops a "broken" CSV that the Agent
  must repair into the canonical pipeline artefacts.
* ``reference_oracle(workspace)`` — runs the **reference** pipeline in
  a sandboxed ``reference/<task_id>/`` directory that the Agent cannot
  read.  Produces ``expected_summary.csv`` etc.
* ``score(workspace, agent_dir)`` — reads the Agent's frozen outputs
  from ``outputs/<task_id>/`` and compares them to the reference
  outputs.  Does **not** execute the Agent code; treats its output as
  immutable.
* ``TASK_DEFINITION`` / ``ORACLE_INPUTS`` / ``ORACLE_EXPECTED`` —
  schema-valid fixture metadata.

The fixture is **synthetic** and clearly labelled.  Real tasks must
upgrade this family before any pilot run.
"""

FAMILY = "data_pipeline_csv"
DOMAIN = "software_data_pipeline"


STAGES = [
    {"stage": 1, "name": "csv_to_jsonl",
     "function": "csv_to_jsonl",
     "input": "broken_seed.csv", "output": "rows.jsonl",
     "expected": {"min_rows": 6, "columns": ["a", "b"]}},
    {"stage": 2, "name": "sum_columns",
     "function": "sum_columns",
     "input": "rows.jsonl", "output": "summary.csv",
     "expected": {"columns": ["a", "b"], "row_count": 1}},
    {"stage": 3, "name": "render_report",
     "function": "render_report",
     "input": "summary.csv", "output": "report.json",
     "expected": {"fields": ["sum_a", "sum_b"]}},
]


class StageError(AssertionError):
    pass


def _read_csv(path):
    import csv as _csv
    if not path.exists():
        raise StageError(f"missing: {path}")
    with path.open(encoding="utf-8") as f:
        return list(_csv.DictReader(f))


def _write_csv(path, rows):
    import csv as _csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def csv_to_jsonl(input_path, output_path):
    rows = _read_csv(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        import json as _json
        for row in rows:
            f.write(_json.dumps(row, ensure_ascii=False) + "\n")
    return {"rows": len(rows), "columns": list(rows[0].keys()) if rows else []}


def sum_columns(input_path, output_path):
    import json as _json
    if not input_path.exists():
        raise StageError(f"missing: {input_path}")
    sums: dict = {}
    n = 0
    with input_path.open(encoding="utf-8") as f:
        for line in f:
            row = _json.loads(line)
            for k, v in row.items():
                try:
                    sums[k] = sums.get(k, 0.0) + float(v)
                except (TypeError, ValueError):
                    continue
            n += 1
    if not sums:
        raise StageError("no numeric columns")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    import csv as _csv
    with output_path.open("w", encoding="utf-8", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=list(sums.keys()))
        w.writeheader()
        w.writerow({k: f"{v:.0f}" for k, v in sums.items()})
    return {"columns": list(sums.keys()), "row_count": 1, "n": n}


def render_report(input_path, output_path):
    import json as _json
    if not input_path.exists():
        raise StageError(f"missing: {input_path}")
    rows = _read_csv(input_path)
    if not rows:
        raise StageError("empty summary.csv")
    first = rows[0]
    report = {"sum_a": float(first.get("a", 0)),
              "sum_b": float(first.get("b", 0))}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_json.dumps(report, ensure_ascii=False),
                            encoding="utf-8")
    return {"fields": list(report.keys())}


# -----------------------------------------------------------------------------
# Fixture entry points (called by scripts/benchmark/run.py)
# -----------------------------------------------------------------------------


def prepare_initial_state(workspace, task_id: str) -> None:
    """Drop the task inputs under inputs/ — but NOT the expected outputs.

    The Agent must run the pipeline on the inputs and produce
    ``outputs/<task_id>/rows.jsonl``, ``summary.csv``, ``report.json``.
    """
    workspace = Path(workspace)
    inputs_dir = workspace / "inputs" / "fixtures" / FAMILY
    inputs_dir.mkdir(parents=True, exist_ok=True)
    # Write a "broken" CSV with two corrupted rows + a comment row.
    # The Agent must clean it before summing.
    broken_csv = (
        "a,b\n"
        "# corrupted comment row\n"
        "1,2\n"
        "3,4\n"
        "5,6\n"
        "7,8\n"
        "9,10\n"
        "11,12\n"
    )
    (inputs_dir / "broken_seed.csv").write_text(broken_csv, encoding="utf-8")
    # README for the Agent.
    (inputs_dir / "README.md").write_text(
        "Software pipeline task (synthetic).\n\n"
        "Inputs:\n"
        "* broken_seed.csv  -  contains a comment row that must be filtered.\n\n"
        "Required outputs (under outputs/<task_id>/):\n"
        "* rows.jsonl       -  filtered rows as JSONL\n"
        "* summary.csv      -  column sums\n"
        "* report.json      -  {sum_a, sum_b} from the summary\n\n"
        "Allowed tools: fs.read, fs.write, python3.\n"
        "Do NOT read from reference/ (read by scorer only).\n",
        encoding="utf-8",
    )


def reference_oracle(workspace, task_id: str) -> dict:
    """Run the reference pipeline in a directory the Agent cannot read.

    Writes ``reference/<task_id>/`` (the canonical expected output)
    and returns the path map so the scorer can compare.
    """
    workspace = Path(workspace)
    inputs_root = workspace / "inputs"
    ref_root = workspace / "reference" / task_id
    ref_root.mkdir(parents=True, exist_ok=True)
    # Step 1: filter out the comment row from broken_seed.csv.
    src = inputs_root / "fixtures" / FAMILY / "broken_seed.csv"
    rows = []
    import csv as _csv
    with src.open(encoding="utf-8") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            if (row.get("a") or "").lstrip("-").isdigit() and (row.get("b") or "").lstrip("-").isdigit():
                rows.append({"a": int(row["a"]), "b": int(row["b"])})
    if not rows:
        raise StageError("reference oracle: no valid rows in broken_seed.csv")
    _write_csv(ref_root / "expected_seed.csv", rows)
    # Step 2: JSONL
    csv_to_jsonl(ref_root / "expected_seed.csv", ref_root / "rows.jsonl")
    # Step 3: summary
    sum_columns(ref_root / "rows.jsonl", ref_root / "summary.csv")
    # Step 4: report
    render_report(ref_root / "summary.csv", ref_root / "report.json")
    return {
        "rows.jsonl": ref_root / "rows.jsonl",
        "summary.csv": ref_root / "summary.csv",
        "report.json": ref_root / "report.json",
    }


def score(workspace, task_id: str, agent_dir, reference_paths: dict) -> dict:
    """Read the Agent's frozen outputs and compare to reference.

    Does NOT execute the Agent code; treats its output as immutable.
    Returns a dict of per-stage pass / fail and an overall score.
    """
    workspace = Path(workspace)
    agent_dir = Path(agent_dir)
    result = {"stages": {}, "matched": 0, "expected": 3}
    for stage in STAGES:
        output_name = stage["output"]
        agent_path = agent_dir / output_name
        reference_path = reference_paths.get(output_name)
        from ._base import compare_files
        ok = compare_files(agent_path, reference_path) if reference_path else False
        result["stages"][output_name] = "pass" if ok else "fail"
        if ok:
            result["matched"] += 1
    result["score"] = result["matched"] / result["expected"] if result["expected"] else 0.0
    return result


TASK_DEFINITION = {
    "task_id": "sw_pipe_synth_v3",
    "family": FAMILY, "domain": DOMAIN,
    "schema_version": "benchmark/v1",
    "inputs": [f"fixtures/{FAMILY}/broken_seed.csv",
               f"fixtures/{FAMILY}/README.md"],
    "initial_state": {"working_dir": "episodes/sw_pipe_synth_v3/workspace"},
    "stages": STAGES,
    "success_criteria": [
        "outputs/rows.jsonl exists and matches reference",
        "outputs/summary.csv exists and matches reference",
        "outputs/report.json exists and matches reference",
    ],
    "annotations": [{
        "annotation_id": "ann_sw_pipe_synth_v3_01", "task_id": "sw_pipe_synth_v3",
        "label": "accepted",
        "evidence_refs": [f"fixtures/{FAMILY}/broken_seed.csv"],
        "annotation_pending": False, "schema_version": "benchmark/v1",
        "notes": "Synthetic v3; Agent must produce outputs; scorer reads them.",
    }],
    "notes": "Synthetic v3 fixture; Agent executes; reference produces expected under reference/.",
}


ORACLE_INPUTS = {
    f"fixtures/{FAMILY}/broken_seed.csv":
    "a,b\n# corrupted comment row\n1,2\n3,4\n5,6\n7,8\n9,10\n11,12\n",
    f"fixtures/{FAMILY}/README.md":
    "Software pipeline task (synthetic). See score() for expected outputs.\n",
}


ORACLE_EXPECTED = {
    "rows.jsonl": (
        '{"a": "1", "b": "2"}\n{"a": "3", "b": "4"}\n{"a": "5", "b": "6"}\n'
        '{"a": "7", "b": "8"}\n{"a": "9", "b": "10"}\n{"a": "11", "b": "12"}\n'
    ),
    "summary.csv": "a,b\n36,42\n",
    "report.json": '{"sum_a": 36.0, "sum_b": 42.0}',
}
