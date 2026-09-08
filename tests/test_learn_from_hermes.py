from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Add Partner to path so we can import the learn module
_PARTNER_ROOT = Path("/mnt/e/work/partner")
if str(_PARTNER_ROOT) not in sys.path:
    sys.path.insert(0, str(_PARTNER_ROOT))

from partner.learn import learn_from_hermes as lfh  # noqa: E402


HERMES_ROOT = lfh.HERMES_DEFAULT_ROOT


def test_bdk_import_works():
    """Accepted organs must be importable from Partner itself."""
    from partner.learn.bdk_constraints import apply_ocamms_constraint
    from partner.learn.bdk_function_pool import FunctionPool
    assert callable(apply_ocamms_constraint)
    assert callable(FunctionPool)


def test_module_constants_are_present():
    assert "partner_test" not in str(lfh.HERMES_DEFAULT_ROOT)
    assert lfh.KERNEL_NAMES == ["linear", "quadratic", "fourier", "expdecay"]
    import partner.learn.bdk_constraints as ov
    assert ov.apply_ocamms_constraint is not None


def test_scan_notes_finds_partner_owned_adopted_notes():
    notes = lfh.scan_notes(HERMES_ROOT)
    assert len(notes) >= 1
    paths = {str(n.relative_to(HERMES_ROOT)) for n in notes}
    assert any("adopted_harness_world_model" in p for p in paths)


def test_parse_note_extracts_metadata():
    note = HERMES_ROOT / "notes/adopted_harness_world_model.md"
    meta = lfh._parse_note(note)
    assert meta is not None
    assert "partner_test" in meta["source"].lower()
    assert meta["topic"]  # topic must be non-empty
    assert meta["title"]
    assert meta["fetched_at"]
    assert isinstance(meta["headings"], list)
    assert len(meta["headings"]) > 0


def test_parse_note_returns_dict_with_empty_fields_for_missing_metadata(tmp_path):
    # File with no metadata block still parses but with empty source/topic.
    # Caller (scan_notes + main) decides whether to skip; we verify the parser
    # is honest about the absence of fields.
    bad = tmp_path / "no_meta.md"
    bad.write_text("# Just a title\n\nNo metadata here.\n", encoding="utf-8")
    meta = lfh._parse_note(bad)
    assert meta is not None
    assert meta["source"] == ""
    assert meta["topic"] == ""
    assert meta["title"] == "Just a title"  # parsed from # heading


def test_category_for_topic_maps_correctly():
    cat, conf = lfh._category_for_topic("bayesian / in-context")
    assert cat == "bayesian_function_learning"
    assert conf >= 0.8

    cat, conf = lfh._category_for_topic("random unrelated text")
    assert cat == "agent_general"
    assert conf <= 0.6


def test_kernel_logits_length_matches_function_pool():
    """Logits length must equal number of kernels declared in BDK FunctionPool."""
    from partner.learn.bdk_function_pool import FunctionPool
    logits = lfh._kernel_logits_for("bayesian_function_learning")
    assert len(logits) == len(lfh.KERNEL_NAMES)
    assert len(logits) == len(FunctionPool.KERNEL_NAMES)


def test_build_skill_payload_includes_bdk_intervention(tmp_path):
    fake_note = HERMES_ROOT / "notes/adopted_harness_world_model.md"
    meta = lfh._parse_note(fake_note)
    assert meta is not None
    payload = lfh.build_skill_payload(meta, str(tmp_path), HERMES_ROOT)
    assert payload["status"] == "candidate"
    assert payload["artifact_type"] == "knowledge_draft"
    assert payload["execution_contract"]["ready"] is False
    # production_effective is added by register_candidate_skill, not by build.
    # We verify it on the registered record in test_register_real_writes_to_isolated_workspace.
    intervention = json.loads(payload["intervention"])
    assert intervention["bdk_module"] == "partner.learn.bdk_function_pool.FunctionPool"
    assert intervention["kernel_names"] == lfh.KERNEL_NAMES
    assert "ocamms_passed" in intervention
    assert "ocamms_message" in intervention
    assert payload["source_episode_ids"][0].startswith("hermes_external_learning:")
    assert any("manual_stable" in a for a in payload["applicability"])


def test_bdk_ocamms_validator_actually_invoked(tmp_path):
    """Sanity check: ocamms validator returns a bool and runs on our logits."""
    from partner.learn.bdk_constraints import apply_ocamms_constraint
    logits = lfh._kernel_logits_for("agent_framework")
    result = apply_ocamms_constraint(logits)
    assert isinstance(result, bool)
    # Some categories (agent_framework) have 3+ kernels > margin so should fail
    logits_simple = [0.9, 0.1, 0.1, 0.1]  # clearly passes
    assert apply_ocamms_constraint(logits_simple) is True


def test_register_in_isolated_workspace(tmp_path):
    """Run main() in dry_run against a tmp workspace to ensure no errors."""
    rc = lfh.main([
        "--workspace", str(tmp_path),
        "--source", str(HERMES_ROOT),
        "--dry-run",
    ])
    assert rc == 0


def test_register_real_writes_to_isolated_workspace(tmp_path):
    """Real registration against an isolated workspace creates candidate skill files."""
    rc = lfh.main([
        "--workspace", str(tmp_path),
        "--source", str(HERMES_ROOT),
    ])
    assert rc == 0
    skills_dir = tmp_path / "share/mind/governance/experience_guided_policy/candidate_skills"
    assert skills_dir.exists()
    files = [p for p in skills_dir.glob("*.json") if p.name != "revisions.jsonl"]
    assert len(files) >= 1
    # Verify source_episode_ids and status on every file
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["status"] == "candidate"
        assert data["production_effective"] is False
        assert data["artifact_type"] == "knowledge_draft"
        assert data["execution_ready"] is False
        assert data["source_episode_ids"]
        assert all(s.startswith("hermes_external_learning:")
                   for s in data["source_episode_ids"])
        intervention = json.loads(data["intervention"])
        assert intervention["bdk_module"] == "partner.learn.bdk_function_pool.FunctionPool"
