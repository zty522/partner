"""Synthetic scientific_design fixture (M3 / Domain "scientific_analysis_planning") — v1.

Four-phase separation:
* ``prepare_initial_state`` — write task inputs.
* ``reference_oracle``      — compute expected outputs under reference/.
* ``score``                  — read Agent's frozen outputs and compare.
"""

from pathlib import Path
from ._base import (
    StageError, read_csv, write_csv, read_jsonl, write_jsonl,
    write_json, read_json, fingerprint_file, compare_files, aggregate_score,
)

FAMILY = "scientific_design"
DOMAIN = "scientific_analysis_planning"


STAGES = [{'stage': 1, 'name': 'load_data', 'function': 'load_data', 'input': 'input.csv', 'output': 'loaded.json', 'expected': {'row_count': 12}}, {'stage': 2, 'name': 'compute_power', 'function': 'compute_power', 'input': 'loaded.json', 'output': 'power.json', 'expected': {'fields': ['power', 'alpha']}}, {'stage': 3, 'name': 'plan', 'function': 'plan', 'input': 'power.json', 'output': 'plan.json', 'expected': {'fields': ['sample_size']}}]


def prepare_initial_state(workspace, task_id: str) -> None:
    workspace = Path(workspace)
    inputs_dir = workspace / "inputs" / "fixtures" / FAMILY
    inputs_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    text = "A,5.1\nA,5.3\nA,5.0\nA,5.2\nA,5.4\nA,5.1\nB,5.7\nB,5.9\nB,5.6\nB,5.8\nB,6.0"
    for line in text.split("\n"):
        cells = line.split(",")
        try:
            rows.append({"subject": cells[0], "score": float(cells[1])})
        except (ValueError, IndexError):
            continue
    write_csv(inputs_dir / "input.csv", rows)
    (inputs_dir / "README.md").write_text(
        "# " + FAMILY + " task (synthetic).\n\n"
        "Inputs:\n"
        "* input.csv - subject,score rows.\n\n"
        "Required outputs (outputs/<task_id>/):\n"
        + "".join(f"* {s['output']}\n" for s in STAGES)
        + "\nDo NOT read from reference/.\n",
        encoding="utf-8",
    )


def reference_oracle(workspace, task_id: str) -> dict:
    workspace = Path(workspace)
    ref_root = workspace / "reference" / task_id
    ref_root.mkdir(parents=True, exist_ok=True)
    inputs_dir = workspace / "inputs" / "fixtures" / FAMILY
    rows = read_csv(inputs_dir / "input.csv")
    summary = {}
    for r in rows:
        s = r["subject"]
        if s not in summary:
            summary[s] = {"n": 0, "sum": 0.0}
        summary[s]["n"] += 1
        summary[s]["sum"] += float(r["score"])
    summary_rows = [{"subject": k, "mean": v["sum"] / v["n"], "n": v["n"]}
                   for k, v in summary.items()]
    write_csv(ref_root / "summary.csv", summary_rows)
    write_json(ref_root / "report.json", {
        "subjects": summary_rows,
        "method": "mean_by_subject",
    })
    return {"summary.csv": ref_root / "summary.csv",
            "report.json": ref_root / "report.json"}


def score(workspace, task_id: str, agent_dir, reference_paths: dict) -> dict:
    workspace = Path(workspace)
    agent_dir = Path(agent_dir)
    per_output_pass = {}
    for output_name, ref_path in reference_paths.items():
        agent_path = agent_dir / output_name
        per_output_pass[output_name] = "pass" if compare_files(agent_path, ref_path) else "fail"
    return aggregate_score(per_output_pass)


TASK_DEFINITION = {
    "task_id": "sci_design_v1",
    "family": FAMILY,
    "domain": DOMAIN,
    "schema_version": "benchmark/v1",
    "inputs": ['fixtures/scientific_design/input.csv', 'fixtures/scientific_design/README.md'],
    "initial_state": {"working_dir": "episodes/sci_design_v1/workspace"},
    "stages": STAGES,
    "success_criteria": ['loaded.json matches reference', 'power.json matches reference', 'plan.json matches reference'],
    "annotations": [{
        "annotation_id": "ann_sci_design_v1_01",
        "task_id": "sci_design_v1",
        "label": "needs_human",
        "evidence_refs": ['fixtures/scientific_design/input.csv', 'fixtures/scientific_design/README.md'],
        "annotation_pending": True,
        "schema_version": "benchmark/v1",
        "notes": "Synthetic v1 fixture; pending expert review.",
    }],
    "notes": "Synthetic v1 fixture; placeholder for pilot upgrade.",
}


ORACLE_INPUTS = {
    f"fixtures/{FAMILY}/input.csv": "A,5.1\nA,5.3\nA,5.0\nA,5.2\nA,5.4\nA,5.1\nB,5.7\nB,5.9\nB,5.6\nB,5.8\nB,6.0",
    f"fixtures/{FAMILY}/README.md": "see prepare_initial_state",
}


ORACLE_EXPECTED = {"see reference/"}
