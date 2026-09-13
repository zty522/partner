"""Evidence-manifested deep context for high-entropy Partner reasoning.

The pack is deliberately richer than the compact routing context, but remains
bounded, provenance-labelled and safe for the configured external model.  It
never includes credentials or configuration files and writes a manifest for
every pack so token volume can be audited against actual evidence coverage.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .models import now_iso
from .portfolio_improvement import effective_trajectories
from .storage import atomic_json, latest_receipt, load_project_state, workspace_root


_SECRET = re.compile(
    r"[\"']?(?:api[_-]?key|access[_-]?token|secret|password|authorization)[\"']?\s*[:=]",
    re.I,
)
_FORBIDDEN_PARTS = {
    ".git", "config", "credentials", "secrets", "cookies", "login data",
    "network", "session storage", "local storage", "__pycache__",
}
_LOCAL_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:mnt|home|tmp)/)[^\s\"'；;，,)}\]]+",
    re.I,
)


def _redact_paths(content: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        return "[local-evidence:" + hashlib.sha256(value.encode()).hexdigest()[:12] + "]"
    return _LOCAL_PATH.sub(replace, content)


def _read(path: Path, limit: int) -> tuple[str, str]:
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", "unreadable"
    if _SECRET.search(body[:12000]):
        return "", "possible_secret"
    return body[:limit], "included" if len(body) <= limit else "truncated"


def _safe_path(path: Path, roots: Iterable[Path]) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return False
    if any(part.lower() in _FORBIDDEN_PARTS for part in resolved.parts):
        return False
    return any(resolved == root or root in resolved.parents for root in roots)


def _section(kind: str, source_id: str, content: str, *, path: Path | None = None) -> dict[str, Any]:
    content = _redact_paths(content)
    return {
        "kind": kind,
        "source_id": source_id,
        "content": content,
        "chars": len(content),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "local_path_hash": (
            hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16] if path else ""
        ),
    }


def _derived_episode(path: Path) -> str:
    """Return diagnostic structure without conversation text or local paths."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return ""
    if not isinstance(state, dict):
        return ""
    tools = [{key: row.get(key) for key in (
        "step_id", "event_type", "status", "terminal_reason", "elapsed_sec")}
        for row in state.get("tool_calls") or [] if isinstance(row, dict)]
    failures = [{key: row.get(key) for key in (
        "class", "step_id", "event_type", "failure_owner", "mechanism", "error")}
        for row in state.get("failure_details") or [] if isinstance(row, dict)]
    trace_types: list[str] = []
    try:
        for line in (path.parent / "trace.jsonl").read_text(
                encoding="utf-8", errors="replace").splitlines():
            row = json.loads(line)
            if isinstance(row, dict) and row.get("type"):
                trace_types.append(str(row["type"]))
    except (OSError, TypeError, ValueError):
        pass
    value = {
        "episode_id_hash": hashlib.sha256(str(state.get("episode_id") or "").encode()).hexdigest()[:16],
        "task_id_hash": hashlib.sha256(str(state.get("task_id") or "").encode()).hexdigest()[:16],
        "instance_id": state.get("instance_id"), "project_id": state.get("project_id"),
        "status": state.get("status"), "failure_classes": state.get("failure_classes") or [],
        "failure_details": failures, "tool_calls": tools,
        "reward_vector": state.get("reward_vector") or {},
        "trace_event_types": trace_types,
    }
    return json.dumps(value, ensure_ascii=False, indent=2)


