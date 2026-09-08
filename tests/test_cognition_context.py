from __future__ import annotations

import hashlib
import json

from partner.governance.cognition_context import (
    cognition_document_ranking,
    load_valid_cognition_shadows,
    run_context_gate_c_preflight,
    select_cognition_context,
)
from partner.governance.models import IterationReceipt, NextAction
from partner.governance.storage import save_receipt


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _archive(workspace, *, import_id="cognition_shadow_test", corrected=False):
    bundle = {
        "schema_version": 1, "kind": "partner_cognition_shadow",
        "project_id": "literature_github_learning", "cognition_episode_id": "cog:episode_ctx",
        "partner_instance_id": "04", "partner_task_id": "task_ctx",
        "source_episode_ids": ["episode_ctx"],
        "ledger": {"sha256": "a" * 64, "head_hash": "b" * 64, "event_count": 2},
        "reduced_state": {
            "schema_version": 1, "project_id": "literature_github_learning",
            "episode_id": "cog:episode_ctx", "event_count": 2, "replay_digest": "c" * 64,
            "percepts": {"p": {"payload": {"status": "completed", "failure_classes": []}}},
            "unresolved_actions": [],
        },
        "candidate_registration_allowed": False, "production_mutation_allowed": False,
        "manual_stable_override": False,
    }
    bundle["bundle_digest"] = _digest(bundle)
    import_id = f"cognition_shadow_{bundle['bundle_digest'][:16]}"
    source = workspace / "share/mind/governance/episodes/episode_ctx/state.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({
        "schema_version": 3, "episode_id": "episode_ctx", "task_id": "task_ctx",
        "instance_id": "04", "project_id": "literature_github_learning",
    }), encoding="utf-8")
    directory = workspace / "share/mind/governance/cognition_shadow"
    directory.mkdir(parents=True)
    (directory / f"{import_id}.json").write_text(json.dumps({
        "schema_version": 1, "import_id": import_id, "production_mutation": False,
        "candidate_registered": False, "status": "shadow_imported", "bundle": bundle,
    }), encoding="utf-8")
    if corrected:
        (directory / "corrections.jsonl").write_text(json.dumps({
            "import_id": import_id, "action": "invalidate",
        }) + "\n", encoding="utf-8")


def _tasks():
    return [
        {"match_key": f"gate-c-{index}", "query": f"继续第 {index} 个项目证据任务",
         "instance_id": "04", "project_id": "literature_github_learning"}
        for index in range(1, 8)
    ]


def test_cognition_ranking_uses_only_valid_identity_matched_archives(tmp_path):
    workspace = tmp_path / "workspace"
    _archive(workspace)
    result = cognition_document_ranking(
        str(workspace), "继续项目", instance_id="04", project_id="literature_github_learning",
        catalog_path="docs/catalog.yaml",
    )
    assert result["source_episode_ids"] == ["episode_ctx"]
    assert result["selected_document_ids"]
    assert any("github" in reason["cognition_overlap"] or "literature" in reason["cognition_overlap"]
               for reason in result["selection_reasons"])

    other = cognition_document_ranking(
        str(workspace), "继续项目", instance_id="05", project_id="literature_github_learning",
        catalog_path="docs/catalog.yaml",
    )
    assert other["source_episode_ids"] == []
    assert other["selected_document_ids"] == []


def test_invalidated_shadow_is_not_context_evidence(tmp_path):
    workspace = tmp_path / "workspace"
    _archive(workspace, corrected=True)
    assert load_valid_cognition_shadows(
        str(workspace), project_id="literature_github_learning", instance_id="04",
    ) == []


def test_candidate_selection_is_deterministic_and_budgeted(tmp_path):
    workspace = tmp_path / "workspace"
    _archive(workspace)
    first = select_cognition_context(
        str(workspace), "继续当前项目", instance_id="04", project_id="literature_github_learning",
        budget_chars=9000, catalog_path="docs/catalog.yaml",
    )
    second = select_cognition_context(
        str(workspace), "继续当前项目", instance_id="04", project_id="literature_github_learning",
        budget_chars=9000, catalog_path="docs/catalog.yaml",
    )
    assert first["selection_digest"] == second["selection_digest"]
    assert first["budget_used"] <= first["budget_chars"]
    assert first["production_mutation"] is False
    assert first["candidate_registered"] is False


def test_gate_c_preflight_requires_seven_pairs(tmp_path):
    workspace = tmp_path / "workspace"
    _archive(workspace)
    try:
        run_context_gate_c_preflight(
            str(workspace), experiment_id="experiment_short", tasks=_tasks()[:6],
            catalog_path="docs/catalog.yaml",
        )
    except ValueError as exc:
        assert "at least 7" in str(exc)
    else:
        raise AssertionError("short Gate C preflight was accepted")


def test_gate_c_preflight_registers_shadow_but_makes_no_quality_claim(tmp_path):
    workspace = tmp_path / "workspace"
    _archive(workspace)
    save_receipt(str(workspace), IterationReceipt(
        project_id="literature_github_learning", iteration=1, goal="previous",
        inputs=[], actions_executed=["inspect"], artifacts=["previous.md"], findings=["evidence"],
        next_actions=[NextAction(title="continue", event_type="inspect")], delivery_confirmed=True,
    ))
    result = run_context_gate_c_preflight(
        str(workspace), experiment_id="experiment_gate_c", tasks=_tasks(),
        catalog_path="docs/catalog.yaml", register_shadow=True,
    )
    assert result["mechanical_gate_passed"] is True
    assert result["pairs"] == 7
    assert result["intervention_isolated"] is True
    assert result["independent_executed_tasks"] is False
    assert result["causal_status"] == "not_executed_no_quality_claim"
    assert result["promotion"] is False
    assert result["candidate_skill"]["status"] == "shadow"
    assert result["candidate_skill"]["production_effective"] is False
    assert result["cognition_route_used"] is True
    for pair in result["pairs_detail"]:
        assert pair["checks"]["project_continuity_preserved"] is True
        assert pair["checks"]["baseline_query_relevance_preserved"] is True
        assert any(ref.startswith("latest_receipt:") for ref in pair["candidate"]["selected_context_refs"])
