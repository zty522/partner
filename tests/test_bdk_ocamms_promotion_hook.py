from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# BDK + Partner path injection
sys.path.insert(0, "/mnt/e/work/partner")

from partner.governance.evolution_loop import decide_experiment, start_experiment, record_issue  # noqa: E402
from partner.governance.candidate_skills import register_candidate_skill  # noqa: E402


def _workspace(tmp_path) -> str:
    ws = tmp_path / "ws"
    ws.mkdir()
    return str(ws)


def _register_bdk_skill(workspace: str, *, candidate_id: str, kernel_probs: list[float]):
    """Register a candidate skill with explicit BDK intervention."""
    intervention = {
        "bdk_module": "bdk.function_pool.FunctionPool",
        "kernel_names": ["linear", "quadratic", "fourier", "expdecay"],
        "kernel_probs": kernel_probs,
        "kernel_logits": [0.6, 0.5, -0.5, -1.0],
    }
    register_candidate_skill(workspace, {
        "candidate_id": candidate_id,
        "title": "test bdk skill",
        "status": "candidate",
        "experiment_id": "exp_test",
        "strategy_id": candidate_id,
        "source_episode_ids": ["test:test"],
        "success_criteria": ["test"],
        "applicability": ["test"],
        "intervention": json.dumps(intervention, ensure_ascii=False),
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "targetdiff_bdk_function_pool",
                               "allowed_instances": ["02"], "default_params": {}},
    })


def _make_promote_params(experiment_id: str = "exp_test") -> dict:
    return {
        "experiment_id": experiment_id,
        "decision": "promoted",
        "evidence": ["pytest"],
        "regression_passed": True,
        "criteria_results": {"criterion_a": True, "criterion_b": True},
    }


# ---- Backward compatibility ----------------------------------------------


def test_decide_without_enforce_bdk_ocamms_is_unchanged(tmp_path):
    """Existing callers that don't set enforce_bdk_ocamms must see no behavior change."""
    ws = _workspace(tmp_path)
    result = decide_experiment(ws, _make_promote_params())
    assert result["ok"] is True
    assert result["promoted"] is True
    assert result["status"] == "promoted"
    assert "bdk_ocamms" not in str(result).lower() or "verdict" not in result


def test_decide_with_enforce_but_no_candidate_id_is_blocked(tmp_path):
    """Event-first enforcement requires an identifiable Candidate."""
    ws = _workspace(tmp_path)
    result = decide_experiment(ws, {
        **_make_promote_params(),
        "enforce_bdk_ocamms": True,
        # no candidate_id
    })
    assert result["ok"] is False
    assert result["status"] == "candidate_id_required"


# ---- Guard fires when candidate_id is supplied --------------------------


def test_enforce_blocks_promotion_when_bdk_ocamms_fails(tmp_path):
    """A BDK candidate with too many activated kernels must be blocked."""
    ws = _workspace(tmp_path)
    # 3 kernels activated (uniform distribution) → fails ocamms
    _register_bdk_skill(
        ws, candidate_id="candidate_test_block",
        kernel_probs=[0.34, 0.33, 0.33, 0.0],
    )
    result = decide_experiment(ws, {
        **_make_promote_params(),
        "enforce_bdk_ocamms": True,
        "candidate_id": "candidate_test_block",
    })
    assert result["ok"] is False
    assert result["status"] == "bdk_ocamms_blocked"
    assert "verdict" in result
    assert result["verdict"]["blocked"] is True


def test_enforce_allows_promotion_when_bdk_ocamms_passes(tmp_path):
    """A BDK candidate with 1-2 activated kernels should be allowed."""
    ws = _workspace(tmp_path)
    _register_bdk_skill(
        ws, candidate_id="candidate_test_pass",
        kernel_probs=[0.41, 0.37, 0.14, 0.08],  # 2 activated
    )
    result = decide_experiment(ws, {
        **_make_promote_params(),
        "enforce_bdk_ocamms": True,
        "candidate_id": "candidate_test_pass",
    })
    assert result["ok"] is True
    assert result["promoted"] is True


