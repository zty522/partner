"""Bounded, generic code-invention laboratory.

The laboratory may ask an LLM for a patch, but machine-owned gates decide the
result.  It never edits the live repository: successful output is a shadow
Candidate that a later Event-first promotion flow may consume.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4
from pathlib import Path
from typing import Any

from partner.adapters.direct_api import chat
from partner.governance.maturity_gates import assess_sprint24
from partner.governance.models import now_iso
from partner.governance.storage import atomic_json, workspace_root
from partner.governance.evolution_events import append_evolution_event


DENIED_TARGET_PARTS = {
    "tests", ".git", "control_policy.json", "production_readiness.py",
    "experience_policy.py", "evolution_events.py", "code_invention_lab.py",
}
ALLOWED_TARGET_ROOTS = (
    "partner/adapters/", "partner/cli/", "partner/core/", "partner/mind/",
    "partner/monitoring/", "partner/planner/", "partner/v2/", "shells/frontend/",
)


def _inside(repo: Path, relative: str) -> Path:
    candidate = (repo / relative).resolve()
    if candidate != repo and repo not in candidate.parents:
        raise ValueError("path_outside_repository")
    return candidate


def _targets(repo: Path, values: list[str]) -> list[str]:
    unique: list[str] = []
    for raw in values:
        value = str(raw or "").replace("\\", "/").lstrip("./")
        if value in unique:
            continue
        if not any(value.startswith(root) for root in ALLOWED_TARGET_ROOTS):
            raise ValueError(f"target_not_allowlisted:{value}")
        if any(part in DENIED_TARGET_PARTS for part in Path(value).parts):
            raise ValueError(f"target_denied:{value}")
        path = _inside(repo, value)
        if not path.is_file() or path.suffix != ".py" or path.stat().st_size > 60_000:
            raise ValueError(f"target_not_small_python_source:{value}")
        unique.append(value)
    if not 1 <= len(unique) <= 3:
        raise ValueError("candidate_requires_one_to_three_targets")
    return unique


def _tests(repo: Path, values: list[str]) -> list[str]:
    output: list[str] = []
    for raw in values:
        value = str(raw or "").replace("\\", "/").lstrip("./")
        file_part, separator, node_part = value.partition("::")
        path = _inside(repo, file_part)
        if (not file_part.startswith("tests/") or not path.is_file()
                or path.suffix != ".py"
                or (separator and not re.fullmatch(r"[A-Za-z0-9_:.\-\[\]]+", node_part))):
            raise ValueError(f"test_not_preexisting:{value}")
        output.append(value)
    if not output:
        raise ValueError("preexisting_reproducer_required")
    return output


def _run_pytest(repo: Path, tests: list[str], timeout: int = 180) -> dict[str, Any]:
    command = ["/home/os/miniconda3/bin/python", "-m", "pytest", "-q", *tests, "--tb=short"]
    try:
        done = subprocess.run(command, cwd=repo, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return {"command": command, "exit_code": done.returncode,
                "stdout": done.stdout[-8000:], "stderr": done.stderr[-4000:]}
    except subprocess.TimeoutExpired as exc:
        return {"command": command, "exit_code": 124,
                "stdout": str(exc.stdout or "")[-8000:],
                "stderr": "candidate test timeout"}


def _json_object(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S | re.I)
    decoder = json.JSONDecoder()
    values = []
    for match in re.finditer(r"\{", cleaned):
        try:
            value, used = decoder.raw_decode(cleaned[match.start():])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            values.append((used, value))
    return max(values, key=lambda row: row[0])[1] if values else {}


def _redact(text: str) -> str:
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+",
                  r"\1=<REDACTED>", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "<REDACTED_KEY>", text)
    return text


def _patch_paths(patch: str) -> set[str]:
    paths = set()
    for line in patch.splitlines():
        if line.startswith(("--- a/", "+++ b/")):
            paths.add(line[6:].strip())
    return paths


def _normalize_patch(value: Any) -> str:
    patch = str(value or "").strip()
    if patch.startswith("```diff") or patch.startswith("```patch"):
        patch = patch.split("\n", 1)[1] if "\n" in patch else ""
    if patch.endswith("```"):
        patch = patch[:-3].rstrip()
    return patch + "\n" if patch else ""


def _apply_exact_unified_diff(repo: Path, patch: str, targets: list[str]) -> tuple[bool, str]:
    """Apply hunks only when each old sequence has one exact source match.

    Some providers emit valid-looking minimal hunks that GNU patch accepts
    only with fuzz and ``git apply`` rejects.  Fuzz is too permissive for code
    invention.  This fallback ignores line-number drift but requires the full
    old hunk (including context) to occur exactly once, and writes only the
    already allowlisted target set inside the isolated repository.
    """
    per_file: dict[str, list[tuple[list[str], list[str]]]] = {}
    current = ""
    old: list[str] | None = None
    new: list[str] | None = None
    try:
        for line in patch.splitlines():
            if line.startswith("--- a/"):
                old, new = None, None
            elif line.startswith("+++ b/"):
                current = line[6:].strip()
                old, new = None, None
                if current not in targets:
                    return False, f"unexpected_target:{current}"
            elif line.startswith("@@"):
                if not current:
                    return False, "hunk_without_target"
                old, new = [], []
                per_file.setdefault(current, []).append((old, new))
            elif old is not None and new is not None:
                if line.startswith("\\ No newline"):
                    continue
                if not line or line[0] not in {" ", "+", "-"}:
                    return False, "invalid_hunk_line"
                content = line[1:]
                if line[0] in {" ", "-"}:
                    old.append(content)
                if line[0] in {" ", "+"}:
                    new.append(content)
        if set(per_file) != set(targets):
            return False, "target_hunks_incomplete"
        staged: dict[Path, str] = {}
        for relative, hunks in per_file.items():
            path = _inside(repo, relative)
            raw = path.read_text(encoding="utf-8")
            lines = raw.splitlines()
            for old_lines, new_lines in hunks:
                if not old_lines:
                    return False, "empty_old_hunk"
                matches = [index for index in range(len(lines) - len(old_lines) + 1)
                           if lines[index:index + len(old_lines)] == old_lines]
                if len(matches) != 1:
                    return False, f"old_hunk_match_count:{len(matches)}"
                index = matches[0]
                lines[index:index + len(old_lines)] = new_lines
            staged[path] = "\n".join(lines) + ("\n" if raw.endswith("\n") else "")
        for path, content in staged.items():
            path.write_text(content, encoding="utf-8")
        return True, "exact_unique_hunks_applied"
    except (OSError, TypeError, ValueError) as exc:
        return False, f"{type(exc).__name__}:{exc}"


def _repair_patch_response(*, root: Path, targets: list[str], sources: dict[str, str],
                           issue: dict[str, Any], prior: str, error: str) -> dict[str, Any]:
    """One bounded syntax/applicability repair without revealing hidden tests."""
    raw = chat(
        "Repair the FORMAT/APPLICABILITY of a proposed unified diff. Return one strict JSON object "
        "with keys unified_diff, causal_hypothesis, counterexample, risk. Preserve the intended "
        "causal repair, but make the patch apply exactly to the supplied source. Modify exactly the "
        "listed target files and no others. Use numeric hunk ranges, no markdown fence, no tests, no "
        "network calls. Hidden tests and their contents are not available.\n"
        + _redact(json.dumps({"issue": issue, "required_targets": targets,
                              "apply_error": error[-2000:], "prior_response": prior[-12000:],
                              "sources": sources}, ensure_ascii=False)),
        purpose="generic_code_candidate_diff_repair", max_tokens=6000,
        temperature=0.0, timeout=180, workspace=str(root), instance_id="05",
        project_id="agent_self_evolution", event_type="generic_code_candidate_shadow")
    return _json_object(raw)


def _copy_repo(repo: Path) -> Path:
    destination = Path(tempfile.mkdtemp(prefix="partner_code_invention_")) / "repo"
    shutil.copytree(repo, destination, ignore=shutil.ignore_patterns(
        ".git", ".pytest_cache", "__pycache__", "*.pyc", "docs", "artifacts"))
    return destination


def run_shadow_code_invention(workspace: str | Path, repo_root: str | Path, *,
                              issue: dict[str, Any], target_files: list[str],
                              reproducer_tests: list[str], hidden_tests: list[str]) -> dict[str, Any]:
    root = workspace_root(str(workspace))
    repo = Path(repo_root).resolve()
    targets = _targets(repo, target_files)
    reproducers = _tests(repo, reproducer_tests)
    hidden = _tests(repo, hidden_tests)
    candidate_id = ("generic_code_candidate_"
                    + now_iso().replace("-", "").replace(":", "").replace("+", "_")
                    + "_" + uuid4().hex[:8])
    directory = root / "share/mind/governance/code_candidates" / candidate_id
    directory.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": 1, "candidate_id": candidate_id,
        "kind": "generic_shadow_code_invention", "created_at": now_iso(),
        "targets": targets, "reproducer_tests": reproducers, "hidden_tests": hidden,
        "production_effective": False, "maturity_prerequisite": assess_sprint24(root),
    }
    baseline = _run_pytest(repo, reproducers)
    result["baseline"] = baseline
    if baseline["exit_code"] == 0:
        result.update({"ok": False, "decision": "rejected",
                       "reason": "baseline_does_not_reproduce_issue"})
        atomic_json(directory / "result.json", result)
        return result
    sources = {path: _redact((repo / path).read_text(encoding="utf-8")) for path in targets}
    prompt = (
        "You are proposing one minimal Python repair. Return strict JSON with keys unified_diff, "
        "causal_hypothesis, counterexample, risk. The diff may modify only the supplied files, at most "
        "three files. Do not modify tests, governance/reward/promotion code, credentials, or add network "
        "calls. unified_diff must be a directly git-apply-able patch: every hunk header must include "
        "numeric old/new ranges such as @@ -1,4 +1,4 @@; do not use a bare @@ header or markdown fence. "
        "The existing reproducer failed; hidden tests are not shown.\nISSUE:\n"
        + _redact(json.dumps(issue, ensure_ascii=False)) + "\nSOURCES:\n"
        + json.dumps(sources, ensure_ascii=False))
    raw_proposal = chat(
        prompt, purpose="generic_code_candidate_design", max_tokens=6000,
        temperature=0.1, timeout=180, workspace=str(root), instance_id="05",
        project_id="agent_self_evolution", event_type="generic_code_candidate_shadow")
    proposal = _json_object(raw_proposal)
    patch = _normalize_patch(proposal.get("unified_diff"))
    repair_attempts: list[dict[str, Any]] = []
    if not patch or _patch_paths(patch) != set(targets) or len(patch) > 40_000:
        repaired = _repair_patch_response(
            root=root, targets=targets, sources=sources, issue=issue,
            prior=str(raw_proposal), error="unsafe_or_invalid_patch_surface")
        repair_attempts.append({"stage": "surface", "received": bool(repaired)})
        if repaired:
            proposal = repaired
            patch = _normalize_patch(proposal.get("unified_diff"))
    result["llm_proposal"] = {key: proposal.get(key) for key in
                              ("causal_hypothesis", "counterexample", "risk")}
    result["proposal_model_calls"] = 1 + len(repair_attempts)
    result["proposal_repair_attempts"] = repair_attempts
    if not patch or _patch_paths(patch) != set(targets) or len(patch) > 40_000:
        result.update({"ok": False, "decision": "rejected", "reason": "unsafe_or_invalid_patch_surface"})
        atomic_json(directory / "result.json", result)
        return result
    patch_path = directory / "candidate.patch"
    patch_path.write_text(patch, encoding="utf-8")
    proposed_event = append_evolution_event(
        str(root), "candidate/proposed", subject_id=candidate_id,
        project_id="agent_self_evolution",
        payload={"candidate_id": candidate_id, "kind": "generic_shadow_code_invention",
                 "targets": targets, "production_effective": False},
        evidence_refs=[str(patch_path)], idempotency_key=f"generic-code-proposed:{candidate_id}")
    started_event = append_evolution_event(
        str(root), "experiment/started", subject_id=candidate_id,
        project_id="agent_self_evolution", parents=[proposed_event["event_id"]],
        payload={"candidate_id": candidate_id, "baseline": "preexisting_reproducer",
                 "candidate": "isolated_patch", "production_effective": False},
        evidence_refs=[str(patch_path)], idempotency_key=f"generic-code-experiment:{candidate_id}")
    isolated = _copy_repo(repo)
    # Models sometimes normalise indentation in unchanged context lines.  Git's
    # whitespace-tolerant context matching is safe here because the target path
    # set was already exact-allowlisted and hidden tests still own acceptance.
    applied = subprocess.run(["git", "apply", "--no-index", "--ignore-space-change",
                              str(patch_path)], cwd=isolated,
                             capture_output=True, text=True, check=False)
    if applied.returncode != 0:
        exact_ok, exact_status = _apply_exact_unified_diff(isolated, patch, targets)
        if exact_ok:
            applied = subprocess.CompletedProcess(
                args=["exact_unified_diff"], returncode=0, stdout="", stderr=exact_status)
    if applied.returncode != 0:
        repaired = _repair_patch_response(
            root=root, targets=targets, sources=sources, issue=issue, prior=patch,
            error=applied.stderr)
        repaired_patch = _normalize_patch(repaired.get("unified_diff"))
        repair_attempts.append({"stage": "apply", "received": bool(repaired),
                                "valid_surface": bool(repaired_patch
                                                      and _patch_paths(repaired_patch) == set(targets)
                                                      and len(repaired_patch) <= 40_000)})
        result["proposal_model_calls"] = 1 + len(repair_attempts)
        result["proposal_repair_attempts"] = repair_attempts
        if repair_attempts[-1]["valid_surface"]:
            (directory / "candidate_initial.patch").write_text(patch, encoding="utf-8")
            patch = repaired_patch
            patch_path.write_text(patch, encoding="utf-8")
            isolated = _copy_repo(repo)
            applied = subprocess.run(
                ["git", "apply", "--no-index", "--ignore-space-change", str(patch_path)],
                cwd=isolated, capture_output=True, text=True, check=False)
            if applied.returncode != 0:
                exact_ok, exact_status = _apply_exact_unified_diff(isolated, patch, targets)
                if exact_ok:
                    applied = subprocess.CompletedProcess(
                        args=["exact_unified_diff"], returncode=0,
                        stdout="", stderr=exact_status)
    result["patch_apply"] = {"exit_code": applied.returncode,
                             "stderr": applied.stderr[-4000:]}
    if applied.returncode != 0:
        result.update({"ok": False, "decision": "rejected", "reason": "patch_did_not_apply"})
        atomic_json(directory / "result.json", result)
        completed_event = append_evolution_event(
            str(root), "experiment/completed", subject_id=candidate_id,
            project_id="agent_self_evolution", parents=[started_event["event_id"]],
            payload={"candidate_id": candidate_id, "decision": "rejected",
                     "reason": "patch_did_not_apply", "baseline_failed": True,
                     "candidate_passed": False, "hidden_passed": False,
                     "production_effective": False},
            evidence_refs=[str(patch_path), str(directory / "result.json")],
            idempotency_key=f"generic-code-completed:{candidate_id}")
        result["evolution_event_ids"] = [proposed_event["event_id"], started_event["event_id"],
                                         completed_event["event_id"]]
        atomic_json(directory / "result.json", result)
        return result
    candidate_test = _run_pytest(isolated, reproducers)
    hidden_test = _run_pytest(isolated, hidden)
    result.update({"candidate_test": candidate_test, "hidden_test": hidden_test})
    critic = _json_object(chat(
        "Independently audit this code Candidate. Production is still false by design. Return strict JSON "
        "with causal_isolation=pass|uncertain|fail, recommendation=promote|reject|more_evidence, "
        "missing_tests, rollback_trigger. Do not treat production_effective=false as failure.\n"
        + _redact(json.dumps({"issue": issue, "targets": targets,
                              "patch": patch, "baseline": baseline,
                              "candidate": candidate_test, "hidden": hidden_test}, ensure_ascii=False)),
        purpose="generic_code_candidate_critic", max_tokens=3500,
        temperature=0.0, timeout=180, workspace=str(root), instance_id="05",
        project_id="agent_self_evolution", event_type="generic_code_candidate_shadow"))
    result["independent_critic"] = critic
    passed = (candidate_test["exit_code"] == 0 and hidden_test["exit_code"] == 0
              and critic.get("causal_isolation") == "pass"
              and critic.get("recommendation") == "promote")
    result.update({"ok": passed, "decision": "validated_shadow" if passed else "rejected",
                   "reason": "all_shadow_gates_passed" if passed else "candidate_or_critic_gate_failed",
                   "production_effective": False})
    atomic_json(directory / "result.json", result)
    completed_event = append_evolution_event(
        str(root), "experiment/completed", subject_id=candidate_id,
        project_id="agent_self_evolution", parents=[started_event["event_id"]],
        payload={"candidate_id": candidate_id, "decision": result["decision"],
                 "baseline_failed": baseline["exit_code"] != 0,
                 "candidate_passed": candidate_test["exit_code"] == 0,
                 "hidden_passed": hidden_test["exit_code"] == 0,
                 "critic": critic, "production_effective": False},
        evidence_refs=[str(patch_path), str(directory / "result.json")],
        idempotency_key=f"generic-code-completed:{candidate_id}")
    result["evolution_event_ids"] = [proposed_event["event_id"], started_event["event_id"],
                                     completed_event["event_id"]]
    atomic_json(directory / "result.json", result)
    return result


__all__ = ["run_shadow_code_invention"]
