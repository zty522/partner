"""Deterministic business-progress steps selected by the governed RL control plane."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from partner.governance.storage import workspace_root


def _context(ctx: Any) -> tuple[Path, Path, str]:
    root = workspace_root(str(getattr(ctx, "workspace", "")))
    task = getattr(ctx, "task_instance", None)
    working = Path(str(getattr(task, "working_dir", "") or getattr(ctx, "working_dir", "")))
    working.mkdir(parents=True, exist_ok=True)
    instance = Path(str(getattr(ctx, "workspace", ""))).name
    return root, working, instance if instance in {"01", "02", "03", "04", "05"} else ""


def _latest_json(root: Path, project_id: str) -> dict[str, Any]:
    candidates = sorted((root / "share/evidence" / project_id).glob("*/*/*.json"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.name == "evidence_manifest.json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _content_step(root: Path, strategy: str) -> dict[str, Any]:
    inbox = root / "external/content/inbox.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        rows = [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        pass
    urls = sorted({str(url) for row in rows for url in (row.get("urls") or []) if str(url).startswith("http")})
    if strategy == "01_source_fact_check":
        checks = []
        for url in urls[:5]:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "PartnerEvidence/1.0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    body = response.read(512_000)
                    checks.append({"url": url, "status": int(response.status), "bytes": len(body),
                                   "sha256": hashlib.sha256(body).hexdigest(),
                                   "content_type": str(response.headers.get("content-type") or "")})
            except Exception as exc:
                checks.append({"url": url, "status": 0, "error": type(exc).__name__})
        return {"ok": bool(rows), "strategy_id": strategy, "records": len(rows), "unique_urls": len(urls),
                "checks": checks, "business_metrics": {"sources_checked": len(checks),
                "sources_reachable": sum(row.get("status") == 200 for row in checks)}}
    if strategy == "01_claim_evidence_matrix":
        matrix = []
        for row in rows:
            acquisition = row.get("acquisition") if isinstance(row.get("acquisition"), dict) else {}
            row_urls = row.get("urls") or ([acquisition.get("source_url")] if acquisition.get("source_url") else [])
            preview = str(acquisition.get("text_preview") or row.get("visible_body") or "")
            matrix.append({
                "record_id": str(row.get("id") or ""),
                "source_urls": [str(value) for value in row_urls if str(value).startswith("http")],
                "source_text_sha256": hashlib.sha256(preview.encode("utf-8")).hexdigest() if preview else "",
                "source_text_chars": len(preview),
                "claim_status": "source_text_present_requires_claim_review" if preview else "source_text_missing",
                "publish_authorized": False,
                "risk": str(row.get("risk") or "unreviewed"),
            })
        evidenced = sum(bool(value["source_text_sha256"] and value["source_urls"]) for value in matrix)
        return {"ok": bool(matrix), "strategy_id": strategy, "claim_evidence_matrix": matrix,
                "business_metrics": {"records_mapped": len(matrix), "records_with_source_evidence": evidenced,
                                     "publish_authorized": 0}}
    if strategy == "01_claim_risk_queue":
        queue = []
        for index, row in enumerate(rows):
            acquisition = row.get("acquisition") if isinstance(row.get("acquisition"), dict) else {}
            row_urls = row.get("urls") or ([acquisition.get("source_url")] if acquisition.get("source_url") else [])
            preview = str(acquisition.get("text_preview") or row.get("visible_body") or "")
            risk = str(row.get("risk") or "unreviewed")
            missing_source = not any(str(value).startswith("http") for value in row_urls)
            missing_text = not bool(preview.strip())
            score = 3 * int(missing_source) + 2 * int(missing_text) + int(risk != "low")
            queue.append({"record_id": str(row.get("id") or index + 1), "source_urls": row_urls,
                          "source_text_chars": len(preview), "risk": risk,
                          "priority_score": score, "publish_authorized": False,
                          "claim_status": "needs_evidence" if score else "ready_for_human_claim_review"})
        queue.sort(key=lambda value: (-int(value["priority_score"]), str(value["record_id"])))
        return {"ok": bool(queue), "strategy_id": strategy, "claim_risk_queue": queue,
                "business_metrics": {"records_ranked": len(queue),
                                     "high_priority_gaps": sum(int(row["priority_score"]) >= 3 for row in queue),
                                     "publish_authorized": 0}}
    if strategy == "01_editorial_backlog":
        backlog = []
        for index, row in enumerate(rows):
            acquisition = row.get("acquisition") if isinstance(row.get("acquisition"), dict) else {}
            preview = str(acquisition.get("text_preview") or row.get("visible_body") or "")
            urls_for_row = row.get("urls") or ([acquisition.get("source_url")] if acquisition.get("source_url") else [])
            has_source = bool(preview and any(str(value).startswith("http") for value in urls_for_row))
            backlog.append({"record_id": str(row.get("id") or index + 1),
                            "lane": "human_claim_review" if has_source else "evidence_required",
                            "source_urls": urls_for_row, "publish_authorized": False,
                            "acceptance": "claim-level source citation plus explicit approval"})
        return {"ok": bool(backlog), "strategy_id": strategy, "editorial_backlog": backlog,
                "business_metrics": {"backlog_items": len(backlog),
                                     "ready_for_human_claim_review": sum(row["lane"] == "human_claim_review" for row in backlog),
                                     "evidence_required": sum(row["lane"] == "evidence_required" for row in backlog),
                                     "publish_authorized": 0}}
    previous = _latest_json(root, "xiaohongshu_operations")
    if strategy == "01_candidate_brief":
        checks = previous.get("checks") or []
        return {"ok": bool(rows), "strategy_id": strategy,
                "draft_candidates": [{"source_url": row.get("url"), "verified_http": row.get("status") == 200,
                    "claim_status": "requires_content_fact_check", "publish_authorized": False}
                    for row in checks],
                "business_metrics": {"drafts": len(checks), "publish_authorized": 0}}
    return {"ok": True, "strategy_id": strategy, "ready_for_publication": False,
            "blocked_reason": "content approval and claim-level fact check required",
            "resume_event": "new verified content or explicit publication approval",
            "business_metrics": {"safe_wait": 1}}


def _framework_step(root: Path, strategy: str) -> dict[str, Any]:
    code = Path(__file__).resolve().parents[2]
    if strategy == "03_evidence_graph_canary":
        command = [sys.executable, "-m", "pytest", "tests/test_evidence_archive.py", "tests/test_rl_evolution.py", "-q"]
    elif strategy == "03_runtime_recovery_canary":
        command = [sys.executable, "-m", "pytest", "tests/test_campaign.py", "-q", "-k",
                   "portfolio or restart or recovery or two_slots"]
    elif strategy == "03_user_observability_canary":
        command = [sys.executable, "-m", "pytest", "tests/test_user_experience.py", "tests/test_campaign.py",
                   "-q", "-k", "user_progress or delivery_acknowledgement or domain_reports"]
    elif strategy == "03_soak_density_analysis":
        campaign_dirs = sorted((root / "state/campaigns").glob("campaign_*"),
                               key=lambda path: path.stat().st_mtime, reverse=True)
        selected = campaign_dirs[:3]
        rows = []
        for directory in selected:
            items = []
            for path in (directory / "work_items").glob("*.json"):
                try:
                    items.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
            business = sum("business_progress=true" in " ".join(item.get("evidence") or []) for item in items)
            scouts = sum("[portfolio_scout=true]" in str(item.get("instruction") or "") for item in items)
            rows.append({"campaign_id": directory.name, "work_items": len(items),
                         "business_progress_items": business, "scouts": scouts,
                         "business_density": round(business / max(1, len(items)), 4)})
        total = sum(row["work_items"] for row in rows)
        business = sum(row["business_progress_items"] for row in rows)
        scouts = sum(row["scouts"] for row in rows)
        return {"ok": bool(rows), "strategy_id": strategy, "campaign_density": rows,
                "command": ["deterministic_campaign_ledger_analysis"], "exit_code": 0,
                "test_output": json.dumps(rows, ensure_ascii=False, indent=2),
                "business_metrics": {"campaigns_analyzed": len(rows), "work_items": total,
                                     "business_progress_items": business, "scout_items": scouts,
                                     "business_density": round(business / max(1, total), 4),
                                     "degraded": business / max(1, total) < 0.30}}
    else:
        command = [sys.executable, "-m", "pytest", "tests/test_rl_control.py", "tests/test_campaign.py", "-q"]
    proc = subprocess.run(command, cwd=code, text=True, capture_output=True, timeout=180, check=False)
    manifests = list((root / "share/evidence").glob("*/*/*/evidence_manifest.json"))
    return {"ok": proc.returncode == 0, "strategy_id": strategy, "command": command,
            "exit_code": proc.returncode, "test_output": (proc.stdout + proc.stderr)[-4000:],
            "business_metrics": {"tests_passed": proc.returncode == 0, "durable_evidence_bundles": len(manifests)}}


def _molecular_step(root: Path, strategy: str) -> dict[str, Any]:
    evidence_root = root / "share/evidence/molecular_generation"
    paths = sorted((path for path in evidence_root.glob("*/*/*.json")
                    if path.name != "evidence_manifest.json"),
                   key=lambda path: path.stat().st_mtime, reverse=True)[:40]
    records = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            records.append({"path": str(path), "strategy": value.get("strategy_id") or value.get("stage"),
                            "metrics": value.get("business_metrics") or value.get("metrics") or {},
                            "conclusion": value.get("conclusion") or value.get("decision") or ""})
    text = json.dumps(records, ensure_ascii=False).lower()
    risks = [
        {"risk": "small_official_test", "present": 'test_rows' in text or 'test' in text,
         "required_evidence": "report target-group uncertainty and avoid broad generalization"},
        {"risk": "uncertain_candidate_advantage", "present": 'inconclusive' in text or 'confidence' in text,
         "required_evidence": "pre-registered grouped bootstrap interval"},
        {"risk": "target_or_identity_leakage", "present": 'overlap' in text or 'leakage' in text,
         "required_evidence": "zero train/test identity-group overlap"},
        {"risk": "causal_overclaim", "present": True,
         "required_evidence": "external or experimental validation before efficacy claims"},
    ]
    if strategy == "02_next_experiment_gate":
        experiments = [
            {"hypothesis": "error varies materially across target groups", "event": "target_group_residual_bootstrap",
             "acceptance": "grouped CI and minimum group count reported", "automatic_promotion": False},
            {"hypothesis": "calibration differs between central and tail slices", "event": "slice_calibration_curve",
             "acceptance": "predefined bins, coverage and uncertainty reported", "automatic_promotion": False},
            {"hypothesis": "candidate improvement survives identity-level resampling", "event": "identity_bootstrap_compare",
             "acceptance": "95% CI excludes zero without leakage", "automatic_promotion": False},
        ]
        return {"ok": bool(records), "strategy_id": strategy, "next_experiments": experiments,
                "evidence_records": len(records), "business_metrics": {"experiments_specified": len(experiments),
                "experiments_executed": 0, "automatic_promotion": 0}}
    return {"ok": bool(records), "strategy_id": strategy, "model_risk_register": risks,
            "evidence_records": records[:12], "business_metrics": {"evidence_files_reviewed": len(records),
            "risks_registered": len(risks), "risks_with_current_signal": sum(row["present"] for row in risks),
            "production_promotion": 0}}


def _harness_step(root: Path, strategy: str) -> dict[str, Any]:
    sources = {
        "deepseek": root / "external/code/deepseek-harness/docs/architecture.md",
        "codex": root / "external/code/openai-codex/codex-rs/rollout-trace/README.md",
    }
    partner_files = {
        "durable_evidence": Path(__file__).resolve().parents[1] / "governance/evidence_archive.py",
        "offline_reducer": Path(__file__).resolve().parents[1] / "governance/rl_evolution.py",
        "policy_control": Path(__file__).resolve().parents[1] / "governance/rl_control.py",
        "project_receipt": Path(__file__).resolve().parents[1] / "governance/project_loop.py",
    }
    concepts = {
        "append_only_evidence": ["append-only", "append_items", "raw events"],
        "model_visible_boundary": ["model-visible", "ConversationItem"],
        "lifecycle_brackets": ["turn/start", "turn start/end"],
        "observe_then_reduce": ["observe first, interpret later", "derived from the log"],
    }
    texts = {name: path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
             for name, path in sources.items()}
    mapping = {concept: {name: any(term.lower() in text.lower() for term in terms)
                         for name, text in texts.items()} for concept, terms in concepts.items()}
    implementation = {name: {"path": str(path), "exists": path.is_file()} for name, path in partner_files.items()}
    result = {"ok": all(texts.values()) and all(row["exists"] for row in implementation.values()),
            "strategy_id": strategy, "source_concepts": mapping, "partner_implementation": implementation,
            "copied_source": False,
            "business_metrics": {"concepts_mapped": len(mapping),
                                 "partner_contracts_present": sum(row["exists"] for row in implementation.values())}}
    if strategy == "04_reference_gap_matrix":
        result["reference_gap_matrix"] = [
            {"concept": name, "external_evidence": any(values.values()),
             "partner_contract": name in {"append_only_evidence", "observe_then_reduce"},
             "decision": "retain_independent_adapter" if any(values.values()) else "insufficient_source_evidence"}
            for name, values in mapping.items()
        ]
        result["business_metrics"]["gaps_identified"] = sum(
            not row["partner_contract"] for row in result["reference_gap_matrix"])
    elif strategy == "04_adoption_backlog":
        result["adoption_backlog"] = [
            {"candidate": "model_visible_evidence_boundary", "test": "fixture separates model/runtime/delivery facts",
             "rollback": "remove adapter only", "copied_source": False},
            {"candidate": "lifecycle_bracket_events", "test": "restart replay preserves start/end ordering",
             "rollback": "retain current append-only event schema", "copied_source": False},
        ]
        result["business_metrics"]["candidate_experiments"] = len(result["adoption_backlog"])
    return result


def atomic_continuous_project_step(ctx: Any, params: dict) -> dict:
    root, working, instance = _context(ctx)
    strategy = str(params.get("strategy_id") or "")
    if instance == "01":
        result = _content_step(root, strategy)
    elif instance == "02":
        if strategy in {"02_model_risk_register", "02_next_experiment_gate"}:
            result = _molecular_step(root, strategy)
        else:
            from partner.v2.targetdiff_official_split_events import (
                atomic_official_split_calibration, atomic_official_split_error_slices,
            )
            delegated = atomic_official_split_error_slices if strategy == "02_error_slices" else atomic_official_split_calibration
            return delegated(ctx, params)
    elif instance == "03":
        result = _framework_step(root, strategy)
    elif instance == "04":
        result = _harness_step(root, strategy)
    else:
        return {"ok": False, "status": "unsupported_instance", "error": instance}
    output = working / f"{strategy or 'continuous_project_step'}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    from partner.v2.domain_reports import render_continuous_report
    report = render_continuous_report(instance, strategy, result)
    md = working / f"{strategy or 'continuous_project_step'}.md"
    md.write_text(report, encoding="utf-8")
    from partner.v2.pdf_events import atomic_generate_detailed_pdf
    pdf = working / f"{strategy or 'continuous_project_step'}.pdf"
    pdf_result = atomic_generate_detailed_pdf(ctx, {"content": report, "output_path": str(pdf),
        "title": f"持续项目推进 {strategy}", "min_content_chars": 700, "min_sections": 4})
    ok = bool(result.get("ok") and pdf_result.get("ok"))
    return {"ok": ok, "status": "completed" if ok else "verification_failed",
            "summary": f"{strategy} 已执行；业务指标={result.get('business_metrics')}",
            "result": result, "files": [str(output), str(md), str(pdf)]}


HANDLERS = {"continuous_project_step": atomic_continuous_project_step}
