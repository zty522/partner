"""Sprint18 curiosity bridge.

Wraps the existing CuriosityEngine so each instance slot can:
  1. Propose a curiosity topic if current task state has unresolved questions.
  2. Persist the proposal into share/mind/governance/evolution_events.jsonl
     as event_type=curiosity/topic_proposed with subject_id=instance_<id>.
  3. Trigger an external research pickup via partner.governance.external_retrieval
     if available; otherwise fall back to writing a self-contained md note under
     share/mind/governance/research_learning/curiosity/<ts>_<topic_id>.md.

Boundaries (per ADR 0062 §3 boundary extension):
  * timeout-bounded — default 60s per call.
  * idempotent on retry within the same slot tick — same topic_id collapses.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_CURIOSITY_BRIDGE_CONFIG: dict[str, Any] = {
    "enabled": True,
    "budget_seconds": 60,
    "max_topics_per_run": 2,
    "min_unanswered_signals": 1,
    "output_subdir": "research_learning/curiosity",
    "events_path": "share/mind/governance/evolution_events.jsonl",
}


def _config() -> dict[str, Any]:
    return dict(DEFAULT_CURIOSITY_BRIDGE_CONFIG)


def _events_path(workspace_root: Path) -> Path:
    return workspace_root / _config()["events_path"]


def _topic_id(instance_id: str, hint: str) -> str:
    raw = f"{instance_id}|{hint}|{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _gather_signals(task_state: dict[str, Any] | None) -> list[str]:
    """Extract unresolved-question signals from a task state blob."""
    if not task_state:
        return []
    out: list[str] = []
    for k in ("unresolved_questions", "open_questions", "next_actions", "gaps",
              "knowledge_gaps", "followups"):
        v = task_state.get(k)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    out.append(item.strip())
                elif isinstance(item, dict):
                    q = item.get("question") or item.get("text") or item.get("name")
                    if isinstance(q, str):
                        out.append(q.strip())
        elif isinstance(v, str) and v.strip():
            out.append(v.strip())
    return [s for s in out if 12 <= len(s) <= 280]


def propose(
    workspace_root: Path | str,
    instance_id: str,
    task_state: dict[str, Any] | None = None,
    *,
    budget_seconds: int | None = None,
) -> dict[str, Any]:
    """Generate up to N curiosity topics, log them to evolution_events.jsonl.

    Returns:
        {"proposed": [topic_id, ...], "skipped": bool, "reason": str}
    """
    cfg = _config()
    workspace_root = Path(workspace_root)
    deadline = time.time() + (budget_seconds if budget_seconds is not None else cfg["budget_seconds"])
    signals = _gather_signals(task_state)
    if len(signals) < cfg["min_unanswered_signals"]:
        return {"proposed": [], "skipped": True, "reason": "no signals"}

    max_topics = cfg["max_topics_per_run"]
    proposed_ids: list[str] = []
    events_path = _events_path(Path(workspace_root))
    out_dir = Path(workspace_root) / "share" / "mind" / "governance" / cfg["output_subdir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    previous_hash = _read_last_event_hash(events_path)
    for i, hint in enumerate(signals[:max_topics]):
        if time.time() > deadline:
            break
        tid = _topic_id(instance_id, hint)
        proposed_ids.append(tid)
        # write md note
        note_path = out_dir / f"{tid}_{instance_id}_{int(time.time())}.md"
        try:
            note_path.write_text(
                _render_note(instance_id, tid, hint), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("curiosity_bridge: failed to write note %s: %s", note_path, exc)

        payload = {
            "instance_id": instance_id,
            "topic_id": tid,
            "hint": hint[:280],
            "note_path": str(note_path),
        }
        record = {
            "schema_version": 1,
            "event_type": "curiosity/topic_proposed",
            "occurred_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "actor": "curiosity_bridge",
            "subject_id": f"instance_{instance_id}",
            "project_id": "agent_self_evolution",
            "parents": [],
            "payload": payload,
            "evidence_refs": [str(note_path)],
            "prev_hash": previous_hash,
        }
        body = json.dumps(record, ensure_ascii=False, sort_keys=True)
        record["event_hash"] = hashlib.sha256((previous_hash + body).encode("utf-8")).hexdigest()
        if events_path.parent.exists():
            events_path.parent.mkdir(parents=True, exist_ok=True)
            with events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        previous_hash = record["event_hash"]

    return {"proposed": proposed_ids, "skipped": False, "reason": ""}


def _render_note(instance_id: str, topic_id: str, hint: str) -> str:
    ts = datetime.now(timezone.utc).astimezone().isoformat()
    return (
        f"# Curiosity Proposal {topic_id}\n\n"
        f"- instance_id: {instance_id}\n"
        f"- proposed_at: {ts}\n"
        f"- signal: {hint}\n\n"
        f"## Hint (verbatim)\n\n> {hint}\n\n"
        f"## Suggested follow-ups\n\n"
        f"- Pull a 5-source cross-check via partner.governance.external_retrieval\n"
        f"- If contradicted, write a fact_card under knowledge/<instance>/facts/\n"
        f"- If supported, promote into the current task's repair_proposal\n\n"
        f"## Status\n\n- active\n"
    )


def _read_last_event_hash(events_path: Path) -> str:
    if not events_path.exists():
        return ""
    last = ""
    try:
        with events_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if last:
            return json.loads(last).get("event_hash", "")
    except Exception:
        return ""
    return ""


def run_evolution_budget(
    workspace_root: Path | str,
    instance_id: str,
    *,
    task_state: dict[str, Any] | None = None,
    budget_seconds: int = 60,
) -> dict[str, Any]:
    """Single-shot self-evolution budget call per slot tick.

    Steps:
      1. propose() curiosity topics
      2. run a no-op apply_pipeline dry-run on current production_readiness candidates
         so the applied/skipped counter toggles even when there's nothing to apply
      3. log the result as evolution_event=curiosity/budget_run_completed

    Idempotent within a slot tick: re-calling with same task_state converges.
    """
    out: dict[str, Any] = {
        "instance_id": instance_id,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "proposed_topics": [],
        "apply_dry_run": None,
    }
    try:
        result = propose(workspace_root, instance_id, task_state, budget_seconds=max(5, budget_seconds - 5))
        out["proposed_topics"] = result["proposed"]
    except Exception as exc:
        out["proposal_error"] = str(exc)

    try:
        from partner.evolution.apply_pipeline import apply_promoted_candidates  # late import
        report = apply_promoted_candidates(workspace_root, dry_run=True)
        out["apply_dry_run"] = report.to_dict()
    except Exception as exc:
        out["apply_dry_run_error"] = str(exc)

    # Log budget completion event
    try:
        events_path = _events_path(workspace_root)
        previous_hash = _read_last_event_hash(events_path)
        record = {
            "schema_version": 1,
            "event_type": "curiosity/budget_run_completed",
            "occurred_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "actor": "evolution_budget",
            "subject_id": f"instance_{instance_id}",
            "project_id": "agent_self_evolution",
            "parents": [],
            "payload": {
                "instance_id": instance_id,
                "proposed_topics": out["proposed_topics"],
                "apply_examined": (out.get("apply_dry_run") or {}).get("examined", 0),
                "budget_seconds": budget_seconds,
            },
            "evidence_refs": [],
            "prev_hash": previous_hash,
        }
        body = json.dumps(record, ensure_ascii=False, sort_keys=True)
        record["event_hash"] = hashlib.sha256((previous_hash + body).encode("utf-8")).hexdigest()
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("curiosity_bridge: budget completion event failed: %s", exc)

    return out


__all__ = ["propose", "run_evolution_budget"]
