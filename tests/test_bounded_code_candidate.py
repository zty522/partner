from pathlib import Path
import re

from partner.evolution import bounded_code_candidate as candidate_module
from partner.evolution.bounded_code_candidate import (
    _candidate_source,
    _llm_critic,
    _llm_diagnose_candidate,
    _probe,
)
from partner.v2.continuous_project_events import atomic_continuous_project_step


def test_generated_code_candidate_changes_behavior_in_isolation(tmp_path):
    source = Path("partner/evolution/generated_candidate_policy.py").read_text(encoding="utf-8")
    baseline = tmp_path / "baseline.py"
    baseline.write_text(re.sub(
        r"POLICIES: dict\[str, str\] = \{.*?\n\}",
        "POLICIES: dict[str, str] = {}", source, count=1, flags=re.S),
        encoding="utf-8")
    candidate = tmp_path / "candidate.py"
    candidate.write_text(_candidate_source(baseline.read_text(encoding="utf-8")), encoding="utf-8")
    assert _probe(baseline)["passed"] is False
    assert _probe(candidate)["passed"] is True


def test_candidate_source_is_idempotent():
    original = Path("partner/evolution/generated_candidate_policy.py").read_text(encoding="utf-8")
    once = _candidate_source(original)
    assert _candidate_source(once) == once


def test_llm_diagnosis_is_bounded_to_candidate_options(monkeypatch):
    captured = {}
    monkeypatch.setattr(candidate_module, "_llm_json",
                        lambda prompt, **kwargs: captured.setdefault("value", {
                            "project_id": "literature_github_learning",
                            "recipe": "turn_source_rotation_v1",
                            "decision": "propose",
                            "causal_hypothesis": "rotation changes the next grounded source",
                        }))
    result = _llm_diagnose_candidate([
        (candidate_module.SPECS[1], {"trajectory_id": "t1"}),
    ], "POLICIES: dict[str, str] = {}")
    assert result["decision"] == "propose"
    assert result["project_id"] == "literature_github_learning"


def test_llm_critic_cannot_set_production_effective(monkeypatch):
    monkeypatch.setattr(candidate_module, "_llm_json", lambda *args, **kwargs: {
        "causal_isolation": "uncertain", "recommendation": "more_evidence",
        "missing_tests": ["cross-day replay"],
    })
    result = _llm_critic({
        "candidate_id": "c1", "project_id": "p", "production_effective": False,
        "baseline_probe": {"passed": False}, "candidate_probe": {"passed": True},
    })
    assert result["recommendation"] == "more_evidence"
    assert "production_effective" not in result


def test_code_candidate_does_not_depend_on_pdf_presentation(monkeypatch, tmp_path):
    class Ctx:
        workspace = str(tmp_path / "instances/05")
        working_dir = str(tmp_path / "task")
        instance_id = "05"
    Path(Ctx.working_dir).mkdir(parents=True)
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    patch = candidate_dir / "candidate.patch"
    report = candidate_dir / "result.json"
    patch.write_text("real behavioral diff", encoding="utf-8")
    report.write_text('{"production_effective": true}', encoding="utf-8")
    monkeypatch.setattr(
        "partner.v2.continuous_project_events._hermes_partner_step",
        lambda *_: {"ok": True, "decision": "applied",
                    "production_effective": True, "summary": "real code gate passed",
                    "files": [str(patch), str(report)]},
    )
    result = atomic_continuous_project_step(Ctx(), {
        "strategy_id": "05_code_candidate_autonomous",
    })
    assert result["ok"] is True
    assert result["status"] == "applied"
    assert len(result["files"]) == 2
    assert all(Path(value).parent == Path(Ctx.working_dir) for value in result["files"])
