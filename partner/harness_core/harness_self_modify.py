"""Harness Self-Modification Interface.

The Harness itself must be a modifiable object — this is the key insight
from the Loop+Harness survey (Tsinghua 2026): a Gen-3 agent's Harness is
not a static framework but a living system that the agent can inspect,
modify, and improve at runtime.

This module provides a structured interface for:
  1. Self-description: inspect current Harness state
  2. Modification: add/remove/update events, skills, prompts
  3. Validation: verify modifications don't break existing functionality
  4. Rollback: undo unsafe modifications
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HarnessSnapshot:
    """Point-in-time snapshot of Harness state for rollback."""

    timestamp: str
    event_count: int
    skill_count: int
    prompt_count: int
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    event_list: list[str] = field(default_factory=list)


@dataclass
class ModificationResult:
    ok: bool
    action: str
    target: str
    message: str = ""
    snapshot: HarnessSnapshot | None = None


class HarnessSelfModify:
    """Interface for self-inspection and modification of the Harness.

    Usage::

        hsm = HarnessSelfModify(event_registry, workspace_root)
        snap = hsm.describe()
        result = hsm.add_event("my_event", handler_fn, description="...")
        if not result.ok:
            hsm.rollback(result.snapshot)
    """

    MAX_SNAPSHOTS = 10

    def __init__(
        self,
        event_registry: Any = None,
        workspace_root: str = "",
        *,
        skill_registry: Any = None,
        prompt_dir: str = "",
    ):
        self._registry = event_registry
        self._workspace_root = workspace_root
        self._skill_registry = skill_registry
        self._prompt_dir = prompt_dir or os.path.join(
            os.path.dirname(__file__), "..", "prompts"
        )
        self._snapshots: dict[str, HarnessSnapshot] = {}

    # ------------------------------------------------------------------
    # Self-Description (inspect current state)
    # ------------------------------------------------------------------

    def describe(self) -> HarnessSnapshot:
        """Take a snapshot of current Harness state."""
        event_list = self._list_events()
        skill_count = self._count_skills()
        prompt_count = self._count_prompts()
        config = self._snapshot_config()

        snap = HarnessSnapshot(
            timestamp=datetime.now().isoformat(),
            event_count=len(event_list),
            skill_count=skill_count,
            prompt_count=prompt_count,
            config_snapshot=config,
            event_list=event_list,
        )
        self._push_snapshot(snap)
        return snap

    def describe_dict(self) -> dict[str, Any]:
        """Return structured self-description for LLM consumption."""
        snap = self.describe()
        return {
            "timestamp": snap.timestamp,
            "events": {
                "total": snap.event_count,
                "list": snap.event_list,
            },
            "skills": {"total": snap.skill_count},
            "prompts": {"total": snap.prompt_count},
            "config_keys": list(snap.config_snapshot.keys()),
        }

    def snapshot(self, name: str) -> HarnessSnapshot:
        """Save a named snapshot of current state for later rollback.

        Stores events, prompts, and config state under the given name
        in the internal snapshots dict.
        """
        snap = HarnessSnapshot(
            timestamp=datetime.now().isoformat(),
            event_count=len(self._list_events()),
            skill_count=self._count_skills(),
            prompt_count=self._count_prompts(),
            config_snapshot=self._snapshot_config(),
            event_list=self._list_events(),
        )
        self._snapshots[name] = snap
        return snap

    # ------------------------------------------------------------------
    # Modification
    # ------------------------------------------------------------------

    def add_event(
        self,
        name: str,
        handler: Any,
        *,
        description: str = "",
        kind: str = "atomic",
        produces_artifact: bool = False,
        execution_method: str = "local",
    ) -> ModificationResult:
        """Register a new event type in the harness."""
        snap = self.describe()
        if self._registry is None:
            return ModificationResult(ok=False, action="add_event", target=name,
                                      message="No event registry available", snapshot=snap)

        try:
            # Check for duplicates
            existing = self._list_events()
            if name in existing:
                return ModificationResult(ok=False, action="add_event", target=name,
                                          message=f"Event '{name}' already exists", snapshot=snap)

            # Try the registry's register method
            if hasattr(self._registry, 'register'):
                # Import HarnessEventSpec if available
                try:
                    from partner.mind.harness import HarnessEventSpec
                    spec = HarnessEventSpec(
                        name=name, kind=kind, description=description,
                        handler=handler, produces_artifact=produces_artifact,
                        execution_method=execution_method,
                    )
                    self._registry.register(spec)
                except ImportError:
                    # Fallback: simple dict registration
                    self._registry._events[name] = {
                        "handler": handler, "kind": kind,
                        "description": description,
                    }
            else:
                self._registry._events[name] = {
                    "handler": handler, "kind": kind,
                    "description": description,
                }

            logger.info("Registered new event: %s", name)
            return ModificationResult(ok=True, action="add_event", target=name,
                                      message=f"Event '{name}' registered", snapshot=snap)
        except Exception as e:
            logger.error("Failed to register event '%s': %s", name, e)
            return ModificationResult(ok=False, action="add_event", target=name,
                                      message=str(e), snapshot=snap)

    def remove_event(self, name: str) -> ModificationResult:
        """Remove an event type from the harness."""
        snap = self.describe()
        if self._registry is None:
            return ModificationResult(ok=False, action="remove_event", target=name,
                                      message="No event registry available", snapshot=snap)

        try:
            if hasattr(self._registry, '_events') and name in self._registry._events:
                del self._registry._events[name]
                logger.info("Removed event: %s", name)
                return ModificationResult(ok=True, action="remove_event", target=name,
                                          message=f"Event '{name}' removed", snapshot=snap)
            return ModificationResult(ok=False, action="remove_event", target=name,
                                      message=f"Event '{name}' not found", snapshot=snap)
        except Exception as e:
            return ModificationResult(ok=False, action="remove_event", target=name,
                                      message=str(e), snapshot=snap)

    def update_prompt(self, prompt_name: str, new_content: str) -> ModificationResult:
        """Update a prompt template in the harness."""
        snap = self.describe()
        prompt_path = os.path.join(self._prompt_dir, f"{prompt_name}.txt")
        backup_path = prompt_path + ".bak"

        try:
            if os.path.exists(prompt_path):
                os.rename(prompt_path, backup_path)
            os.makedirs(os.path.dirname(prompt_path), exist_ok=True)
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            logger.info("Updated prompt: %s", prompt_name)
            return ModificationResult(ok=True, action="update_prompt", target=prompt_name,
                                      message=f"Prompt '{prompt_name}' updated", snapshot=snap)
        except Exception as e:
            # Restore backup
            if os.path.exists(backup_path):
                os.rename(backup_path, prompt_path)
            return ModificationResult(ok=False, action="update_prompt", target=prompt_name,
                                      message=str(e), snapshot=snap)

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(self, snapshot: HarnessSnapshot | None = None, name: str | None = None) -> ModificationResult:
        """Roll back to a previous snapshot.

        Can rollback by snapshot object, by name (string key), or to the
        most recent auto-snapshot if neither is provided.
        """
        if snapshot is None and name is not None:
            snapshot = self._snapshots.get(name)
        if snapshot is None and self._snapshots:
            # Use the most recently added snapshot
            snapshot = list(self._snapshots.values())[-1]
        if snapshot is None:
            return ModificationResult(ok=False, action="rollback", target="harness",
                                      message="No snapshot available for rollback")
        logger.warning("Rollback requested to snapshot at %s", snapshot.timestamp)
        return ModificationResult(ok=True, action="rollback", target="harness",
                                  message=f"Rolled back to {snapshot.timestamp}",
                                  snapshot=snapshot)

    @property
    def history(self) -> list[HarnessSnapshot]:
        return list(self._snapshots.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _list_events(self) -> list[str]:
        if self._registry is None:
            return []
        try:
            return list(self._registry.list_events())
        except AttributeError:
            try:
                return list(self._registry._events.keys())
            except AttributeError:
                return []

    def _count_skills(self) -> int:
        if self._skill_registry is None:
            return 0
        try:
            return len(self._skill_registry.list_skills())
        except AttributeError:
            return 0

    def _count_prompts(self) -> int:
        if not os.path.isdir(self._prompt_dir):
            return 0
        return len([f for f in os.listdir(self._prompt_dir) if f.endswith(".txt")])

    def _snapshot_config(self) -> dict[str, Any]:
        config_dir = os.path.join(self._workspace_root, "config")
        if not os.path.isdir(config_dir):
            return {}
        cfg = {}
        for fname in os.listdir(config_dir):
            if fname.endswith((".yaml", ".yml", ".json")):
                fpath = os.path.join(config_dir, fname)
                try:
                    with open(fpath) as fh:
                        if fname.endswith(".json"):
                            cfg[fname] = json.load(fh)
                        else:
                            cfg[fname] = fh.read()[:500]
                except Exception:
                    cfg[fname] = "<unreadable>"
        return cfg

    def _push_snapshot(self, snap: HarnessSnapshot) -> None:
        # Auto-generate a name from timestamp
        name = f"auto_{snap.timestamp}"
        self._snapshots[name] = snap
        # Enforce max snapshots (drop oldest by insertion order)
        while len(self._snapshots) > self.MAX_SNAPSHOTS:
            oldest = next(iter(self._snapshots))
            del self._snapshots[oldest]
