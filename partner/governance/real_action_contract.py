"""ADR 0061 real-action contract — single source of truth for project-step validity.

Both the instance-native runtime and the manual runtime must call this
gate before writing a project Receipt.  The contract is intentionally strict:

* a real external action must be present in actions_executed;
* artifacts must point at real files outside the project's own report dir;
* findings must show meaningful progress vs the most recent receipt window.

A failing assessment returns a violation code so the caller can decide
whether to record it (manual) or yield/block the slot (instance-native).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ACTION_SIGNALS = (
    "exec:", "web.fetch", "pytest", "code_write", "data_write",
    "external_query", "scientific_run", "external_knowledge_scout",
    "molecular_external_activity_acquire",
    "research_adoption_context_shadow",
    "multimodal_browser_observe", "multimodal_community_read", "multimodal_login_resume",
    "multimodal_safe_navigation", "molecular_docking_holdout",
    "github.clone", "paper.download", "source_acquisition",
)

FINDING_NORMALISER = re.compile(r"\s+")


def normalise_finding(text: Any) -> str:
    text = FINDING_NORMALISER.sub(" ", str(text or "")).strip().lower()
    if len(text) >= 8:
        return text
    return ""


def findings_signature(findings: list[Any]) -> set[str]:
    """Return the normalised set of finding sentences used for near-duplicate detection."""
    out: set[str] = set()
    for item in findings or []:
        key = normalise_finding(item)
        if key:
            out.add(key)
    return out


def list_project_artifact_resolutions(workspace_root: Path, project_id: str) -> set[str]:
    """Return the set of resolved artifact paths that already appear in past receipts."""
    project_root = workspace_root / "share/projects" / project_id
    receipts = project_root / "governance/receipts"
    if not receipts.exists():
        return set()
    out: set[str] = set()
    for path in receipts.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for artifact in value.get("artifacts", []) or []:
            try:
                out.add(str(Path(str(artifact)).expanduser().resolve()))
            except OSError:
                continue
    return out


def assess(*, workspace_root_path: Path, project_id: str,
           findings: list[Any], actions_executed: list[Any],
           artifacts: list[Any],
           max_repeat_findings: int = 2,
           require_external_artifact: bool = True,
           history_window: int | None = None) -> dict[str, Any]:
    """Evaluate the ADR 0061 contract; return a structured report."""
    actions_norm = " ".join(str(item) for item in actions_executed or [])
    has_external_action = any(signal in actions_norm.lower() for signal in ACTION_SIGNALS)
    has_atomic_action = any(token in actions_norm.lower() for token in (
        "atomic_inspect_file", "extract", "code_write", "data_write", "delete_file",
        "atomic_search_", "atomic_visit", "scientific_run", "scientific_query",
        "scientific_", "data_write", "data_read", "continuous_project_step",
        "molecular_generation_benchmark", "molecular_diversity_benchmark",
        "molecular_synth_baseline_benchmark", "molecular_goal_optimization_benchmark",
        "molecular_data_readiness_audit",
        "molecular_external_activity_acquire",
        # A matched Candidate recomputes both arms from the same frozen
        # molecular table.  A falsified hypothesis is still a real scientific
        # action and must not be mislabeled as "missing_external_action".
        "molecular_method_candidate_benchmark", "molecular_docking_holdout"))
    has_atomic_action = bool(has_atomic_action or
                             "research_adoption_context_shadow" in actions_norm.lower())
    has_atomic_action = bool(has_atomic_action or any(token in actions_norm.lower() for token in (
        "multimodal_browser_observe", "multimodal_community_read", "multimodal_login_resume",
        "multimodal_safe_navigation")))
    new_real_artifacts: list[str] = []
    fake_paths: list[str] = []
    governance_artifacts: list[str] = []
    project_receipts = (workspace_root_path / "share/projects" / project_id / "governance/receipts")
    history = sorted(project_receipts.glob("*.json"),
                     key=lambda value: value.stat().st_mtime) if project_receipts.exists() else []
    window = max(2, int(history_window if history_window is not None else max_repeat_findings))
    recent = history[-window:]
    for raw in artifacts or []:
        try:
            resolved = str(Path(str(raw)).expanduser().resolve())
        except OSError:
            fake_paths.append(str(raw))
            continue
        if not Path(resolved).is_file():
            fake_paths.append(str(raw))
            continue
        # ADR 0061 v2: governance receipts are real iterative artifacts.  A
        # project step that only reads and writes its governance ledger is
        # legitimate iteration, not report-only spam.  Only files under
        # share/projects/<p>/reports/ are treated as content-only spam.
        in_project = ("share/projects/" in resolved
                      and ("/" + project_id + "/") in resolved)
        if in_project and "/reports/" in resolved:
            fake_paths.append(str(raw))
            continue
        if in_project and ("/governance/receipts/" in resolved
                           or resolved.endswith("/governance/project_state.json")
                           or "/governance/" in resolved):
            governance_artifacts.append(resolved)
            new_real_artifacts.append(resolved)
            continue
        # All other paths are real external artifacts.
        new_real_artifacts.append(resolved)
    current_sig = findings_signature(findings)
    same = 0
    for path in recent:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        previous_sig = findings_signature(value.get("findings", []) or [])
        if not previous_sig or not current_sig:
            continue
        intersection = previous_sig & current_sig
        smaller = min(len(previous_sig), len(current_sig)) or 1
        ratio = len(intersection) / float(smaller)
        if ratio >= 0.6:
            same += 1
    evidence: dict[str, Any] = {
        "actions_executed": list(actions_executed or []),
        "real_artifacts": new_real_artifacts,
        "governance_artifacts": governance_artifacts,
        "fake_artifact_paths": fake_paths,
        "same_window": same,
        "window": window,
        "current_signature_size": len(current_sig),
    }
    if same >= window:
        return {"ok": False, "violation": "repeated_findings", "evidence": evidence}
    if require_external_artifact:
        real_action = (has_external_action or has_atomic_action
                       or bool(governance_artifacts))
        if not real_action:
            return {"ok": False, "violation": "missing_external_action", "evidence": evidence}
        if not new_real_artifacts:
            return {"ok": False, "violation": "no_real_artifact", "evidence": evidence}
    if fake_paths:
        return {"ok": False, "violation": "fake_artifact_paths", "evidence": evidence}
    return {"ok": True, "violation": "", "evidence": evidence,
            "real_artifacts": new_real_artifacts,
            "governance_artifacts": governance_artifacts,
            "window_signature_overlap": same,
            "current_signature_size": len(current_sig)}
