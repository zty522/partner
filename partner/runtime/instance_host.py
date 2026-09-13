"""One process host: pure QQ transport, no EventWorker.

ADR 0100 / migration_plan_0100 Phase 6 Step 4.

An instance is now a pure message transport:
- starts the QQ bridge
- receives QQ messages and calls PartnerApplicationService.submit()
  (which does the LLM sync + writes a JobRecord to the global queue)
- immediately returns the Submission to the user

It does NOT start an EventWorker and does NOT run any EventFlowRunner
flow.  Actual job execution is done by an independent shared worker
process (see partner.runtime.shared_worker).
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _qq_config(instance_id: str, workspace: Path) -> Path | None:
    try:
        from partner.monitoring.instance_root import resolve_global_config_path, resolve_partner_root
        value = json.loads(resolve_global_config_path().read_text(encoding="utf-8"))
        relative = ((value.get("instances") or {}).get(instance_id) or {}).get("qq_config")
        if relative:
            candidate = Path(resolve_partner_root()) / str(relative)
            if candidate.is_file():
                return candidate
    except (OSError, TypeError, ValueError):
        pass
    for candidate in (workspace / "qq_config.json", workspace / "state/qq_config.json"):
        if candidate.is_file():
            return candidate
    return None


class InstanceHost:
    """Pure QQ transport host.  No EventWorker, no flow execution."""

    def __init__(self, workspace: str, instance_id: str):
        self.workspace = Path(workspace).resolve()
        self.instance_id = instance_id
        self.bridge = None

    def run(self) -> None:
        config = _qq_config(self.instance_id, self.workspace)
        disabled = os.environ.get("PARTNER_DISABLE_QQ", "").lower() in {"1", "true", "yes", "on"}
        if config is None or disabled:
            # No QQ config (e.g. unit-test/headless mode): the host has
            # nothing to do — it should not spin up an EventWorker.
            return
        from shells.frontend.qq_bot.qq_official_bridge import create_bridge
        self.bridge = create_bridge(str(self.workspace), config_path=str(config))
        try:
            self.bridge.start()
        finally:
            # Bridge is blocking; when it returns the process exits.
            pass


def run_instance_host(workspace: str, instance_id: str) -> None:
    InstanceHost(workspace, instance_id).run()


__all__ = ["InstanceHost", "run_instance_host"]
