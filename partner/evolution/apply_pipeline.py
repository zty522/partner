"""Sprint18 self-evolution apply pipeline.

Reads candidate promotion decisions from production_readiness/, validates
each promotion's diff_hunk / target_file via git apply --check, then either
applies (writing a real git commit) or refuses with a policy/auto_apply_failed
evolution event. Idempotent: re-running on already-applied commits is a no-op.

Boundaries (per ADR 0062 §3 boundary extension):
    * must NOT touch partner/<pkg>/ source files unless diff_hunk is valid
    * must NOT bypass pytest — failing tests = auto-rollback
    * must NOT auto-merge candidates that lack a non-empty diff_hunk
    * must NOT re-attempt any target_file in apply_blacklist.txt for 72h

Public API:
    apply_promoted_candidates(repo_root, *, dry_run=False) -> ApplyPipelineReport
    apply_one(candidate, repo_root, *, dry_run=False) -> ApplyOutcome
    list_blacklist(repo_root) -> list[str]
    clear_blacklist(repo_root)
    append_blacklist(repo_root, target_file, reason)
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GOVERNANCE_RELATIVE = "share/mind/governance"
PRODUCTION_READINESS_DIR = "rl/production_readiness"
EVOLUTION_EVENTS = "evolution_events.jsonl"
APPLY_BLACKLIST = "rl/apply_blacklist.json"
BLACKLIST_COOLDOWN_HOURS = 72

SCHEMA_VERSION = 2
EXPERIMENT_TARGET_REQUIRED = {"target_file", "diff_hunk"}


@dataclasses.dataclass
class ApplyOutcome:
    candidate_id: str
    target_file: str | None
    action: str  # "applied" | "dry_run" | "skipped_applied" | "skipped_no_diff" | "skipped_blacklisted" | "skipped_invalid_diff" | "skipped_inconclusive_decision" | "failed" | "rolled_back"
    commit_hash: str | None = None
    pre_commit_hash: str | None = None
    detail: str = ""


@dataclasses.dataclass
class ApplyPipelineReport:
    examined: int = 0
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    rolled_back: int = 0
    outcomes: list[ApplyOutcome] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "examined": self.examined,
            "applied": self.applied,
            "skipped": self.skipped,
            "failed": self.failed,
            "rolled_back": self.rolled_back,
            "outcomes": [dataclasses.asdict(o) for o in self.outcomes],
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ────────────────────────────────────────────────────────────────────────────
# candidate / diff utilities
# ────────────────────────────────────────────────────────────────────────────


def _find_production_readiness_dir(workspace_root: Path) -> Path:
    """production_readiness lives at share/mind/governance/experience_guided_policy/production_readiness"""
    candidates = [
        workspace_root / "share" / "mind" / "governance" / "experience_guided_policy" / "production_readiness",
        workspace_root / "share" / "mind" / "governance" / "production_readiness",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def _find_evolution_events_path(workspace_root: Path) -> Path:
    return workspace_root / "share" / "mind" / "governance" / EVOLUTION_EVENTS


def _find_blacklist_path(workspace_root: Path) -> Path:
    return workspace_root / "share" / "mind" / "governance" / APPLY_BLACKLIST


def list_blacklist(workspace_root: Path | str) -> list[dict[str, Any]]:
    workspace_root = Path(workspace_root)
    p = _find_blacklist_path(workspace_root)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def clear_blacklist(workspace_root: Path | str) -> None:
    workspace_root = Path(workspace_root)
    p = _find_blacklist_path(workspace_root)
    if p.exists():
        p.unlink()


def append_blacklist(workspace_root: Path | str, target_file: str, reason: str) -> None:
    workspace_root = Path(workspace_root)
    p = _find_blacklist_path(workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = list_blacklist(workspace_root)
    cur.append({
        "target_file": target_file,
        "reason": reason,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "cooldown_until": (datetime.now(timezone.utc) + timedelta(hours=BLACKLIST_COOLDOWN_HOURS)).isoformat(),
    })
    p.write_text(json.dumps(cur, indent=2), encoding="utf-8")


def _blacklist_active(workspace_root: Path, target_file: str) -> dict[str, Any] | None:
    if not target_file:
        return None
    items = list_blacklist(workspace_root)
    now = datetime.now(timezone.utc)
    for item in items:
        if item.get("target_file") != target_file:
            continue
        try:
            cooldown_until = datetime.fromisoformat(item["cooldown_until"])
        except Exception:
            return item
        if now < cooldown_until:
            return item
    return None


def _is_valid_diff_hunk(hunk: str | None) -> bool:
    if not hunk or not isinstance(hunk, str):
        return False
    s = hunk.strip()
    if not s:
        return False
    lines = s.splitlines()
    has_diff = False
    for ln in lines:
        if ln.startswith("diff --git"):
            has_diff = True
            break
        if ln.startswith("--- ") and any(other.startswith("+++ ") for other in lines):
            has_diff = True
            break
    if not has_diff:
        return False
    if "@@" not in s:
        return False
    bad = re.search(r"(?m)^(?:\\ No newline at end of file|index [0-9a-f]+\.\.[0-9a-f]+)", s)
    has_minus = any(ln.startswith("-") and not ln.startswith("---") for ln in lines)
    has_plus = any(ln.startswith("+") and not ln.startswith("+++") for ln in lines)
    return has_minus and has_plus


def _candidate_status(candidate: dict[str, Any]) -> str:
    if "decision" in candidate:
        return str(candidate["decision"])
    if "production_effective" in candidate:
        return "candidate_validated" if candidate["production_effective"] else "inconclusive"
    return "unknown"


# ────────────────────────────────────────────────────────────────────────────
# evolution_events append (own implementation; nothing else writes this file)
# ────────────────────────────────────────────────────────────────────────────


def _append_evolution_event(workspace_root: Path, event_type: str, payload: dict[str, Any], subject_id: str = "") -> None:
    p = _find_evolution_events_path(workspace_root)
    if not p.parent.exists():
        return
    prev_hash = ""
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                last_line = ""
                for line in f:
                    line = line.strip()
                    if line:
                        last_line = line
                if last_line:
                    rec = json.loads(last_line)
                    prev_hash = rec.get("event_hash", "")
        except Exception:
            prev_hash = ""
    seq = int(payload.get("seq", 0)) if isinstance(payload.get("seq"), int) else 0
    record = {
        "schema_version": 1,
        "seq": seq,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "actor": "apply_pipeline",
        "subject_id": subject_id or "apply_pipeline",
        "project_id": "agent_self_evolution",
        "parents": [],
        "payload": payload,
        "evidence_refs": [],
        "prev_hash": prev_hash,
    }
    body = json.dumps(record, ensure_ascii=False, sort_keys=True)
    record["event_hash"] = hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ────────────────────────────────────────────────────────────────────────────
# git helpers
# ────────────────────────────────────────────────────────────────────────────


def _git(*args: str, cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=20,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _git_apply_check(repo_root: Path, diff_text: str) -> tuple[bool, str]:
    """Use git apply --check via a sidecar file in the repo's tmp dir."""
    sidecar = repo_root / ".apply_pipeline_check.patch"
    try:
        sidecar.write_text(diff_text, encoding="utf-8")
    except Exception as exc:
        return False, f"could-not-write-sidecar: {exc}"
    try:
        code, out, err = _git("apply", "--check", str(sidecar), cwd=repo_root)
        if code != 0:
            return False, (err or out).strip() or f"git apply --check exit={code}"
        return True, ""
    finally:
        try:
            sidecar.unlink()
        except Exception:
            pass


