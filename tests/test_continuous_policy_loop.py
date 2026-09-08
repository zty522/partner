from datetime import datetime, timedelta
from pathlib import Path

from partner.governance.campaign import create_campaign
from partner.governance.campaign_models import CampaignBudget
from partner.governance.campaign_storage import list_work_items, save_work_item
from partner.governance.continuous_policy_loop import (
    BASELINE_STRATEGY,
    CANDIDATE_STRATEGY,
    TOPICS,
    build_instruction,
    ensure_real_date_window,
    enable_local_observation_fallback,
    experiment_id,
    seed_window,
)
from partner.governance.campaign_storage import load_campaign, save_campaign


def _campaign(tmp_path):
    return create_campaign(
        str(tmp_path), goal="event-driven matched experiment",
        allowed_instances=["04", "05"], duration_seconds=3600, max_active=2,
        budget=CampaignBudget(max_work_items=20, max_failures=19,
                              max_retries_per_item=0, max_runtime_seconds=3600,
                              max_model_calls=40, max_cost_units=40),
    )


def test_seed_window_creates_fifo_matched_work_for_both_slots(tmp_path):
    state = _campaign(tmp_path)
    created = seed_window(str(tmp_path), state.campaign_id,
                          date_window="2026-09-01", pairs_per_project=2)
    assert len(created) == 8
    items = list_work_items(str(tmp_path), state.campaign_id)
    assert {row.instance_id for row in items} == {"04", "05"}
    assert all(row.max_attempts == 1 and not row.requires_delivery for row in items)
    for iid in ("04", "05"):
        scheduled = sorted((row for row in items if row.instance_id == iid),
                           key=lambda row: (-row.priority, row.created_at, row.work_item_id))
        arms = ["candidate" if "[policy_arm=candidate]" in row.instruction else "baseline"
                for row in scheduled]
        assert arms == ["baseline", "candidate", "baseline", "candidate"]


def test_instructions_are_auditable_diverse_and_include_one_stress_source():
    normal = build_instruction(instance_id="04", topic_index=0,
                               policy_arm="baseline", date_window="2026-09-01")
    candidate = build_instruction(instance_id="04", topic_index=0,
                                  policy_arm="candidate", date_window="2026-09-01")
    stress = build_instruction(instance_id="05", topic_index=len(TOPICS) - 1,
                               policy_arm="candidate", date_window="2026-09-01")
    assert f"[experiment_id={experiment_id()}]" in normal
    assert f"[strategy_id={BASELINE_STRATEGY}]" in normal
    assert f"[strategy_id={CANDIDATE_STRATEGY}]" in candidate
    assert "Candidate ID=" in candidate
    assert "context_compressor.py" in normal
    assert "compact.rs" in stress and "context_compressor.py" not in stress
    assert "每条实质 Claim 只能绑定一个来源绝对路径" in candidate


def test_local_observation_migration_only_changes_unstarted_campaign_items(tmp_path):
    state = _campaign(tmp_path)
    seed_window(str(tmp_path), state.campaign_id,
                date_window="2026-09-01", pairs_per_project=1)
    items = list_work_items(str(tmp_path), state.campaign_id)
    for item in items:
        item.requires_delivery = True
        save_work_item(str(tmp_path), item)
    items[0].status = "blocked"
    items[0].blocked_reason = "historical delivery failure"
    save_work_item(str(tmp_path), items[0])
    changed = enable_local_observation_fallback(str(tmp_path), state.campaign_id)
    after = list_work_items(str(tmp_path), state.campaign_id)
    assert changed == 3
    historical = next(row for row in after if row.work_item_id == items[0].work_item_id)
    assert historical.requires_delivery is True
    assert all(not row.requires_delivery for row in after if row.status == "proposed")


def test_real_date_window_seeds_once_and_preserves_creation_date(tmp_path, monkeypatch):
    state = _campaign(tmp_path)
    for name in ("HERMES_SOURCE", "JITRL_SOURCE", "CODEX_SOURCE"):
        source = tmp_path / f"{name}.txt"
        source.write_text("evidence", encoding="utf-8")
        monkeypatch.setattr(
            "partner.governance.continuous_policy_loop." + name, str(source),
        )
    first = ensure_real_date_window(
        str(tmp_path), state.campaign_id, candidate_id="candidate_x",
        pairs_per_project=1,
    )
    assert first["status"] == "date_window_already_seeded"
    ledger = Path(first["path"])
    ledger.unlink(missing_ok=True)
    state = load_campaign(str(tmp_path), state.campaign_id)
    state.created_at = "2026-08-31T20:00:00+08:00"
    save_campaign(str(tmp_path), state)
    seeded = ensure_real_date_window(
        str(tmp_path), state.campaign_id, candidate_id="candidate_x",
        pairs_per_project=1,
    )
    assert seeded["status"] == "real_date_window_seeded"
    assert seeded["work_items"] == 4
    again = ensure_real_date_window(
        str(tmp_path), state.campaign_id, candidate_id="candidate_x",
        pairs_per_project=1,
    )
    assert again["status"] == "date_window_already_seeded"
    assert len(list_work_items(str(tmp_path), state.campaign_id)) == 4
