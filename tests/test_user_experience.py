from partner.governance.user_experience import (
    execution_receipt_message, file_delivery_confirmed, finish_message, start_message,
    instruction_received_message, validate_progress_receipts, verification_receipt_message,
    visibility_mode,
)
from partner.v2.domain_reports import render_continuous_report
from partner.v2.pdf_events import _content_quality


def test_file_delivery_acknowledgement_shapes_are_normalized():
    assert file_delivery_confirmed({"delivered": True}) is True
    assert file_delivery_confirmed({"ok": True, "pushed": 1, "total": 1}) is True
    assert file_delivery_confirmed({"ok": True, "pushed": 1, "total": 2}) is False
    assert file_delivery_confirmed({"ok": False, "pushed": 0, "total": 1}) is False


def test_standard_campaign_messages_explain_plan_execution_and_next_step():
    instruction = "[strategy_id=02_error_slices] direct"
    started = start_message(instance_id="02", title="官方拆分误差切片",
                            event_type="continuous_project_step", instruction=instruction)
    assert "本轮关键步骤" in started
    assert "1." in started and "2." in started and "3." in started
    received = instruction_received_message(
        instance_id="02", title="官方拆分误差切片", event_type="continuous_project_step",
        instruction="外层\n任务：运行官方拆分误差切片并保存证据\n\n强制要求：执行",
    )
    assert "收到本轮任务" in received and "运行官方拆分误差切片并保存证据" in received
    assert "强制要求" not in received and "[strategy_id=" not in received
    result = {"ok": True, "summary": "切片完成", "files": ["result.json", "report.pdf"],
              "result": {"train_rows": 100, "test_rows": 20, "train_test_group_overlap": 0}}
    executed = execution_receipt_message(instance_id="02", event_type="continuous_project_step",
                                         result=result, instruction=instruction)
    assert "train_rows=100" in executed and "report.pdf" in executed
    assert "关键操作完成" in executed and "实际操作" in executed
    verified = verification_receipt_message(
        instance_id="02", result=result,
        file_delivery={"ok": True, "pushed": 1, "total": 1}, instruction=instruction,
    )
    assert "结果核验完成" in verified and "PDF 真实送达：已确认" in verified
    finished = finish_message(instance_id="02", title="官方拆分误差切片",
                              event_type="continuous_project_step", result=result,
                              instruction=instruction, report_delivered=True)
    assert "报告送达：已确认" in finished
    assert "没有真实入队回执时不会声称已开始" in finished


def test_scout_is_compact_but_still_visible():
    instruction = "[portfolio_scout=true] audit"
    assert visibility_mode(instruction) == "compact"
    message = start_message(instance_id="04", title="外部来源 Scout",
                            event_type="external_learning_index_slice", instruction=instruction)
    assert "开始低频证据检查" in message
    assert "无变化会明确报告" in message


def test_progress_receipts_are_a_delivery_hard_gate():
    receipts = [
        {"phase": "started", "delivered": True},
        {"phase": "executed", "delivered": True},
        {"phase": "finished", "delivered": True},
    ]
    assert validate_progress_receipts(receipts, "")['ok'] is True
    receipts[-1]["delivered"] = False
    result = validate_progress_receipts(receipts, "")
    assert result["ok"] is False and result["missing"] == ["finished"]


def test_v2_progress_requires_instruction_and_verified_step():
    receipts = [
        {"phase": phase, "delivered": True}
        for phase in ("instruction_received", "started", "executed", "verified", "finished")
    ]
    assert validate_progress_receipts(receipts, "[user_progress_v2=true]")["ok"] is True
    receipts = [row for row in receipts if row["phase"] != "instruction_received"]
    result = validate_progress_receipts(receipts, "[user_progress_v2=true]")
    assert result["ok"] is False and result["missing"] == ["instruction_received"]


def test_domain_reports_have_different_information_architectures():
    content = render_continuous_report("01", "01_claim_evidence_matrix", {
        "ok": True,
        "claim_evidence_matrix": [{"record_id": "r1", "source_urls": ["https://example.org"],
                                    "claim_status": "待核验", "publish_authorized": False}],
        "business_metrics": {"records_mapped": 1, "records_with_source_evidence": 1,
                             "publish_authorized": 0},
    })
    framework = render_continuous_report("03", "03_runtime_recovery_canary", {
        "ok": True, "command": ["pytest"], "exit_code": 0, "test_output": "5 passed",
        "business_metrics": {"tests_passed": True, "durable_evidence_bundles": 3},
    })
    assert "来源与主张矩阵" in content and "合同覆盖范围" not in content
    assert "合同覆盖范围" in framework and "来源与主张矩阵" not in framework
    assert "目标与承接" not in content + framework
    assert _content_quality(content, [])["plain_chars"] >= 700
    assert _content_quality(framework, [])["plain_chars"] >= 600
