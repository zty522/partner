import json
import time

from partner.application.models import JobRecord
from partner.event_fabric.catalog import build_catalog
from partner.event_fabric.flows import EventFlowController, EventFlowStore
from partner.event_flows.registry import build_flow_registry
from partner.index.job_repository import init
from partner.operations.job_reconcile import reconcile_stale_jobs


def test_reconcile_is_dry_run_then_append_only_cancel(tmp_path):
    repo = init(tmp_path)
    flow = EventFlowController(EventFlowStore(tmp_path)).start(
        build_flow_registry().get("benchmark_subject"),
        catalog_version=build_catalog(workspace=tmp_path).version,
        task_id="j1", project_id="p", instance_id="01")
    job = JobRecord(job_id="j1", project_id="p", status="queued", flow_id=flow.flow_id,
                    flow_type=flow.flow_type, assigned_instance="01")
    repo.upsert_from_record(job.to_dict())
    conn = __import__('partner.index.sqlite_base', fromlist=['get_connection']).get_connection(repo.db_path)
    conn.execute("UPDATE jobs SET updated_at=? WHERE job_id='j1'", (time.time()-3600,)); conn.commit()
    dry = reconcile_stale_jobs(tmp_path, older_than_seconds=60, apply=False)
    assert dry["candidate_count"] == 1 and repo.get("j1")["status"] == "queued"
    applied = reconcile_stale_jobs(tmp_path, older_than_seconds=60, apply=True)
    assert applied["candidate_count"] == 1 and repo.get("j1")["status"] == "cancelled"
    assert EventFlowStore(tmp_path).load(flow.flow_id).status == "cancelled"
    assert any(row["kind"] == "stale_projection_reconciled" for row in repo.history("j1"))
