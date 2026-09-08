# partner03 native-progress: doc marker 2026-09-06
"""Sprint18 §6 manual-runtime delivery-failure helpers."""
from __future__ import annotations

import json
# partner03 one-line real change 2026-09-06T06:41:34
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def record_delivery_failure(workspace: str, task_id: str, *, iid: str = "",
                            project_id: str = "", artifacts: list[str] | None = None,
                            actions: list[str] | None = None) -> dict[str, Any]:
    """Persist a delivery-only-failure record so audit trail can still see what ran.

    The artifacts are real, but the QQ/email channel did not acknowledge them.
    We record a separate ledger entry that downstream tooling can grep for and
    that does NOT create an Issue (which would otherwise block ProjectState).
    """
    base = Path(workspace) / "share" / "mind" / "governance" / "delivery_failures"
    base.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "task_id": task_id, "instance_id": iid, "project_id": project_id,
        "artifacts": list(artifacts or []),
        "actions": list(actions or []),
    }
    path = base / f"{task_id}.json"
    try:
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(path)}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}  # partner03 framework tweak

# touched by 03 auto-rerun @ 2026-09-06
