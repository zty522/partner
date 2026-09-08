from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.v2.targetdiff_bdk_events import (  # noqa: E402
    HANDLERS,
    atomic_targetdiff_bdk_stage,
    atomic_targetdiff_sklearn_baseline,
)


_HAS_REAL_DATA = Path("/mnt/e/work/partner_workspace/external/targetdiff/data/affinity_info.pkl").exists()


class MockCtx:
    def __init__(self, workspace: str, working_dir: str):
        self.workspace = workspace
        self.working_dir = working_dir
        self.task_instance = None


def test_handler_is_registered():
    """The BDK handler must be in HANDLERS dict."""
    assert "targetdiff_bdk_function_pool" in HANDLERS
    assert HANDLERS["targetdiff_bdk_function_pool"] is atomic_targetdiff_bdk_stage
    assert HANDLERS["targetdiff_sklearn_affinity_baseline"] is atomic_targetdiff_sklearn_baseline


def test_bdk_implementation_is_partner_owned():
    from partner.learn.bdk_function_pool import FunctionPool
    assert FunctionPool.KERNEL_NAMES == ["linear", "quadratic", "fourier", "expdecay"]


@pytest.mark.skipif(not _HAS_REAL_DATA,
                    reason="TargetDiff data not present in workspace")
def test_stage_returns_bdk_unavailable_when_bdk_missing(monkeypatch, tmp_path):
    """If BDK is not importable, the stage must fail cleanly."""
    # Force ImportError by removing BDK from sys.path
    # Re-import the module after monkeypatch... actually just call handler
    # Simulate an unavailable Partner-owned implementation.
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name == "partner.learn.bdk_function_pool":
            raise ImportError("simulated missing BDK")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    ctx = MockCtx("/mnt/e/work/partner_workspace", str(tmp_path))
    result = atomic_targetdiff_bdk_stage(ctx, {"run_id": "smoke_unavail"})
    assert result["ok"] is False
    assert result["status"] == "bdk_unavailable"
    assert "BDK" in result["error"]


@pytest.mark.skipif(not _HAS_REAL_DATA,
                    reason="TargetDiff data not present in workspace")
def test_stage_returns_missing_input_when_no_pkl(tmp_path):
    """If affinity_info.pkl is missing, return clear error."""
    # Use a workspace that doesn't have the data
    fake_ws = tmp_path / "fake_ws"
    fake_ws.mkdir()
    ctx = MockCtx(str(fake_ws), str(tmp_path / "out"))
    result = atomic_targetdiff_bdk_stage(ctx, {"run_id": "no_data"})
    assert result["ok"] is False
    assert result["status"] == "missing_input"
    assert "affinity_info.pkl" in result["error"]


@pytest.mark.skipif(not _HAS_REAL_DATA,
                    reason="TargetDiff data not present in workspace")
def test_stage_runs_end_to_end_and_writes_outputs(tmp_path):
    """Happy path: run with synthetic working dir, verify outputs exist."""
    out_dir = tmp_path / "bdk_outputs"
    out_dir.mkdir()
    ctx = MockCtx("/mnt/e/work/partner_workspace", str(out_dir))
    result = atomic_targetdiff_bdk_stage(ctx, {
        "epochs": 20,  # small for fast test
        "run_id": "e2e",
    })
    assert result["ok"] is True, f"stage failed: {result.get('error', result)}"
    assert result["status"] == "completed"
    assert result["method"] == "bdk_function_pool"
    r = result["result"]
    # Real-data sanity
    assert r["row_records"] > 1000, f"expected real TargetDiff rows, got {r['row_records']}"
    assert len(r["folds"]) == 5
    # 5-fold group-disjoint
    for fold in r["folds"]:
        assert fold["group_overlap"] == 0
        assert fold["bdk"]["count"] > 0
    # Kernel probs are valid
    assert len(r["mean_kernel_probs"]) == 4
    for p in r["mean_kernel_probs"]:
        assert 0.0 <= p <= 1.0
    # ocamms flag present
    assert "ocamms_all_passed" in r
    # Files written
    files = result["files"]
    assert any(f.endswith(".py") for f in files), "no .py source"
    assert any(f.endswith(".json") for f in files), "no .json result"
    assert any(f.endswith(".md") for f in files), "no .md report"
    # The generated script imports only Partner-owned code.
    py_source = next(f for f in files if f.endswith(".py"))
    content = Path(py_source).read_text(encoding="utf-8")
    assert "from partner.learn.bdk_function_pool_fitter" in content
    assert "partner_test" not in content
    # Result JSON parses
    json_path = next(f for f in files if f.endswith(".json"))
    parsed = json.loads(Path(json_path).read_text(encoding="utf-8"))
    assert parsed["method"] == "bdk_function_pool"


@pytest.mark.skipif(not _HAS_REAL_DATA,
                    reason="TargetDiff data not present in workspace")
def test_stage_supports_data_driven_mask_opt_in(monkeypatch, tmp_path):
    """When params['data_driven_mask']=True, the subprocess gets
    --data-driven-mask 1 and uses kernel-mask-optimizer-recommended mask."""
    from pathlib import Path
    ws = "/mnt/e/work/partner_workspace"
    out_dir = tmp_path / "bdk_data_driven"
    out_dir.mkdir()

    class MockCtx:
        workspace = ws
        working_dir = str(out_dir)
        task_instance = None

    ctx = MockCtx()
    result = atomic_targetdiff_bdk_stage(ctx, {
        "epochs": 30, "run_id": "data_driven_test",
        "data_driven_mask": True,
    })
    assert result["ok"] is True
    r = result["result"]
    py_source = next(f for f in result["files"] if f.endswith(".py"))
    src_text = Path(py_source).read_text(encoding="utf-8")
    assert "data_driven_mask" in src_text
    assert "recommend_kernel_mask" in src_text


def test_stage_does_not_replace_existing_pipeline():
    """The BDK stage must NOT touch stages 9-13 — they remain on sklearn."""
    from partner.v2 import targetdiff_continuous_events
    # Stage 9-13 handler must still be the original sklearn-based one.
    assert "targetdiff_method_decision" in targetdiff_continuous_events.HANDLERS
    # And the original PIPELINE string must still reference sklearn.
    assert "LinearRegression" in targetdiff_continuous_events.PIPELINE
    assert "HistGradientBoostingRegressor" in targetdiff_continuous_events.PIPELINE
    # And the BDK file must not import or modify those stages.
    from partner.v2 import targetdiff_bdk_events
    assert not hasattr(targetdiff_bdk_events, "atomic_targetdiff_continuous_stage")
