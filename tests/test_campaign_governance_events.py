from partner.v2.campaign_governance_events import _is_evidence_backed_low_reward


def test_healthy_lowest_policy_action_does_not_create_false_issue():
    assert _is_evidence_backed_low_reward({
        "mean_reward": 0.85, "success_rate": 1.0, "samples": 1,
    }) is False


def test_negative_reward_or_low_success_is_issue_evidence():
    assert _is_evidence_backed_low_reward({
        "mean_reward": 0.10, "success_rate": 1.0, "samples": 1,
    }) is True
    assert _is_evidence_backed_low_reward({
        "mean_reward": 0.80, "success_rate": 0.5, "samples": 4,
    }) is True
