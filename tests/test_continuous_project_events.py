from pathlib import Path
from types import SimpleNamespace

from partner.v2.continuous_project_events import atomic_continuous_project_step


def test_framework_canary_produces_detailed_pdf_and_machine_result(tmp_path):
    workspace = tmp_path / "workspace/instances/03"
    working = workspace / "state/tasks/task"
    ctx = SimpleNamespace(
        workspace=str(workspace), working_dir=str(working),
        task_instance=SimpleNamespace(
            working_dir=str(working), workspace=str(workspace),
            user_message="[PARTNER_CAMPAIGN campaign_id=c work_item_id=w] test",
        ),
    )
    result = atomic_continuous_project_step(ctx, {"strategy_id": "03_evidence_graph_canary"})
    assert result["ok"] is True
    paths = [Path(path) for path in result["files"]]
    assert all(path.is_file() for path in paths)
    assert next(path for path in paths if path.suffix == ".pdf").stat().st_size > 1024
    assert result["result"]["business_metrics"]["tests_passed"] is True


def test_content_claim_matrix_uses_real_inbox_evidence_without_authorizing_publish(tmp_path):
    root = tmp_path / "workspace"
    workspace = root / "instances/01"
    working = workspace / "state/tasks/task"
    inbox = root / "external/content/inbox.jsonl"
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        '{"id":"source-1","urls":["https://example.org/a"],'
        '"acquisition":{"text_preview":"bounded source text"}}\n',
        encoding="utf-8",
    )
    ctx = SimpleNamespace(
        workspace=str(workspace), working_dir=str(working),
        task_instance=SimpleNamespace(
            working_dir=str(working), workspace=str(workspace),
            user_message="[PARTNER_CAMPAIGN campaign_id=c work_item_id=w] test",
        ),
    )
    result = atomic_continuous_project_step(ctx, {"strategy_id": "01_claim_evidence_matrix"})
    assert result["ok"] is True
    machine = result["result"]
    assert machine["business_metrics"] == {
        "records_mapped": 1, "records_with_source_evidence": 1, "publish_authorized": 0,
    }
    assert machine["claim_evidence_matrix"][0]["source_text_sha256"]
    assert machine["claim_evidence_matrix"][0]["publish_authorized"] is False


def test_new_content_risk_queue_and_editorial_backlog_are_distinct(tmp_path):
    root = tmp_path / "workspace"
    workspace = root / "instances/01"
    inbox = root / "external/content/inbox.jsonl"
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        '{"id":"with-source","urls":["https://example.org/a"],"visible_body":"evidence"}\n'
        '{"id":"missing-source","risk":"high"}\n', encoding="utf-8",
    )
    def run(strategy):
        working = workspace / f"state/tasks/{strategy}"
        ctx = SimpleNamespace(workspace=str(workspace), working_dir=str(working),
                              task_instance=SimpleNamespace(working_dir=str(working), workspace=str(workspace),
                                                            user_message="campaign"))
        return atomic_continuous_project_step(ctx, {"strategy_id": strategy})["result"]
    risk = run("01_claim_risk_queue")
    backlog = run("01_editorial_backlog")
    assert risk["business_metrics"]["records_ranked"] == 2
    assert risk["claim_risk_queue"][0]["record_id"] == "missing-source"
    assert backlog["business_metrics"]["ready_for_human_claim_review"] == 1
    assert all(row["publish_authorized"] is False for row in backlog["editorial_backlog"])


def test_molecular_risk_register_consumes_persisted_machine_evidence(tmp_path):
    root = tmp_path / "workspace"
    workspace = root / "instances/02"
    evidence = root / "share/evidence/molecular_generation/campaign/work/result.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"conclusion":"inconclusive confidence interval","metrics":{"test_rows":27}}',
                        encoding="utf-8")
    working = workspace / "state/tasks/risk"
    ctx = SimpleNamespace(workspace=str(workspace), working_dir=str(working),
                          task_instance=SimpleNamespace(working_dir=str(working), workspace=str(workspace),
                                                        user_message="campaign"))
    result = atomic_continuous_project_step(ctx, {"strategy_id": "02_model_risk_register"})
    assert result["ok"] is True
    assert result["result"]["business_metrics"]["evidence_files_reviewed"] == 1
    assert result["result"]["business_metrics"]["production_promotion"] == 0
