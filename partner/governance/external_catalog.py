"""Curated, local-first catalog for Partner's external research corpus.

Indexing is deliberately separate from adoption.  A source being present on
disk is evidence that it can be inspected, not evidence that its design has
been integrated or that its code is safe to execute.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .storage import atomic_json, workspace_root


CURATED_SOURCES = (
    {
        "source_id": "polar-agentic-rl",
        "relative_path": "literature/Polar Agentic RL on Any Harness at Scale.pdf",
        "kind": "paper",
        "use_for": ["trajectory schema", "harness/evaluator separation", "asynchronous rollout"],
        "adoption": "design_reference",
    },
    {
        "source_id": "rlvr-world",
        "relative_path": "code/RLVR-World-main/README.md",
        "kind": "repository",
        "use_for": ["verifiable reward", "task-specific evaluation"],
        "adoption": "design_reference",
    },
    {
        "source_id": "sesa",
        "relative_path": "code/SESA-Self-Evolving-Search-Agents-master/README.md",
        "kind": "repository",
        "use_for": ["failure queue", "skill cards", "proposer/solver separation"],
        "adoption": "design_reference",
    },
    {
        "source_id": "jit-rl",
        "relative_path": "literature/Just-In-Time Reinforcement Learning Continual Learning in LLM Agents Without Gradient Updates.pdf",
        "kind": "paper",
        "use_for": ["online experience reuse without weight updates"],
        "adoption": "design_reference",
    },
    {
        "source_id": "deepseek-harness",
        "relative_path": "code/deepseek-harness/docs/architecture.md",
        "kind": "repository",
        "upstream": "https://github.com/deepseek-ai/deepseek-harness",
        "pinned_revision": "b150a551b8d465e31e418e1b2eaf5e79bbb7d28e",
        "license": "MIT",
        "use_for": [
            "durable session event log",
            "live versus durable event separation",
            "capability seams and reversible lifecycle",
            "tool execution pipeline",
        ],
        "adoption": "design_reference",
    },
    {
        "source_id": "openai-codex",
        "relative_path": "code/openai-codex/codex-rs/rollout-trace/README.md",
        "kind": "repository",
        "upstream": "https://github.com/openai/codex",
        "pinned_revision": "76d98a771e6cd44a79a3ab895a9f7c49d27d6deb",
        "license": "Apache-2.0",
        "use_for": [
            "raw evidence then deterministic reduction",
            "model-visible versus runtime evidence separation",
            "thread persistence boundary",
            "sandbox and command approval policy",
        ],
        "adoption": "design_reference",
    },
    {
        "source_id": "hermes-agent",
        "relative_path": "code/hermes-agent/agent/trajectory.py",
        "kind": "repository",
        "upstream": "https://github.com/NousResearch/hermes-agent",
        "checkout_remote": "https://github.com/zty522/hermes-agent",
        "pinned_revision": "9d059cfa3b05d693f9f7e1f8a486e5b29b872860",
        "license": "MIT",
        "use_for": [
            "memory prefetch and post-turn synchronization",
            "conversation search and candidate skill lifecycle",
            "observer correlation identifiers",
            "context compression with protected recent evidence",
        ],
        "adoption": "design_reference",
    },
    {
        "source_id": "openclaw",
        "relative_path": "code/openclaw/docs/agent-runtime-architecture.md",
        "kind": "repository",
        "upstream": "https://github.com/openclaw/openclaw",
        "pinned_revision": "97196164358dd9b58bd6d2207ccfcd219a2492ad",
        "license": "MIT",
        "use_for": [
            "gateway and session authority",
            "durable transcript and external harness mirroring",
            "tiered workspace memory and pre-compaction flush",
            "isolated cron retention and authority reset",
        ],
        "adoption": "design_reference",
    },
)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_external_catalog(workspace: str) -> dict[str, Any]:
    root = workspace_root(workspace)
    external = root / "external"
    records: list[dict[str, Any]] = []
    for source in CURATED_SOURCES:
        path = external / str(source["relative_path"])
        record = dict(source)
        record.update({
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": _digest(path) if path.is_file() else "",
            "execution_allowed": False,
            "integration_status": "indexed" if path.is_file() else "missing",
        })
        records.append(record)
    result = {
        "schema_version": 1,
        "external_root": str(external),
        "rule": "indexed != integrated; external code requires an explicit experiment and promotion decision",
        "sources": records,
        "summary": {
            "curated": len(records),
            "present": sum(bool(row["exists"]) for row in records),
            "integrated": 0,
        },
    }
    output = root / "share" / "mind" / "external" / "catalog.json"
    atomic_json(output, result)
    return {**result, "path": str(output)}
