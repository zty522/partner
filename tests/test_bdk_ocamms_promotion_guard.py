from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PARTNER_ROOT = Path("/mnt/e/work/partner")
if str(_PARTNER_ROOT) not in sys.path:
    sys.path.insert(0, str(_PARTNER_ROOT))

from partner.learn.bdk_ocamms_promotion_guard import (  # noqa: E402
    _extract_kernel_logits,
    bdk_ocamms_promotion_guard,
)
from partner.governance.candidate_skills import register_candidate_skill  # noqa: E402


def _register_test_skill(workspace: str, *, intervention_obj, candidate_id: str = "candidate_test"):
    """Helper: register a candidate with a custom intervention JSON."""
    intervention_str = json.dumps(intervention_obj, ensure_ascii=False)
    register_candidate_skill(workspace, {
        "candidate_id": candidate_id,
        "title": "test",
        "status": "candidate",
        "experiment_id": "exp_test",
        "strategy_id": candidate_id,
        "source_episode_ids": ["test:test"],
        "success_criteria": ["test"],
        "applicability": ["test"],
        "intervention": intervention_str,
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": "targetdiff_bdk_function_pool",
                               "allowed_instances": ["02"], "default_params": {}},
    })


def test_guard_accepts_simple_pass(tmp_path):
    """A 2-kernel distribution should pass."""
    ws = str(tmp_path)
    intervention = {
        "bdk_module": "bdk.function_pool.FunctionPool",
        "kernel_names": ["linear", "quadratic", "fourier", "expdecay"],
        "kernel_probs": [0.41, 0.37, 0.14, 0.08],
        "kernel_logits": [0.6, 0.5, -0.5, -1.0],
    }
    _register_test_skill(ws, intervention_obj=intervention)
    v = bdk_ocamms_promotion_guard(ws, "candidate_test")
    assert v["not_applicable"] is False
    assert v["allowed"] is True
    assert v["blocked"] is False
    assert v["audit"]["n_activated"] == 2
    assert v["audit"]["activated_names"] == ["linear", "quadratic"]


def test_guard_rejects_three_kernels(tmp_path):
    """A 3-kernel distribution should fail (ocamms blocks promotion)."""
    ws = str(tmp_path)
    intervention = {
        "bdk_module": "bdk.function_pool.FunctionPool",
        "kernel_names": ["linear", "quadratic", "fourier", "expdecay"],
        "kernel_probs": [0.34, 0.33, 0.33, 0.0],
        "kernel_logits": [0.5, 0.5, 0.5, -10.0],
    }
    _register_test_skill(ws, intervention_obj=intervention)
    v = bdk_ocamms_promotion_guard(ws, "candidate_test")
    assert v["allowed"] is False
    assert v["blocked"] is True
    assert "failed" in v["reason"]
    assert v["audit"]["n_activated"] >= 3


def test_guard_handles_logit_input_via_softmax(tmp_path):
    """When only kernel_logits is present, ocamms should softmax internally."""
    ws = str(tmp_path)
    intervention = {
        "bdk_module": "bdk.function_pool.FunctionPool",
        "kernel_names": ["linear", "quadratic", "fourier", "expdecay"],
        # Only logits — ocamms should softmax internally and pass.
        "kernel_logits": [0.6, 0.5, -0.5, -1.0],
        # Deliberately no kernel_probs
    }
    _register_test_skill(ws, intervention_obj=intervention)
    v = bdk_ocamms_promotion_guard(ws, "candidate_test")
    # The ocamms function will softmax the logits and check vs margin=0.20
    # Logits [0.6, 0.5, -0.5, -1.0] softmax to [0.41, 0.37, 0.14, 0.08]
    # n_activated = 2 (linear+quadratic > 0.20)
    assert v["allowed"] is True


def test_guard_rejects_non_bdk_intervention(tmp_path):
    """A non-BDK intervention should be not_applicable, not blocked."""
    ws = str(tmp_path)
    intervention = {
        "bdk_module": "sklearn.linear_model.LinearRegression",
        "method": "fit_intercept",
    }
    _register_test_skill(ws, intervention_obj=intervention)
    v = bdk_ocamms_promotion_guard(ws, "candidate_test")
    assert v["not_applicable"] is True
    assert v["allowed"] is True  # not blocking non-BDK
    assert v["blocked"] is False