def test_enforce_records_guard_verdict_in_evidence(tmp_path):
    """When guard passes, the verdict must appear in promotion_decisions evidence."""
    ws = _workspace(tmp_path)
    _register_bdk_skill(
        ws, candidate_id="candidate_test_record",
        kernel_probs=[0.41, 0.37, 0.14, 0.08],
    )
    result = decide_experiment(ws, {
        **_make_promote_params(),
        "enforce_bdk_ocamms": True,
        "candidate_id": "candidate_test_record",
    })
    assert result["ok"] is True
    decision = result["decision"]
    assert "evidence" in decision
    bdk_entries = [e for e in decision["evidence"] if isinstance(e, dict) and e.get("kind") == "bdk_ocamms_guard"]
    assert len(bdk_entries) == 1
    assert bdk_entries[0]["candidate_id"] == "candidate_test_record"
    assert bdk_entries[0]["allowed"] is True
    assert bdk_entries[0]["blocked"] is False


# ---- Non-BDK candidates are not blocked ----------------------------------


def test_enforce_with_non_bdk_candidate_does_not_block(tmp_path):
    """A candidate without BDK intervention should not block promotion."""
    ws = _workspace(tmp_path)
    register_candidate_skill(ws, {
        "candidate_id": "candidate_non_bdk",
        "title": "non-bdk skill",
        "status": "candidate",
        "experiment_id": "exp_test",
        "strategy_id": "candidate_non_bdk",
        "source_episode_ids": ["test:test"],
        "success_criteria": ["test"],
        "applicability": ["test"],
        "intervention": json.dumps({
            "method": "sklearn",
            "module": "sklearn.linear_model.LinearRegression",
        }, ensure_ascii=False),
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "targetdiff_bdk_function_pool",
                               "allowed_instances": ["02"], "default_params": {}},
    })
    result = decide_experiment(ws, {
        **_make_promote_params(),
        "enforce_bdk_ocamms": True,
        "candidate_id": "candidate_non_bdk",
    })
    assert result["ok"] is True
    assert result["promoted"] is True
    # Evidence should record the verdict anyway
    decision = result["decision"]
    bdk_entries = [e for e in decision["evidence"] if isinstance(e, dict) and e.get("kind") == "bdk_ocamms_guard"]
    assert len(bdk_entries) == 1
    assert bdk_entries[0]["not_applicable"] is True


# ---- Rejected/inconclusive decisions skip the guard ----------------------


def test_enforce_skipped_for_non_promoted_decisions(tmp_path):
    """enforce_bdk_ocamms=True with decision='rejected' must not invoke guard."""
    ws = _workspace(tmp_path)
    # No candidate registered. If guard were called, it would block on unknown id.
    # But decision is 'rejected', so guard is skipped.
    result = decide_experiment(ws, {
        "experiment_id": "exp_test_rej",
        "decision": "rejected",
        "evidence": ["pytest"],
        "regression_passed": False,
        "criteria_results": {},
        "enforce_bdk_ocamms": True,
        "candidate_id": "candidate_does_not_exist",
    })
    assert result["ok"] is True
    assert result["status"] == "rejected"
    assert result["promoted"] is False
    # No bdk entry in evidence (guard was not invoked)
    decision = result["decision"]
    bdk_entries = [e for e in decision["evidence"] if isinstance(e, dict) and e.get("kind") == "bdk_ocamms_guard"]
    assert len(bdk_entries) == 0


def test_enforce_skipped_for_inconclusive_decision(tmp_path):
    ws = _workspace(tmp_path)
    result = decide_experiment(ws, {
        "experiment_id": "exp_test_inc",
        "decision": "inconclusive",
        "evidence": ["canary_metrics.json"],
        "regression_passed": False,
        "criteria_results": {},
        "enforce_bdk_ocamms": True,
        "candidate_id": "candidate_anything",
    })
    assert result["ok"] is True
    assert result["promoted"] is False
    decision = result["decision"]
    bdk_entries = [e for e in decision["evidence"] if isinstance(e, dict) and e.get("kind") == "bdk_ocamms_guard"]
    assert len(bdk_entries) == 0


# ---- Guard error path -----------------------------------------------------


def test_enforce_with_unknown_candidate_is_blocked_before_guard(tmp_path):
    """Event-first lifecycle rejects unknown Candidate identity."""
    ws = _workspace(tmp_path)
    result = decide_experiment(ws, {
        **_make_promote_params(),
        "enforce_bdk_ocamms": True,
        "candidate_id": "candidate_does_not_exist",
    })
    assert result["ok"] is False
    assert result["status"] == "candidate_not_found"
    assert "not found" in result.get("error", "").lower()