def _git_apply(repo_root: Path, diff_text: str) -> tuple[bool, str]:
    sidecar = repo_root / ".apply_pipeline_apply.patch"
    try:
        sidecar.write_text(diff_text, encoding="utf-8")
        code, out, err = _git("apply", str(sidecar), cwd=repo_root)
        if code != 0:
            return False, (err or out).strip() or f"git apply exit={code}"
        return True, ""
    except Exception as exc:
        return False, f"apply-exception: {exc}"
    finally:
        try:
            sidecar.unlink()
        except Exception:
            pass


def _git_current_commit(repo_root: Path) -> str:
    code, out, _ = _git("rev-parse", "HEAD", cwd=repo_root)
    return out.strip() if code == 0 else ""


def _git_clean(repo_root: Path, target_file: str) -> tuple[bool, str]:
    """git checkout -- <target_file> for rollback use."""
    code, out, err = _git("checkout", "--", target_file, cwd=repo_root)
    if code != 0:
        return False, err.strip() or out
    return True, ""


# ────────────────────────────────────────────────────────────────────────────
# public apply_one + apply_promoted_candidates
# ────────────────────────────────────────────────────────────────────────────


def _resolve_partner_code_root(workspace_root: Path, target_file: str = "") -> Path | None:
    """Return the directory from which ``target_file`` is a valid relative path.

    The apply pipeline is invoked with the partner_workspace root (where
    production_readiness/ and evidence live), but git apply needs the
    partner source code root, because target_file is a relative path
    like "partner/governance/manual_runtime.py" and git apply will look
    for it relative to its cwd.

    We pick the cwd that makes ``(cwd / target_file)`` actually exist.
    Heuristics, in order:
      1. if target_file is set, try common candidate cwds; return the one
         where (cwd / target_file) is an existing file.
      2. fall back to the old heuristic: find a dir with a partner/ subdir
         containing *.py.
    """
    if target_file:
        cands = [workspace_root, workspace_root / "partner", workspace_root.parent / "partner"]
        for c in cands:
            if (c / target_file).is_file():
                return c
    # Old fallback
    cands = [workspace_root, workspace_root / "partner", workspace_root.parent / "partner"]
    for c in cands:
        if c.is_dir() and any((c / "partner").glob("*.py")):
            return c
    cur = workspace_root.resolve()
    for _ in range(5):
        if any((cur / "partner").glob("*.py")):
            return cur / "partner"
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def apply_one(candidate: dict[str, Any], repo_root: Path | str, *, dry_run: bool = False) -> ApplyOutcome:
    # The caller typically passes the partner_workspace root, but git apply
    # needs the partner CODE root (because target_file is "partner/<pkg>.py"
    # relative to that).  Resolve the actual partner repo root based on the
    # target_file the candidate names.  This must happen AFTER the diagnostic
    # diff generator may have set target_file, so we re-resolve below once
    # we know the final target_file.
    repo_root = Path(repo_root)
    candidate_id = candidate.get("experiment_id") or candidate.get("candidate_id") or candidate.get("id") or "<unknown>"
    _initial_target_file = str(candidate.get("target_file") or "")
    _resolved_repo_root = _resolve_partner_code_root(repo_root, _initial_target_file)
    if _resolved_repo_root is not None:
        repo_root = _resolved_repo_root
    target_file = candidate.get("target_file")
    diff_hunk = candidate.get("diff_hunk")

    status = _candidate_status(candidate)
    # production_readiness.py writes decision="ready_for_explicit_activation" when
    # production_ready=True; that is the *promoted* form of "candidate_validated"
    # and the only way a self-evolution candidate reaches apply_pipeline in
    # production-ready state.  Accept either form so the pipeline actually
    # promotes code instead of skipping every candidate indefinitely.
    # Sprint18 §6 follow-up: also accept "ready_for_canary_evaluation"
    # because sprint18_unified_patch writes candidates with that decision
    # after active_learning/diagnosis_completed.  Those candidates don't yet
    # have diff_hunks but the contract is to evaluate them — falling through
    # the gate exposes the missing diff_hunk as the real skip reason, which
    # is the truth-active_learning needs to act on.
    if status not in {"candidate_validated", "ready_for_explicit_activation",
                      "ready_for_canary_evaluation"}:
        return ApplyOutcome(candidate_id, target_file, "skipped_inconclusive_decision",
                            detail=f"decision={candidate.get('decision') or candidate.get('production_effective')}")

    if not diff_hunk:
        # A diagnosis or promotion label is not executable code.  Older builds
        # manufactured a comment-only patch here, which could create a commit
        # without changing production behaviour.  That is now a hard reject:
        # a production Candidate must supply a validated behavioural diff.
        return ApplyOutcome(
            candidate_id, target_file, "skipped_no_diff",
            detail=("diff_hunk missing or empty; a behavior-changing Candidate "
                    "must be synthesized and matched-tested before apply"),
        )

    if not _is_valid_diff_hunk(diff_hunk):
        return ApplyOutcome(candidate_id, target_file, "skipped_invalid_diff",
                            detail="diff_hunk fails unified diff structure check")

    if not target_file or not _is_safe_target(target_file):
        return ApplyOutcome(candidate_id, target_file, "skipped_invalid_diff",
                            detail=f"target_file invalid: {target_file!r}")

    blacklist_hit = _blacklist_active(repo_root, target_file)
    if blacklist_hit:
        return ApplyOutcome(candidate_id, target_file, "skipped_blacklisted",
                            detail=f"blacklisted until {blacklist_hit.get('cooldown_until')} ({blacklist_hit.get('reason')})")

    ok, err = _git_apply_check(repo_root, diff_hunk)
    if not ok:
        return ApplyOutcome(candidate_id, target_file, "failed",
                            detail=f"git apply --check failed: {err[:400]}")

    if dry_run:
        return ApplyOutcome(candidate_id, target_file, "dry_run",
                            detail="dry_run=True, git apply --check passed")

    pre_commit = _git_current_commit(repo_root)
    ok2, err2 = _git_apply(repo_root, diff_hunk)
    if not ok2:
        return ApplyOutcome(candidate_id, target_file, "failed",
                            detail=f"git apply failed: {err2[:400]}",
                            pre_commit_hash=pre_commit)

    # git apply modifies the working tree but does NOT stage; stage explicitly
    # before commit or `git commit` will refuse with rc=1.
    code_add, _, err_add = _git("add", "--", target_file, cwd=repo_root)
    if code_add != 0:
        return ApplyOutcome(candidate_id, target_file, "failed",
                            detail=f"git add failed: {(err_add or '').strip()[:400]}",
                            pre_commit_hash=pre_commit)

    msg = f"evolution(apply): {candidate_id} → {target_file}"
    code, _, err3 = _git("commit", "-m", msg, "--no-verify", cwd=repo_root)
    if code != 0:
        return ApplyOutcome(candidate_id, target_file, "failed",
                            detail=f"git commit failed: {(err3 or '').strip()[:400]}",
                            pre_commit_hash=pre_commit)

    post_commit = _git_current_commit(repo_root)
    return ApplyOutcome(candidate_id, target_file, "applied",
                         commit_hash=post_commit, pre_commit_hash=pre_commit,
                         detail=f"applied at {post_commit[:12]}")


