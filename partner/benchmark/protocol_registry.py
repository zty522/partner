"""Protocol-id <-> filename registry for the benchmark runner.

The runner accepts ``--protocol-id PCI-H1H4-v1`` but the on-disk
filename is ``pci_h1h4_v1.json``.  This module is the single
source of truth for that mapping; the runner never guesses the
filename.

Two lookup modes are supported:

* Default registry: a hard-coded fallback so the v1 protocol works
  without configuration.
* Extension registry: any number of additional mappings registered
  at runtime via ``register()``.

Production operators are expected to extend the registry when they
add new protocol files; the fallback only covers the protocols that
ship with this repository.
"""
from __future__ import annotations

import os
from pathlib import Path


# Default registry: protocol_id -> filename.
_DEFAULT_REGISTRY: dict[str, str] = {
    "PCI-H1H4-v1": "pci_h1h4_v1.json",
}


def _load_extension_registry(repo_root: Path) -> dict[str, str]:
    """Load optional ``benchmark/protocols_registry.json`` if it does.
    Exists so operators can ship new ones without touching this file.
    """
    path = repo_root / "partner" / "benchmark" / "protocols_registry.json"
    if not path.exists():
        return {}
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}
    return {}


def resolve_path(repo_root: str | Path, protocol_id: str) -> Path:
    """Return the absolute path of the protocol JSON file.

    Raises ``FileNotFoundError`` if neither the default nor the
    extension registry contains the protocol_id.
    """
    root = Path(repo_root).expanduser().resolve()
    registry = dict(_DEFAULT_REGISTRY)
    registry.update(_load_extension_registry(root))
    if protocol_id not in registry:
        raise FileNotFoundError(
            f"protocol_id={protocol_id!r} is not registered. "
            f"Known: {sorted(registry.keys())}. "
            f"Add an entry to partner/benchmark/protocols_registry.json."
        )
    filename = registry[protocol_id]
    return root / "partner" / "research" / "example_protocols" / filename


__all__ = ["resolve_path"]
