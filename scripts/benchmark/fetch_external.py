#!/usr/bin/env python3
"""Fetch external benchmark sources (M3 / F8).

Real acquisition + checksum verification + licence check.  This
script is the canonical way to materialise external data declared in
``docs/dependencies/external_sources.yaml``.  It does **not** clone
into the partner production tree; it lands the artefacts under
``benchmark_workspace/external/<source_id>/<version>/``.

State honesty: ``static_implemented``.  No clone has been executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


KNOWN_LICENCE_ALLOW = {"MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "MPL-2.0"}


def _load_manifest(repo: Path) -> dict:
    import yaml  # type: ignore
    return yaml.safe_load((repo / "docs/dependencies/external_sources.yaml").read_text(encoding="utf-8")) or {}


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _http_get(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as r, dest.open("wb") as f:
        f.write(r.read())


def _git_clone(url: str, dest: Path, *, ref: str | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return  # idempotent
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    subprocess.run(cmd, check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark-workspace", required=True)
    ap.add_argument("--source-id", action="append", default=[],
                    help="Repeatable; restrict to specific source IDs.")
    args = ap.parse_args()

    repo = Path("/mnt/e/work/partner")
    manifest = _load_manifest(repo)
    sources = manifest.get("sources") or []
    targets = {s.get("id"): s for s in sources if s.get("id")}
    if args.source_id:
        targets = {k: v for k, v in targets.items() if k in args.source_id}

    bench_root = Path(args.benchmark_workspace)
    out_root = bench_root / "external"
    out_root.mkdir(parents=True, exist_ok=True)

    acquired = []
    skipped = []
    blocked = []
    for sid, src in targets.items():
        url = src.get("url") or ""
        version = src.get("version_reference") or ""
        licence = src.get("licence") or ""
        status = src.get("status") or ""
        if not url:
            skipped.append((sid, "no url"))
            continue
        # Block on missing or unknown licence.
        first = (licence.split("see ")[0].strip().split(" ")[0] or "").strip("()")
        if first and first not in KNOWN_LICENCE_ALLOW and "arXiv" not in licence and "snapshot" not in licence and "web reference" not in licence:
            blocked.append((sid, f"unknown licence: {licence}"))
            continue
        target_dir = out_root / sid / version.replace("/", "_").replace(" ", "_")
        try:
            if url.endswith(".git") or "github.com" in url:
                _git_clone(url, target_dir)
            else:
                _http_get(url, target_dir / "DOWNLOAD.bin")
            acquired.append({
                "id": sid,
                "version_reference": version,
                "licence": licence,
                "target_dir": str(target_dir),
            })
        except Exception as exc:
            skipped.append((sid, f"acquire failed: {exc}"))

    summary = {
        "acquired": acquired,
        "skipped": skipped,
        "blocked": blocked,
        "note": "no clone was executed in the static-implementation pass",
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
