from partner.harness_core.robust_executor import load_harness_config


def test_semantic_repair_uses_planner_sized_timeout(tmp_path):
    config = load_harness_config(str(tmp_path))
    repair = config["external_calls"]["per_event"]["batch_planner_semantic_repair"]
    assert repair == {"timeout": 180, "retries": 0}
