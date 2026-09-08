from __future__ import annotations

from partner.governance.campaign_models import WorkItem
from partner.governance.long_horizon_loop import assess_long_horizon


def _item(index: int, *, instance: str = "03", kind: str = "project_iteration",
          status: str = "completed", instruction: str = "work",
          progress: bool = True) -> WorkItem:
    return WorkItem(
        campaign_id="campaign_long", instance_id=instance,
        project_id="partner_framework_frontend", kind=kind,
        title=f"item {index}", instruction=instruction,
        work_item_id=f"work_{index}", status=status,
        task_id=f"task_{index}" if status in {"queued", "running", "completed"} else "",
        evidence=[f"business_progress={str(progress).lower()}"],
        updated_at=f"2026-08-29T00:00:{index:02d}+08:00",
    )


def test_project_continuation_always_preempts_learning():
    items = [_item(1), _item(2)]
    result = assess_long_horizon(
        items, has_project_continuation=True, has_executable_candidate=True,
    )
    assert result.phase == "ADVANCE_PROJECT"
    assert result.learning_allowed is True


def test_two_business_outcomes_allow_one_candidate_validation():
    result = assess_long_horizon(
        [_item(1), _item(2)],
        has_project_continuation=False, has_executable_candidate=True,
    )
    assert result.phase == "VALIDATE_CANDIDATE"
    assert result.candidate_validation_allowed is True


def test_learning_checkpoint_resets_business_quota():
    items = [
        _item(1), _item(2),
        _item(3, instance="05", kind="project_iteration", progress=False),
        _item(4),
    ]
    result = assess_long_horizon(
        items, has_project_continuation=False, has_executable_candidate=True,
    )
    assert result.business_since_learning == 1
    assert result.phase == "VALIDATE_CANDIDATE"
    assert result.candidate_validation_allowed is True


def test_only_one_candidate_experiment_is_allowed_after_learning():
    items = [
        _item(1), _item(2),
        _item(3, instance="05", kind="project_iteration", progress=False),
        _item(4, instance="02", kind="evolution_experiment", progress=False),
    ]
    result = assess_long_horizon(
        items, has_project_continuation=False, has_executable_candidate=True,
    )
    assert result.governance_since_business == 2
    assert result.phase == "WAIT_EVIDENCE"
    assert result.candidate_validation_allowed is False


def test_same_second_random_ids_cannot_reorder_business_learning_candidate():
    business = _item(1)
    learning = _item(2, instance="05", kind="project_iteration", progress=False)
    candidate = _item(3, instance="03", kind="evolution_experiment", progress=False)
    for item in (business, learning, candidate):
        item.updated_at = "2026-08-29T00:00:00+08:00"
        item.created_at = item.updated_at
    business.work_item_id = "work_z"
    learning.work_item_id = "work_y"
    candidate.work_item_id = "work_a"
    result = assess_long_horizon(
        [candidate, business, learning],
        has_project_continuation=False, has_executable_candidate=False,
    )
    assert result.phase == "WAIT_EVIDENCE"
    assert result.business_since_learning == 0


def test_no_change_scouts_cannot_create_learning_work():
    scouts = [
        _item(index, kind="audit", instruction="[portfolio_scout=true] no change", progress=False)
        for index in range(1, 4)
    ]
    result = assess_long_horizon(
        scouts, has_project_continuation=False, has_executable_candidate=True,
    )
    assert result.phase == "WAIT_EVIDENCE"
    assert result.learning_allowed is False
    assert result.no_change_since_learning == 3