def _derived_source(path: Path) -> str:
    """Expose Python interface shape, never implementation bodies or literals."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(raw)
    except (OSError, SyntaxError, ValueError):
        return ""
    symbols = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append({
                "kind": "function", "name": node.name,
                "parameters": [arg.arg for arg in node.args.args],
                "line": node.lineno,
            })
        elif isinstance(node, ast.ClassDef):
            symbols.append({
                "kind": "class", "name": node.name, "line": node.lineno,
                "methods": [child.name for child in node.body
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))],
            })
    return json.dumps({
        "source_id": path.name, "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "symbols": symbols,
    }, ensure_ascii=False, indent=2)


def _derived_markdown(path: Path) -> str:
    """Expose document structure and identity, not prose paragraphs."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    headings = [line.strip()[:240] for line in raw.splitlines()
                if line.lstrip().startswith("#")][:80]
    return json.dumps({"document": path.name,
                       "sha256": hashlib.sha256(raw.encode()).hexdigest(),
                       "headings": headings}, ensure_ascii=False, indent=2)


def build_deep_context_pack(
    workspace: str | Path,
    *,
    project_id: str,
    purpose: str,
    instance_id: str = "",
    task_id: str = "",
    episode_refs: list[str] | None = None,
    relevant_code: list[str] | None = None,
    max_chars: int = 90000,
    privacy_mode: str = "redacted_full",
) -> dict[str, Any]:
    """Build and persist one bounded, source-labelled high-entropy context."""
    root = workspace_root(str(workspace)).resolve()
    repo = Path(__file__).resolve().parents[2]
    allowed_roots = [root, repo]
    sections: list[dict[str, Any]] = []
    omissions: list[dict[str, str]] = []

    state = load_project_state(str(root), project_id)
    receipt = latest_receipt(str(root), project_id)
    project_truth = {
        "project_id": project_id,
        "goal": str(getattr(state, "goal", "") or getattr(receipt, "goal", "") or ""),
        "status": str(getattr(state, "status", "") or "unknown"),
        "latest_receipt": receipt.to_dict() if receipt else {},
    }
    if privacy_mode == "derived_only":
        project_truth["latest_receipt"] = {
            key: project_truth["latest_receipt"].get(key)
            for key in ("receipt_id", "project_id", "iteration", "goal", "stop_reason")
            if key in project_truth["latest_receipt"]
        }
    sections.append(_section(
        "project_truth", f"project:{project_id}:current",
        json.dumps(project_truth, ensure_ascii=False, indent=2),
    ))

    trajectories = [row for row in effective_trajectories(root)
                    if str(row.get("project_id") or "") == project_id][-8:]
    if privacy_mode == "derived_only":
        trajectories = [{
            "trajectory_id_hash": hashlib.sha256(
                str(row.get("trajectory_id") or "").encode()).hexdigest()[:16],
            "revision": row.get("revision"), "project_id": row.get("project_id"),
            "reward": row.get("reward"),
            "action": {key: (row.get("action") or {}).get(key) for key in (
                "action_key", "event_types", "strategy_id", "selection_arm_id")},
            "outcome": {key: (row.get("outcome") or {}).get(key) for key in (
                "status", "business_progress", "learning_progress",
                "self_evolution_progress", "duplicate_outcome", "failure_owner",
                "failure_mechanism")},
        } for row in trajectories]
    sections.append(_section(
        "verified_trajectories", f"project:{project_id}:trajectories:last8",
        json.dumps(trajectories, ensure_ascii=False, indent=2)[:30000],
    ))

    cognition_path = root / "share/projects" / project_id / "cognition/events.jsonl"
    if cognition_path.is_file() and privacy_mode != "derived_only":
        lines = cognition_path.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        sections.append(_section(
            "belief_history", f"project:{project_id}:cognition:last6",
            "\n".join(lines)[:24000], path=cognition_path,
        ))

    # Stable design and acceptance standards accompany every high-entropy
    # call. They are constraints, never evidence of business success.
    for relative in (
        "docs/sprint33_三条认知黄金链与深上下文验收.md",
        "docs/architecture/project_centered_application.md",
    ):
        path = repo / relative
        if privacy_mode == "derived_only":
            body, status = _derived_markdown(path), "derived_only"
        else:
            body, status = _read(path, 14000)
        if body:
            sections.append(_section("acceptance_contract", f"repo:{relative}", body, path=path))
        else:
            omissions.append({"source": relative, "reason": status})

    for raw in episode_refs or []:
        path = Path(str(raw))
        if not _safe_path(path, allowed_roots):
            omissions.append({"source": "episode_ref", "reason": "outside_allowed_roots"})
            continue
        if privacy_mode == "derived_only":
            body, status = _derived_episode(path), "derived_only"
        else:
            body, status = _read(path, 18000)
        if body:
            sections.append(_section("episode_evidence", "episode:" + path.parent.name,
                                     body, path=path))
        else:
            omissions.append({"source": "episode:" + path.parent.name, "reason": status})
        for sibling in (() if privacy_mode == "derived_only" else (path.parent / "trace.jsonl",)):
            if sibling.is_file():
                trace, trace_status = _read(sibling, 24000)
                if trace:
                    sections.append(_section("episode_trace", "episode-trace:" + path.parent.name,
                                             trace, path=sibling))
                else:
                    omissions.append({"source": "episode-trace:" + path.parent.name,
                                      "reason": trace_status})

    for relative in relevant_code or []:
        path = (repo / relative).resolve() if not Path(relative).is_absolute() else Path(relative)
        if not _safe_path(path, [repo]):
            omissions.append({"source": str(relative), "reason": "code_outside_repo"})
            continue
        if privacy_mode == "derived_only":
            body, status = _derived_source(path), "derived_only"
        else:
            body, status = _read(path, 26000)
        if body:
            sections.append(_section("relevant_source", "repo:" + str(path.relative_to(repo)),
                                     body, path=path))
        else:
            omissions.append({"source": str(relative), "reason": status})

    selected: list[dict[str, Any]] = []
    used = 0
    for section in sections:
        remaining = max_chars - used
        if remaining <= 500:
            omissions.append({"source": section["source_id"], "reason": "pack_budget_exhausted"})
            continue
        value = dict(section)
        if value["chars"] > remaining:
            value["content"] = value["content"][:remaining]
            value["chars"] = len(value["content"])
            value["truncated_by_pack"] = True
        selected.append(value)
        used += int(value["chars"])

    identity = hashlib.sha256(json.dumps({
        "project": project_id, "purpose": purpose, "task": task_id,
        "sources": [(row["source_id"], row["sha256"]) for row in selected],
        "created_at": now_iso(),
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    manifest = {
        "schema_version": 1,
        "context_id": f"context_{identity}",
        "created_at": now_iso(),
        "project_id": project_id,
        "instance_id": instance_id,
        "task_id": task_id,
        "purpose": purpose,
        "privacy_mode": privacy_mode,
        "sections": [{key: row[key] for key in (
            "kind", "source_id", "chars", "sha256", "local_path_hash"
        )} for row in selected],
        "omissions": omissions,
        "context_chars": used,
        "estimated_prompt_tokens": (used + 3) // 4,
        "coverage": {
            "project_truth": any(row["kind"] == "project_truth" for row in selected),
            "trajectory": any(row["kind"] == "verified_trajectories" for row in selected),
            "belief_history": any(row["kind"] == "belief_history" for row in selected),
            "acceptance_contract": any(row["kind"] == "acceptance_contract" for row in selected),
            "episode_evidence": any(row["kind"] == "episode_evidence" for row in selected),
            "relevant_source": any(row["kind"] == "relevant_source" for row in selected),
        },
    }
    output = root / "share/mind/governance/context_manifests" / f"{manifest['context_id']}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, manifest)
    return {"manifest": manifest, "manifest_path": str(output), "sections": selected}


def render_deep_context(pack: dict[str, Any]) -> str:
    """Render a model payload without exposing absolute local paths."""
    manifest = dict(pack.get("manifest") or {})
    manifest.pop("manifest_path", None)
    sections = [{
        "kind": row.get("kind"), "source_id": row.get("source_id"),
        "content": row.get("content"),
    } for row in pack.get("sections") or []]
    return json.dumps({"manifest": manifest, "evidence_sections": sections},
                      ensure_ascii=False)


__all__ = ["build_deep_context_pack", "render_deep_context"]