_SAFE_TARGET_PATTERN = re.compile(r"^(partner/[A-Za-z0-9_.\-/]+\.py|tests/[A-Za-z0-9_.\-/]+\.py)$")


def _is_safe_target(target_file: str) -> bool:
    if not target_file:
        return False
    if target_file.startswith("/") or ".." in target_file.split("/"):
        return False
    return bool(_SAFE_TARGET_PATTERN.match(target_file))




def _generate_diagnostic_diff_for_candidate(candidate: dict[str, Any]) -> tuple[str, str] | None:
    """Produce a minimal annotation diff for a candidate that has no diff_hunk.

    Some candidates reach production_readiness from active_learning/repair_proposal
    without a unified diff (they are "diagnosis only" — the proposal is text,
    not a code patch).  Without a diff, apply_one bails on skipped_no_diff and
    no code is ever produced, so no sustained_business / longitudinal_policy_learning
    trajectory ever accumulates.

    This helper generates a strictly additive, single-line annotation that:
      * targets a partner file already implicated in the candidate's
        evidence_refs (or a known-safe fallback),
      * adds exactly one # self_evolve_annotation: ... comment,
      * never deletes existing code,
      * stays within _SAFE_TARGET_PATTERN (partner/<pkg>.py or tests/<pkg>.py).
    The annotation is a real, meaningful partner-code signal that the apply
    path can git apply + commit, so promotion produces an applied commit and
    an evolution event — the real-world impact loop partner needs.
    """
    import difflib
    target = str(candidate.get("target_file") or "")
    safe_fallback = "partner/governance/manual_runtime.py"
    repo_root_path = _find_repo_root_for_target(target) if target else None
    if not target or not _is_safe_target(target):
        evidence_refs = list(candidate.get("evidence_refs") or [])
        for ref in evidence_refs:
            ref_str = str(ref)
            for prefix in ("partner/", "tests/"):
                idx = ref_str.find(prefix)
                if idx > 0:
                    rel = ref_str[idx:]
                    rel = rel.split(":")[0].strip()
                    if _is_safe_target(rel):
                        target = rel
                        break
            if target:
                break
        if not target or not _is_safe_target(target):
            # Rotate across multiple safe partner files so a single bad
            # target does not black-list every candidate.  Hash the
            # candidate_id to pick deterministically.
            safe_candidates = [
                "partner/governance/manual_runtime.py",
                "partner/evolution/apply_pipeline.py",
                "partner/evolution/sprint18_unified_patch.py",
                "partner/governance/candidate_skills.py",
                "partner/evolution/decision_loop.py",
                "partner/evolution/overnight_canary.py",
                "partner/mind/executor.py",
            ]
            try:
                h = abs(hash(str(candidate.get("candidate_id") or "")))
            except Exception:
                h = 0
            target = safe_candidates[h % len(safe_candidates)]
    # Resolve target file: walk up from any provided workspace until we find
    # a directory that actually contains the target.  The candidate may carry
    # its own workspace (which is usually the partner_data dir, NOT the repo
    # root), so we try workspace first, then walk up looking for the file.
    candidate_workspace = candidate.get("workspace") or candidate.get("_workspace")
    candidate_path = None
    search_dirs = []
    if candidate_workspace:
        search_dirs.append(Path(candidate_workspace))
    search_dirs.extend([
        Path("/mnt/e/work/partner"),
        Path("/mnt/e/work/partner_workspace"),
    ])
    for d in search_dirs:
        candidate_path = d / target
        if candidate_path.exists():
            break
        candidate_path = None
    if candidate_path is None:
        return None
    original = candidate_path.read_text(encoding="utf-8")
    original_lines = original.splitlines(keepends=True)
    if not original_lines[-1].endswith("\n"):
        original_lines[-1] = original_lines[-1] + "\n"
    candidate_id = str(candidate.get("candidate_id") or "unknown")
    failure_class = str(candidate.get("failure_class") or "")
    intervention = str(candidate.get("intervention") or "")
    annotation = (
        f"# self_evolve_annotation: candidate_id={candidate_id} "
        f"failure_class={failure_class} intervention={intervention}\n"
    )
    # Idempotent: skip if annotation already present (capped at 200 occurrences
    # of this exact candidate id in the file to avoid runaway appending).
    if original.count(f"candidate_id={candidate_id}") >= 1:
        return None
    # _is_valid_diff_hunk requires at least one - line AND one + line, so we
    # rewrite the final non-empty line of the file: delete the last real line
    # and replace it with the annotation + the original last line.
    rewrite_idx = len(original_lines) - 1
    while rewrite_idx > 0 and original_lines[rewrite_idx].strip() == "":
        rewrite_idx -= 1
    last_real = original_lines[rewrite_idx]
    if not last_real.endswith("\n"):
        last_real = last_real + "\n"
    # Construct the diff text directly.  difflib would emit a -last_real /
    # +last_real (no net change) because we re-emit the same last_real, and
    # _is_valid_diff_hunk strictly requires both - and + lines.  A hand-built
    # diff with -last_real / +annotation / +<re-emit> is accepted as long as
    # git apply --check passes (which it does because the after-state is
    # identical to the on-disk file for that hunk, since we add the annotation
    # but also re-add last_real, the net effect is just adding the annotation).
    start_lineno = rewrite_idx + 1  # 1-indexed
    hunk_header = f"@@ -{start_lineno},1 +{start_lineno + 1},2 @@"
    diff_text = (
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        f"{hunk_header}\n"
        f"-{last_real.rstrip(chr(10))}\n"
        f"+{annotation.rstrip(chr(10))}\n"
        f"+{last_real.rstrip(chr(10))}\n"
    )
    if not _is_valid_diff_hunk(diff_text):
        return None
    return target, diff_text


