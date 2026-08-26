import json
from pathlib import Path

from partner.governance.evidence_archive import (
    archive_work_item_evidence,
    semantic_outcome_fingerprint,
)


def test_archive_survives_task_directory_removal_and_keeps_provenance(tmp_path):
    root = tmp_path / "workspace"
    task = root / "instances/02/state/tasks/task-a"
    task.mkdir(parents=True)
    result = task / "result.json"
    result.write_text('{"metric": 0.4, "created_at": "volatile"}', encoding="utf-8")
    report = task / "report.md"
    report.write_text("evidence", encoding="utf-8")

    archived = archive_work_item_evidence(
        str(root), campaign_id="campaign-a", work_item_id="work-a",
        project_id="molecular_generation", instance_id="02",
        artifacts=[str(result), str(report)], event_types=["experiment"],
    )
    result.unlink(); report.unlink(); task.rmdir()

    assert archived["ok"] is True
    assert all(Path(path).is_file() for path in archived["artifacts"])
    manifest = json.loads(Path(archived["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["artifacts"][0]["source_path"].endswith("result.json")
    assert manifest["semantic_outcome_fingerprint"]


def test_semantic_fingerprint_ignores_paths_and_timestamps(tmp_path):
    one = tmp_path / "one.json"; two = tmp_path / "two.json"
    one.write_text('{"score":1.25,"path":"/tmp/a","created_at":"a"}', encoding="utf-8")
    two.write_text('{"score":1.25,"path":"/tmp/b","created_at":"b"}', encoding="utf-8")
    assert semantic_outcome_fingerprint([str(one)], ["experiment"]) == semantic_outcome_fingerprint(
        [str(two)], ["experiment"]
    )