def test_guard_rejects_missing_intervention(tmp_path):
    """A candidate with no intervention at all should be not_applicable."""
    ws = str(tmp_path)
    register_candidate_skill(ws, {
        "candidate_id": "candidate_test",
        "title": "test",
        "status": "candidate",
        "experiment_id": "exp_test",
        "strategy_id": "candidate_test",
        "source_episode_ids": ["test:test"],
        "success_criteria": ["test"],
        "applicability": ["test"],
        "intervention": "",
    })
    v = bdk_ocamms_promotion_guard(ws, "candidate_test")
    assert v["not_applicable"] is True
    assert v["allowed"] is True
    assert "no intervention" in v["reason"]


def test_guard_handles_unknown_candidate_id(tmp_path):
    """An unknown candidate_id should block promotion (cannot allow unknown)."""
    ws = str(tmp_path)
    v = bdk_ocamms_promotion_guard(ws, "candidate_does_not_exist")
    assert v["not_applicable"] is True
    assert v["allowed"] is False
    assert v["blocked"] is True
    assert "not found" in v["reason"]


def test_guard_includes_audit_with_kernel_probs(tmp_path):
    """Audit dict must include kernel_probs / names / activated_names."""
    ws = str(tmp_path)
    intervention = {
        "bdk_module": "bdk.function_pool.FunctionPool",
        "kernel_names": ["a", "b", "c", "d"],
        "kernel_probs": [0.5, 0.3, 0.15, 0.05],
        "kernel_logits": [0.5, 0.3, -0.5, -1.0],
    }
    _register_test_skill(ws, intervention_obj=intervention)
    v = bdk_ocamms_promotion_guard(ws, "candidate_test")
    audit = v["audit"]
    assert audit is not None
    assert "kernel_probs" in audit
    assert "kernel_names" in audit
    assert "activated_names" in audit
    assert audit["kernel_names"] == ["a", "b", "c", "d"]


def test_guard_prefers_probs_when_both_present_even_if_invalid(tmp_path):
    """When kernel_probs is non-empty, _extract_kernel_logits uses probs
    even if they're not actually normalised (this is a known quirk —
    ocamms itself decides whether to softmax based on its heuristics).
    """
    ws = str(tmp_path)
    intervention = {
        "bdk_module": "bdk.function_pool.FunctionPool",
        "kernel_names": ["linear", "quadratic", "fourier", "expdecay"],
        # sum=4, but the guard's _extract prefers non-empty probs over logits
        "kernel_probs": [1.0, 1.0, 1.0, 1.0],
        "kernel_logits": [0.6, 0.5, -0.5, -1.0],
    }
    _register_test_skill(ws, intervention_obj=intervention)
    v = bdk_ocamms_promotion_guard(ws, "candidate_test")
    # We document the actual behaviour: ocamms sees sum=4 → softmaxes → uniform → 4 activated → FAIL
    # This is fine; the candidate has malformed probs.
    assert v["blocked"] is True
    assert v["audit"]["n_activated"] >= 3


def test_extract_kernel_logits_prefers_probs_over_logits():
    """When both kernel_probs and kernel_logits are present, prefer probs."""
    candidate = {
        "intervention": json.dumps({
            "bdk_module": "bdk.function_pool.FunctionPool",
            "kernel_names": ["a", "b", "c", "d"],
            "kernel_probs": [0.5, 0.3, 0.15, 0.05],
            "kernel_logits": [-99, -99, -99, -99],  # would fail
        })
    }
    logits, reason = _extract_kernel_logits(candidate)
    assert logits == [0.5, 0.3, 0.15, 0.05]
    assert "probs" in reason


def test_extract_kernel_logits_handles_dict_intervention():
    """intervention may be a dict (not just a JSON string)."""
    candidate = {
        "intervention": {
            "bdk_module": "bdk.function_pool.FunctionPool",
            "kernel_names": ["a", "b"],
            "kernel_probs": [0.7, 0.3],
        }
    }
    logits, reason = _extract_kernel_logits(candidate)
    assert logits == [0.7, 0.3]


def test_extract_kernel_logits_returns_none_for_invalid_json():
    candidate = {"intervention": "this is not JSON {"}
    logits, reason = _extract_kernel_logits(candidate)
    assert logits is None
    assert "JSON" in reason