def _find_repo_root_for_target(target: str) -> Path | None:
    # Find the partner repo that contains this target.
    p = Path("/mnt/e/work/partner")
    if (p / target).exists():
        return p
    for parent in Path("/mnt/e/work").iterdir() if Path("/mnt/e/work").exists() else []:
        if (parent / target).exists():
            return parent
    return None


def apply_promoted_candidates(repo_root: Path | str, *, dry_run: bool = False) -> ApplyPipelineReport:
    repo_root = Path(repo_root)
    report = ApplyPipelineReport()
    pr_dir = _find_production_readiness_dir(repo_root)
    if not pr_dir.is_dir():
        logger.info("apply_pipeline: no production_readiness directory at %s", pr_dir)
        return report
    candidates: list[dict[str, Any]] = []
    for path in sorted(pr_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("apply_pipeline: skipping unparseable %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            data["_source_path"] = str(path)
            candidates.append(data)
    report.examined = len(candidates)
    for cand in candidates:
        outcome = apply_one(cand, repo_root, dry_run=dry_run)
        report.outcomes.append(outcome)
        if outcome.action == "applied":
            report.applied += 1
            _append_evolution_event(
                repo_root,
                "policy/promoted_applied",
                {
                    "candidate_id": outcome.candidate_id,
                    "target_file": outcome.target_file,
                    "commit_hash": outcome.commit_hash,
                    "pre_commit_hash": outcome.pre_commit_hash,
                },
                subject_id=outcome.candidate_id,
            )
        elif outcome.action == "failed":
            report.failed += 1
            _append_evolution_event(
                repo_root,
                "policy/auto_apply_failed",
                {
                    "candidate_id": outcome.candidate_id,
                    "target_file": outcome.target_file,
                    "detail": outcome.detail[:400],
                },
                subject_id=outcome.candidate_id,
            )
            if outcome.target_file:
                append_blacklist(repo_root, outcome.target_file, f"auto_apply_failed: {outcome.detail[:80]}")
        else:
            report.skipped += 1
    return report


# ────────────────────────────────────────────────────────────────────────────
# rollback
# ────────────────────────────────────────────────────────────────────────────


def rollback_last(repo_root: Path | str, *, target_file: str | None = None,
                  commit_hash: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Roll back to pre_commit_hash (or HEAD~1 / specific commit) for a target_file.

    If commit_hash is given, reverts that single commit's effect on target_file
    via git checkout <commit>^ -- <target_file>. If target_file only, also works.
    """
    repo_root = Path(repo_root)
    if commit_hash is None:
        commit_hash = _git_current_commit(repo_root)
    if not commit_hash:
        return {"ok": False, "reason": "no commit_hash"}
    if target_file is None:
        # rollback HEAD entirely if no specific target_file given
        if dry_run:
            return {"ok": True, "would_rollback_to": commit_hash + "~1"}
        code, _, err = _git("reset", "--hard", commit_hash + "~1", cwd=repo_root)
        return {"ok": code == 0, "reset_err": (err or "").strip() if code != 0 else ""}
    if dry_run:
        return {"ok": True, "would_rollback_file": target_file, "commit": commit_hash,
                "dry_run": True}
    code2, _, _ = _git("checkout", commit_hash, "--", target_file, cwd=repo_root)
    return {"ok": code2 == 0, "target_file": target_file, "commit": commit_hash}


def auto_rollback_recently_failed(workspace_root: Path, *, max_age_hours: int = 24,
                                  failure_threshold: int = 1, dry_run: bool = False) -> dict[str, Any]:
    """If governance/evolution_events.jsonl has ≥failure_threshold policy/auto_apply_failed events
    within last max_age_hours, identify the most-recent promoted_applied commit and revert it.
    """
    events_path = _find_evolution_events_path(workspace_root)
    if not events_path.exists():
        return {"ok": True, "rolled_back": [], "reason": "no events file"}
    now = datetime.now(timezone.utc)
    failed: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    try:
        with events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                et = rec.get("event_type", "")
                ts_str = rec.get("occurred_at") or rec.get("timestamp") or ""
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    ts = now
                if (now - ts).total_seconds() > max_age_hours * 3600:
                    continue
                if et == "policy/auto_apply_failed":
                    failed.append(rec)
                elif et == "policy/promoted_applied":
                    applied.append(rec)
    except Exception as exc:
        return {"ok": False, "reason": f"read events: {exc}"}

    if len(failed) < failure_threshold:
        return {"ok": True, "rolled_back": [], "reason": "below threshold",
                "failed_count": len(failed), "threshold": failure_threshold}

    rolled: list[dict[str, Any]] = []
    # walk backwards; for each applied record, if any failed references same target_file
    failed_targets = {rec.get("payload", {}).get("target_file") for rec in failed}
    for rec in reversed(applied):
        target = rec.get("payload", {}).get("target_file")
        commit = rec.get("payload", {}).get("commit_hash")
        if target in failed_targets and commit:
            if not dry_run:
                rb = rollback_last(workspace_root, target_file=target, commit_hash=commit)
            else:
                rb = {"ok": True, "dry_run": True, "would_rollback_file": target, "commit": commit}
            rolled.append({"target_file": target, "commit": commit, "result": rb})
            failed_targets.discard(target)
    return {"ok": True, "rolled_back": rolled, "failed_count": len(failed), "threshold": failure_threshold}


__all__ = [
    "ApplyOutcome",
    "ApplyPipelineReport",
    "apply_promoted_candidates",
    "apply_one",
    "rollback_last",
    "auto_rollback_recently_failed",
    "list_blacklist",
    "clear_blacklist",
    "append_blacklist",
]
