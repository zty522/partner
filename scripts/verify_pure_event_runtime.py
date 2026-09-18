#!/usr/bin/env python3
"""Fail when the supported production graph can import retired execution code."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = (
    "partner.__main__", "partner.runtime.instance_host", "partner.runtime.event_worker",
    "partner.application.service", "partner.event_fabric.catalog",
    "shells.frontend.qq_bot.qq_official_bridge",  # 2026-09-17: tui+desktop_gui retired

)
FORBIDDEN = (
    "partner.mind.harness", "partner.mind.executor", "partner.harness_core",
    "partner.v2", "partner.planner.batch_planner", "partner.planner.prompt_builder",
)


def source(module: str) -> Path | None:
    path = ROOT.joinpath(*module.split("."))
    if path.with_suffix(".py").is_file(): return path.with_suffix(".py")
    if (path / "__init__.py").is_file(): return path / "__init__.py"
    return None


def imports(module: str, path: Path) -> set[str]:
    result = set(); tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module.split(".")[:-1] if path.name != "__init__.py" else module.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = list(package)
            if node.level: base = base[:max(0, len(base) - node.level + 1)]
            prefix = ".".join([*base, node.module] if node.module else base)
            if prefix: result.add(prefix)
    return result


def main() -> int:
    queue = list(ENTRY); seen = set(); violations = []
    while queue:
        module = queue.pop()
        if module in seen: continue
        seen.add(module); path = source(module)
        if path is None: continue
        for dependency in imports(module, path):
            if dependency.startswith(FORBIDDEN): violations.append((module, dependency))
            if dependency.startswith(("partner.", "shells.")): queue.append(dependency)
    old_sources = [name for name in FORBIDDEN if source(name) is not None]
    if violations or old_sources:
        for owner, dependency in violations: print(f"forbidden import: {owner} -> {dependency}")
        for name in old_sources: print(f"retired source still exists: {name}")
        return 1
    print(f"pure Event production graph verified: {len(seen)} modules; no retired runtime source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
