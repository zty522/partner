import importlib.util
from pathlib import Path


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "simulate_campaign_soak.py"
_SPEC = importlib.util.spec_from_file_location("simulate_campaign_soak", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
run_simulation = _MODULE.run_simulation


def test_deterministic_campaign_soak():
    result = run_simulation(24)
    assert result["ok"] is True
    assert result["max_slots_observed"] <= 2
    assert result["tasks_dispatched"] >= 5
    assert result["final_status"] == "completed"
