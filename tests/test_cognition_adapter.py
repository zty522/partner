from __future__ import annotations

import hashlib
import json

import pytest

from partner.governance.candidate_skills import register_candidate_skill
from partner.governance.cognition_adapter import (
    candidate_skill_draft_from_cognition,
    correct_cognition_shadow_import,
    import_cognition_shadow,
    validate_cognition_shadow_bundle,
)


def bundle():
    value = {
        "schema_version": 1,
        "kind": "partner_cognition_shadow",
        "project_id": "partner-framework",
        "cognition_episode_id": "cog-1",
        "partner_instance_id": "03",
        "partner_task_id": "task-1",
        "source_episode_ids": ["episode_abc"],
        "ledger": {"sha256": "a" * 64, "head_hash": "b" * 64, "event_count": 2},
        "reduced_state": {
            "project_id": "partner-framework", "episode_id": "cog-1", "event_count": 2,
            "replay_digest": "c" * 64, "unresolved_actions": [],
        },
        "candidate_registration_allowed": False,
        "production_mutation_allowed": False,
        "manual_stable_override": False,
    }
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value["bundle_digest"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return value


def write_source_episode(workspace, value):
    episode_id = value["source_episode_ids"][0]
    path = workspace / f"share/mind/governance/episodes/{episode_id}/state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 3, "episode_id": episode_id,
        "task_id": value["partner_task_id"], "instance_id": value["partner_instance_id"],
        "project_id": value["project_id"],
    }), encoding="utf-8")


def test_validate_refuses_activation_or_tampering():
    active = bundle()
    active["production_mutation_allowed"] = True
    with pytest.raises(ValueError, match="production mutation"):
        validate_cognition_shadow_bundle(active)
    tampered = bundle()
    tampered["reduced_state"]["event_count"] = 3
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_cognition_shadow_bundle(tampered)


def test_import_is_shadow_only_and_idempotent(tmp_path):
    workspace = tmp_path / "workspace"
    value = bundle()
    write_source_episode(workspace, value)
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(value), encoding="utf-8")
    first = import_cognition_shadow(str(workspace), source)
    second = import_cognition_shadow(str(workspace), source)
    assert first["status"] == "shadow_imported"
    assert second["status"] == "already_imported"
    assert first["production_mutation"] is False
    imports = workspace / "share/mind/governance/cognition_shadow/imports.jsonl"
    assert len(imports.read_text(encoding="utf-8").splitlines()) == 1


def test_candidate_draft_matches_registry_without_automatic_registration(tmp_path):
    value = bundle()
    workspace = tmp_path / "registry"
    write_source_episode(workspace, value)
    draft = candidate_skill_draft_from_cognition(
        str(workspace), value,
        candidate_id="candidate_cognition_context_v1",
        title="认知事件上下文选择 shadow",
        intervention="shadow-only context selection annotation",
        applicability=["manual_stable", "evidence-heavy tasks"],
        success_criteria=["truth remains 1", "no observability regression"],
    )
    assert draft["status"] == "shadow"
    assert draft["source_episode_ids"] == ["episode_abc"]
    candidate_dir = workspace / "share/mind/governance/experience_guided_policy/candidate_skills"
    assert not candidate_dir.exists()
    registered = register_candidate_skill(str(workspace), draft)
    assert registered["candidate"]["status"] == "shadow"
    assert registered["candidate"]["source_episode_ids"] == ["episode_abc"]


def test_import_requires_existing_task_linked_partner_episode(tmp_path):
    value = bundle()
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="missing or invalid"):
        import_cognition_shadow(str(tmp_path / "workspace"), source)
    workspace = tmp_path / "mismatched"
    write_source_episode(workspace, value)
    state_path = workspace / "share/mind/governance/episodes/episode_abc/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["task_id"] = "different-task"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="matches partner_task_id"):
        import_cognition_shadow(str(workspace), source)


def test_import_requires_exact_project_and_corrections_are_append_only(tmp_path):
    value = bundle()
    workspace = tmp_path / "workspace"
    write_source_episode(workspace, value)
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(value), encoding="utf-8")
    imported = import_cognition_shadow(str(workspace), source)
    correction = correct_cognition_shadow_import(
        str(workspace), import_id=imported["import_id"], action="invalidate",
        reason="identity mapping was incorrect",
    )
    assert correction["action"] == "invalidate"
    archive = workspace / f"share/mind/governance/cognition_shadow/{imported['import_id']}.json"
    assert archive.is_file()
    assert "identity mapping" in (
        workspace / "share/mind/governance/cognition_shadow/corrections.jsonl"
    ).read_text(encoding="utf-8")

    mismatched = bundle()
    mismatched["project_id"] = "other-project"
    mismatched["reduced_state"]["project_id"] = "other-project"
    raw = json.dumps({key: item for key, item in mismatched.items() if key != "bundle_digest"},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    mismatched["bundle_digest"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with pytest.raises(ValueError, match="project_id"):
        candidate_skill_draft_from_cognition(
            str(workspace), mismatched, candidate_id="candidate_bad", title="bad",
            intervention="bad", applicability=["manual_stable"], success_criteria=["truth=1"],
        )
