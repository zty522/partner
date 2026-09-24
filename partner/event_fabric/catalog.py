"""Versioned Event catalog shared by planners and the Event runtime.

Running work pins ``version``.  A source edit therefore affects only a fresh
catalog snapshot after the owning instance restarts; no handler is monkey
patched halfway through a Task.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable
import json
import os


EventHandler = Callable[[Any, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class EventDefinition:
    name: str
    series: str
    description: str
    handler: EventHandler
    version: str = "1.0.0"
    source: str = "builtin"
    execution_method: str = "local"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    external_call: bool = False
    produces_artifact: bool = False
    reads_existing_artifact: bool = False
    idempotent: bool = True
    timeout_seconds: int = 300
    max_attempts: int = 1
    concurrency_scope: str = "project"
    permission_class: str = "local"
    legacy: bool = False
    granularity: str = "semantic"
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()
    evidence_contract: tuple[str, ...] = ()
    checkpoint_policy: str = "after_terminal"
    failure_classes: tuple[str, ...] = ()

    def public_record(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("handler", None)
        return value


class EventCatalog:
    """Immutable-after-freeze registry with deterministic identity."""

    def __init__(self) -> None:
        self._events: dict[str, EventDefinition] = {}
        self._frozen = False
        self._version = ""

    def register(self, definition: EventDefinition) -> None:
        if self._frozen:
            raise RuntimeError("event catalog is frozen")
        if definition.name in self._events:
            raise ValueError(f"duplicate event definition: {definition.name}")
        if not definition.name or "." not in definition.name:
            raise ValueError("canonical Event names use <series>.<action>")
        self._events[definition.name] = definition

    def register_many(self, definitions: Iterable[EventDefinition]) -> None:
        for definition in definitions:
            self.register(definition)

    def get(self, name: str) -> EventDefinition | None:
        return self._events.get(str(name or ""))

    def names(self) -> list[str]:
        return sorted(self._events)

    @property
    def version(self) -> str:
        if not self._version:
            body = json.dumps(
                [self._events[name].public_record() for name in self.names()],
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            self._version = sha256(body.encode("utf-8")).hexdigest()[:16]
        return self._version

    def freeze(self) -> "EventCatalog":
        self._frozen = True
        _ = self.version
        return self

    def snapshot(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        value = {"schema_version": 1, "catalog_version": self.version,
                 "events": [self._events[name].public_record() for name in self.names()]}
        temporary = target.with_suffix(target.suffix + f".tmp.{os.getpid()}")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target


def build_catalog(*, workspace: str | Path | None = None, **deprecated: Any) -> EventCatalog:
    from partner.events import builtin_definitions
    from .extensions import extension_definitions

    catalog = EventCatalog()
    catalog.register_many(builtin_definitions())
    catalog.register_many(extension_definitions(workspace))
    if deprecated.get("include_legacy"):
        raise ValueError("legacy Harness catalog entries were retired in Sprint 36")
    return catalog.freeze()
