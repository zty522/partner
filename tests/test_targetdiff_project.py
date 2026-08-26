import json
import pickle
from pathlib import Path
from types import SimpleNamespace

from partner.v2 import pdf_events
from partner.v2.targetdiff_project_events import HANDLERS, atomic_targetdiff_project_slice
from partner.v2.targetdiff_continuous_events import HANDLERS as CONTINUOUS_HANDLERS
from partner.v2.targetdiff_official_split_events import HANDLERS as OFFICIAL_SPLIT_HANDLERS


def test_targetdiff_stage_runs_group_safe_pk_baseline(tmp_path, monkeypatch):
    source = tmp_path / "external" / "targetdiff" / "data" / "affinity_info.pkl"
    source.parent.mkdir(parents=True)
    rows = {}
    for group in range(80):
        for ligand in range(3):
            vina = -5.0 - group / 20 - ligand / 10
            rows[f"target_{group}/ligand_{ligand}"] = {
                "pk": 4.0 - 0.4 * vina + ligand / 20,
                "vina": vina,
                "rmsd": 0.2 + ligand / 10,
            }
    rows["target_missing/ligand"] = {"pk": 0.0, "vina": -7.0, "rmsd": 0.5}
    with source.open("wb") as handle:
        pickle.dump(rows, handle)

    working = tmp_path / "instances" / "02" / "state" / "tasks" / "task"
    working.mkdir(parents=True)
    ctx = SimpleNamespace(
        workspace=str(tmp_path / "instances" / "02"), working_dir=str(working),
        task_instance=SimpleNamespace(working_dir=str(working)),
    )

    def fake_pdf(_ctx, params):
        path = Path(params["output_path"])
        path.write_bytes(b"pdf")
        return {"ok": True, "path": str(path), "quality": {"plain_chars": 1200}}

    monkeypatch.setattr(pdf_events, "atomic_generate_detailed_pdf", fake_pdf)
    result = atomic_targetdiff_project_slice(ctx, {"stage": 2})
    assert result["ok"] is True
    payload = json.loads((working / "targetdiff_stage_2_result.json").read_text(encoding="utf-8"))
    assert payload["contract"]["target_field"] == "pk"
    assert payload["contract"]["feature_fields"] == ["vina", "rmsd"]
    assert payload["measured_records"] == 240
    assert payload["zero_or_invalid_pk"] == 1
    assert payload["group_overlap"] == 0
    assert payload["identity_leakage_check"]["passed"] is True
    assert set(payload["baselines"]) == {"train_mean", "vina_linear", "vina_rmsd_linear"}
    assert (working / "targetdiff_stage_2.py").is_file()
    assert (working / "targetdiff_stage_2_report.pdf").is_file()


def test_targetdiff_named_handlers_pin_stages():
    assert {
        "targetdiff_data_contract", "targetdiff_group_baseline", "targetdiff_nonlinear_compare",
        "targetdiff_residual_analysis", "targetdiff_group_cv",
    } <= set(HANDLERS)
    assert {
        "targetdiff_ligand_aggregation_cv", "targetdiff_target_balanced_metrics",
        "targetdiff_failure_group_diagnostics", "targetdiff_group_bootstrap",
        "targetdiff_method_decision",
    } == set(CONTINUOUS_HANDLERS)
    assert {
        "targetdiff_official_split_benchmark", "targetdiff_official_split_bootstrap",
        "targetdiff_official_split_calibration", "targetdiff_official_split_error_slices",
    } == set(OFFICIAL_SPLIT_HANDLERS)
