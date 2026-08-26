from pathlib import Path

from partner.governance.campaign import create_campaign, enqueue_work_item
from partner.governance.campaign_models import CampaignBudget
from partner.governance.campaign_storage import save_work_item
from partner.governance.external_catalog import build_external_catalog
from partner.governance.rl_evolution import run_offline_rl_update


def test_offline_rl_uses_verifiable_outcomes_and_never_auto_promotes(tmp_path):
    root = tmp_path / "workspace"
    state = create_campaign(str(root), goal="test", allowed_instances=["03"], duration_seconds=60,
                            budget=CampaignBudget(max_work_items=10, max_runtime_seconds=60))
    artifact = root / "evidence.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("evidence", encoding="utf-8")
    item = enqueue_work_item(str(root), state.campaign_id, {
        "instance_id": "03", "project_id": "framework", "kind": "audit", "title": "audit",
        "instruction": "audit", "requires_delivery": True,
    })
    item.status = "completed"; item.task_id = "task"; item.attempt = 1
    item.artifacts = [str(artifact)]; item.event_types = ["framework_contract_audit"]
    item.evidence = ["delivery_confirmed=True"]
    save_work_item(str(root), item)
    result = run_offline_rl_update(str(root), state.campaign_id)
    assert result["ok"] and result["new_trajectories"] == 1
    # Delivery/audit quality remains auditable, but monitoring and self-audits
    # are no longer allowed to train the business-action policy.
    assert result["policy"]["actions"] == []
    import json
    row = json.loads(Path(result["trajectory_path"]).read_text(encoding="utf-8").splitlines()[-1])
    assert row["policy_eligible"] is False
    assert row["outcome"]["business_progress"] is False
    assert result["policy"]["automatic_production_promotion"] is False


def test_external_catalog_marks_sources_indexed_not_integrated(tmp_path):
    root = tmp_path / "workspace"
    source = root / "external/code/RLVR-World-main/README.md"
    source.parent.mkdir(parents=True)
    source.write_text("RLVR", encoding="utf-8")
    result = build_external_catalog(str(root))
    rlvr = next(row for row in result["sources"] if row["source_id"] == "rlvr-world")
    assert rlvr["integration_status"] == "indexed"
    assert rlvr["execution_allowed"] is False
    assert result["summary"]["integrated"] == 0


def test_external_catalog_records_harness_provenance_without_execution(tmp_path):
    root = tmp_path / "workspace"
    deepseek = root / "external/code/deepseek-harness/docs/architecture.md"
    codex = root / "external/code/openai-codex/codex-rs/rollout-trace/README.md"
    deepseek.parent.mkdir(parents=True)
    codex.parent.mkdir(parents=True)
    deepseek.write_text("event sourced session", encoding="utf-8")
    codex.write_text("observe first, interpret later", encoding="utf-8")
    hermes = root / "external/code/hermes-agent/agent/trajectory.py"
    openclaw = root / "external/code/openclaw/docs/agent-runtime-architecture.md"
    hermes.parent.mkdir(parents=True)
    openclaw.parent.mkdir(parents=True)
    hermes.write_text("trajectory", encoding="utf-8")
    openclaw.write_text("session authority", encoding="utf-8")

    result = build_external_catalog(str(root))
    records = {row["source_id"]: row for row in result["sources"]}
    assert records["deepseek-harness"]["pinned_revision"].startswith("b150a551")
    assert records["deepseek-harness"]["license"] == "MIT"
    assert records["openai-codex"]["pinned_revision"].startswith("76d98a77")
    assert records["openai-codex"]["license"] == "Apache-2.0"
    assert records["hermes-agent"]["pinned_revision"].startswith("9d059cfa")
    assert records["openclaw"]["pinned_revision"].startswith("97196164")
    assert records["hermes-agent"]["license"] == "MIT"
    assert records["openclaw"]["license"] == "MIT"
    assert records["deepseek-harness"]["integration_status"] == "indexed"
    assert records["openai-codex"]["execution_allowed"] is False
    assert records["hermes-agent"]["execution_allowed"] is False
    assert records["openclaw"]["integration_status"] == "indexed"


def test_offline_rl_rewards_novel_evidence_and_consumed_handoff(tmp_path):
    root = tmp_path / "workspace"
    state = create_campaign(str(root), goal="continuous", allowed_instances=["02"], duration_seconds=60,
                            budget=CampaignBudget(max_work_items=10, max_runtime_seconds=60))
    artifact = root / "stage10.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"lineage":{"consumed":true}}', encoding="utf-8")
    item = enqueue_work_item(str(root), state.campaign_id, {
        "instance_id": "02", "project_id": "molecular_generation", "kind": "project_iteration",
        "title": "stage10", "instruction": "continuous", "requires_delivery": True,
    })
    item.status = "completed"; item.task_id = "task"; item.attempt = 1
    item.artifacts = [str(artifact)]; item.event_types = ["targetdiff_target_balanced_metrics"]
    item.evidence = [
        "delivery_confirmed=True", "outcome_fingerprint=unique-stage10",
        "business_progress=true", "monitor_only=false",
    ]
    save_work_item(str(root), item)
    result = run_offline_rl_update(str(root), state.campaign_id)
    # run_offline_rl_update exposes policy, so read the auditable trajectory file.
    import json
    row = json.loads(Path(result["trajectory_path"]).read_text(encoding="utf-8").splitlines()[-1])
    assert row["reward_components"]["business_progress"] == 0.45
    assert row["reward_components"]["novel_evidence"] == 0.20
    assert row["reward_components"]["handoff_consumed"] == 0.15
    assert row["outcome"]["handoff_consumed"] is True


def test_no_change_scout_cannot_create_false_rl_novelty(tmp_path):
    root = tmp_path / "workspace"
    state = create_campaign(str(root), goal="monitor", allowed_instances=["01"], duration_seconds=60,
                            budget=CampaignBudget(max_work_items=10, max_runtime_seconds=60))
    artifact = root / "scout.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"records":2,"duplicates":1}', encoding="utf-8")
    item = enqueue_work_item(str(root), state.campaign_id, {
        "instance_id": "01", "project_id": "xiaohongshu_operations", "kind": "audit",
        "title": "scout", "instruction": "[portfolio_scout=true] scout", "requires_delivery": False,
    })
    item.status = "completed"; item.task_id = "task"; item.attempt = 1
    item.artifacts = [str(artifact)]; item.event_types = ["evidence_execution_slice"]
    item.evidence = [
        "outcome_fingerprint=scout-same", "business_progress=false",
        "monitor_only=true", "no_change=true",
    ]
    save_work_item(str(root), item)
    result = run_offline_rl_update(str(root), state.campaign_id)
    import json
    row = json.loads(Path(result["trajectory_path"]).read_text(encoding="utf-8").splitlines()[-1])
    assert row["policy_eligible"] is False
    assert row["outcome"]["novel_evidence"] is False
    assert "business_progress" not in row["reward_components"]
