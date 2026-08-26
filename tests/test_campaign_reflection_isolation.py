from pathlib import Path
from types import SimpleNamespace
import json

import partner.core  # initialize core before mind; package currently has a known import cycle
from partner.mind.harness import _maybe_trigger_self_reflect_after_write
from partner.mind.executor import _batch_plan_dedup_key


def test_campaign_artifact_does_not_inject_legacy_reflection_task(tmp_path):
    workspace = tmp_path / "workspace/instances/03"
    state = workspace / "state"
    state.mkdir(parents=True)
    artifact = state / "tasks/task/report.pdf"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("report", encoding="utf-8")
    ctx = SimpleNamespace(
        workspace=str(workspace),
        task_instance=SimpleNamespace(
            workspace=str(workspace),
            user_message="[PARTNER_CAMPAIGN campaign_id=c work_item_id=w] bounded",
        ),
    )
    _maybe_trigger_self_reflect_after_write(ctx, str(artifact), "verified report")
    assert not (state / "desktop_inbox.jsonl").exists()


def test_non_campaign_artifact_keeps_legacy_reflection_hook(tmp_path):
    workspace = tmp_path / "workspace/instances/03"
    state = workspace / "state"
    state.mkdir(parents=True)
    config_dir = tmp_path / "workspace/config"
    config_dir.mkdir(parents=True)
    (config_dir / "partner_config.json").write_text(json.dumps({
        "workspace": {"path": str(tmp_path / "workspace")},
        "runtime": {"mode": "experimental", "automatic_self_heal": True},
    }), encoding="utf-8")
    artifact = state / "tasks/task/report.pdf"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("report", encoding="utf-8")
    ctx = SimpleNamespace(
        workspace=str(workspace),
        task_instance=SimpleNamespace(workspace=str(workspace), user_message="ordinary task"),
    )
    _maybe_trigger_self_reflect_after_write(ctx, str(artifact), "verified report")
    inbox = state / "desktop_inbox.jsonl"
    assert inbox.is_file() and "[自动反思触发]" in inbox.read_text(encoding="utf-8")


def test_campaign_retry_has_distinct_executor_dedup_key():
    base = "[PARTNER_CAMPAIGN campaign_id=c work_item_id=w]"
    first = f"{base} [campaign_attempt=1] [transport_recovery=0] work"
    retry = f"{base} [campaign_attempt=2] [transport_recovery=0] work"
    recovery = f"{base} [campaign_attempt=2] [transport_recovery=1] work"
    assert len({_batch_plan_dedup_key(value) for value in (first, retry, recovery)}) == 3
