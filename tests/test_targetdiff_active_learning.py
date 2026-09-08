import numpy as np
import json

from partner.learn.targetdiff_active_learning import (
    acquisition_scores,
    calibrated_slice_uncertainty,
    diagnose_targetdiff_uncertainty,
    evaluate_targetdiff_uncertainty_candidate,
    evaluate_targetdiff_robustness,
)


def test_acquisition_combines_uncertainty_diversity_and_slice_without_labels():
    scores = acquisition_scores(
        np.array([1.0, 0.2, 0.2]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    )
    assert scores.tolist() == [0.55, 0.3, 0.15]
    assert int(np.argmax(scores)) == 0


def test_acquisition_is_stable_for_constant_inputs():
    scores = acquisition_scores(np.ones(3), np.ones(3), np.ones(3))
    assert np.allclose(scores, np.zeros(3))


def test_acquisition_normalizes_configured_weights():
    scores = acquisition_scores(np.array([1.0, 0.0]), np.array([0.0, 1.0]),
                                np.zeros(2), (9, 1, 0))
    assert np.allclose(scores, np.array([.9, .1]))


def test_slice_calibration_uses_only_labelled_residuals_and_features():
    labelled_x = np.column_stack([np.zeros(8), np.arange(8, dtype=float)])
    labelled_y = np.array([0., 0., 0., 0., 4., 4., 4., 4.])
    oob = np.zeros(8)
    pool_x = np.column_stack([np.zeros(4), np.array([.5, 2.5, 4.5, 6.5])])
    calibrated, audit = calibrated_slice_uncertainty(
        labelled_x, labelled_y, oob, pool_x, np.ones(4), labelled_x,
    )
    assert calibrated[-1] > calibrated[0]
    assert audit["hidden_pool_labels_used"] is False
    assert len(audit["factors"]) == 4


def test_three_seed_robustness_rejects_one_of_three_win(tmp_path):
    directory = tmp_path / "share/mind/governance/active_learning/targetdiff"
    directory.mkdir(parents=True)
    values = [
        (1, 2.5, 2.4, 2.3),
        (2, 2.2, 2.3, 2.4),
        (3, 2.4, 2.3, 2.31),
    ]
    for seed, random, maxvar, active in values:
        payload = {
            "status": "completed", "official_split": True, "seed": seed,
            "arms": {arm: {"history": [{"test_rmse": score}]}
                     for arm, score in (("random", random), ("maxvar", maxvar),
                                        ("active", active))},
        }
        (directory / f"run_{seed}.json").write_text(json.dumps(payload), encoding="utf-8")
    result = evaluate_targetdiff_robustness(
        str(tmp_path), run_ids=["run_1", "run_2", "run_3"], evaluation_id="robust_test",
    )
    assert result["ok"] is True
    assert result["metrics"]["candidate_wins"] == 1
    assert result["decision"] == "rejected_not_robust"
    assert result["gates"]["candidate_wins_two_of_three"] is False
    assert result["event"]["event_type"] == "active_learning/robustness_evaluated"
    assert result["production_effective"] is False


def test_uncertainty_diagnostic_is_posthoc_only_and_three_seed(tmp_path, monkeypatch):
    import partner.learn.targetdiff_active_learning as module

    repo = tmp_path / "external/targetdiff/data"
    repo.mkdir(parents=True)
    (repo / "affinity_info.pkl").write_bytes(b"fixture")
    (repo / "split_by_name.pt").write_bytes(b"fixture")
    rng = np.random.default_rng(7)
    train_x = rng.normal(size=(600, 2))
    train_y = train_x[:, 0] * 2 + train_x[:, 1] ** 2 + rng.normal(scale=.2, size=600)
    test_x = rng.normal(size=(80, 2))
    test_y = test_x[:, 0] * 2 + test_x[:, 1] ** 2 + rng.normal(scale=.2, size=80)
    monkeypatch.setattr(module, "_load_real_rows", lambda _repo: (
        train_x, train_y, test_x, test_y,
        [f"train-{i}" for i in range(600)], [f"test-{i}" for i in range(80)],
    ))
    result = diagnose_targetdiff_uncertainty(
        str(tmp_path), seeds=[11, 12, 13], labelled_budget=120,
        evaluation_id="uncertainty_test",
    )
    assert result["ok"] is True and len(result["runs"]) == 3
    assert result["test_labels_used_for_selection"] is False
    assert result["test_labels_used_for_posthoc_evaluation_only"] is True
    assert result["candidate_tested"] is False
    assert result["production_effective"] is False
    assert result["event"]["event_type"] == "active_learning/uncertainty_diagnostic"


def test_crossfit_uncertainty_candidate_is_matched_and_never_promotes(tmp_path, monkeypatch):
    import partner.learn.targetdiff_active_learning as module

    repo = tmp_path / "external/targetdiff/data"
    repo.mkdir(parents=True)
    (repo / "affinity_info.pkl").write_bytes(b"fixture")
    (repo / "split_by_name.pt").write_bytes(b"fixture")
    rng = np.random.default_rng(17)
    train_x = rng.normal(size=(600, 2))
    train_y = train_x[:, 0] * 2 + train_x[:, 1] ** 2 + rng.normal(scale=.3, size=600)
    test_x = rng.normal(size=(80, 2))
    test_y = test_x[:, 0] * 2 + test_x[:, 1] ** 2 + rng.normal(scale=.3, size=80)
    monkeypatch.setattr(module, "_load_real_rows", lambda _repo: (
        train_x, train_y, test_x, test_y,
        [f"train-{i}" for i in range(600)], [f"test-{i}" for i in range(80)],
    ))
    result = evaluate_targetdiff_uncertainty_candidate(
        str(tmp_path), seeds=[21, 22, 23], labelled_budget=120,
        evaluation_id="crossfit_test",
    )
    assert result["ok"] is True and result["experiment_type"] == "matched_baseline_candidate"
    assert result["test_labels_used_for_training_or_selection"] is False
    assert {"baseline", "candidate"} <= set(result["mean_metrics"])
    assert result["decision"] in {"accept_for_acquisition_shadow", "rejected_candidate_not_better"}
    assert result["promotion"] is False and result["production_effective"] is False
