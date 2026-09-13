"""Fail-closed registry for externally produced wet-lab synthesis evidence.

Computational proxies (docking, SA score, retrosynthesis confidence) are useful,
but they are not experiments.  This module admits an evidence row only when the
underlying primary report can be reopened, hashed, and shown to contain the
quoted experimental observation.  It also keeps the important distinction
between published external evidence and a Partner-generated molecule that was
actually synthesized in a laboratory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import now_iso
from .storage import append_jsonl, atomic_json, workspace_root
from .evolution_events import append_evolution_event


LEDGER = "share/projects/molecular_generation/metrics/experimental_synthesis_evidence.jsonl"
ALLOWED_MODALITIES = {"flow_chemistry", "batch_synthesis", "automated_synthesis"}


def _normalise(value: str) -> str:
    return " ".join(str(value or "").split())


def _load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def validate_experimental_synthesis_record(workspace: str, record: dict[str, Any]) -> dict[str, Any]:
    """Validate provenance and semantics without trusting a caller's boolean."""
    root = workspace_root(workspace).resolve()
    source = Path(str(record.get("source_path") or "")).resolve()
    literature = (root / "external/literature").resolve()
    checks: dict[str, bool] = {}
    try:
        body = source.read_bytes()
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        body, text = b"", ""
    checks["source_inside_external_literature"] = bool(
        source.is_file() and (source == literature or literature in source.parents))
    digest = hashlib.sha256(body).hexdigest() if body else ""
    checks["source_sha256_matches"] = bool(
        len(str(record.get("source_sha256") or "")) == 64
        and digest == str(record.get("source_sha256") or "").lower())
    quote = _normalise(str(record.get("evidence_quote") or ""))
    checks["quote_is_verbatim_in_source"] = bool(
        len(quote) >= 40 and quote in _normalise(text))
    checks["primary_report_has_stable_citation"] = bool(
        str(record.get("source_kind") or "") == "published_primary_report"
        and str(record.get("citation_url") or "").startswith(("https://", "http://")))
    checks["wet_lab_modality_not_proxy"] = bool(
        str(record.get("modality") or "") in ALLOWED_MODALITIES
        and str(record.get("modality") or "") not in {"docking", "sa_score", "simulation"})
    outcome = dict(record.get("experimental_outcome") or {})
    checks["measured_outcome_is_numeric"] = bool(
        isinstance(outcome.get("before"), (int, float))
        and isinstance(outcome.get("after"), (int, float))
        and isinstance(outcome.get("experiment_count"), int)
        and outcome.get("experiment_count", 0) > 0
        and str(outcome.get("metric") or "").strip())
    checks["external_experiment_declared_honestly"] = bool(
        record.get("external_experiment") is True
        and record.get("partner_executed") is False)
    linked = dict(record.get("partner_candidate_link") or {})
    checks["partner_candidate_identity_present"] = bool(
        str(linked.get("candidate_id") or "").strip()
        and str(linked.get("canonical_smiles") or "").strip())
    provenance_checks = {key: value for key, value in checks.items()
                         if key != "partner_candidate_identity_present"}
    return {
        "ok": all(provenance_checks.values()), "checks": checks, "source_path": str(source),
        "source_sha256": digest, "evidence_scope": (
            "partner_candidate_wet_lab" if checks["partner_candidate_identity_present"]
            else "external_published_wet_lab"),
    }


def register_experimental_synthesis_evidence(workspace: str, record: dict[str, Any]) -> dict[str, Any]:
    validation = validate_experimental_synthesis_record(workspace, record)
    if not validation["ok"]:
        return {"ok": False, "status": "experimental_evidence_rejected",
                "validation": validation, "production_effective": False,
                "retryable": False}
    root = workspace_root(workspace)
    ledger = root / LEDGER
    evidence_id = "synth_evidence_" + hashlib.sha256(json.dumps(
        {"source_sha256": validation["source_sha256"],
         "quote": _normalise(str(record.get("evidence_quote") or "")),
         "link": record.get("partner_candidate_link") or {}},
        ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    row = {**record, "schema_version": 1, "evidence_id": evidence_id,
           "created_at": str(record.get("created_at") or now_iso()),
           "validation": validation,
           "evidence_scope": validation["evidence_scope"],
           "production_effective": False}
    if not any(str(old.get("evidence_id") or "") == evidence_id for old in _load_rows(ledger)):
        append_jsonl(ledger, row)
    artifact = ledger.parent / "experimental_synthesis" / f"{evidence_id}.json"
    atomic_json(artifact, row)
    event = append_evolution_event(
        str(root), "evidence/experimental_synthesis_registered",
        subject_id=evidence_id, project_id="molecular_generation",
        payload={"evidence_id": evidence_id, "evidence_scope": row["evidence_scope"],
                 "source_sha256": validation["source_sha256"],
                 "partner_executed": False, "production_effective": False},
        evidence_refs=[str(artifact), str(record.get("source_path") or "")],
        idempotency_key=f"experimental-synthesis-evidence:{evidence_id}")
    return {"ok": True, "status": "experimental_evidence_registered", **row,
            "path": str(artifact), "files": [str(artifact)],
            "ledger_path": str(ledger), "event": event}


def audit_experimental_synthesis_evidence(workspace: str) -> dict[str, Any]:
    root = workspace_root(workspace)
    rows = _load_rows(root / LEDGER)
    verified: list[dict[str, Any]] = []
    for row in rows:
        validation = validate_experimental_synthesis_record(str(root), row)
        if validation["ok"]:
            verified.append({**row, "validation": validation,
                             "evidence_scope": validation["evidence_scope"]})
    external = [row for row in verified
                if row.get("evidence_scope") == "external_published_wet_lab"]
    linked = [row for row in verified
              if row.get("evidence_scope") == "partner_candidate_wet_lab"]
    return {"ok": bool(verified), "verified_records": len(verified),
            "published_external_records": len(external),
            "partner_candidate_wet_lab_records": len(linked),
            "published_experimental_synthesis_evidence": bool(external or linked),
            "partner_candidate_wet_lab_validation": bool(linked),
            "evidence": verified[-10:]}
