from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.learn.learn_literature_bdk_demo import (  # noqa: E402
    load_literature_catalog,
    run_bdk_on_literature,
    main,
)


def _write_minimal_snapshot(tmp_path, sources: list[dict]) -> Path:
    snap = tmp_path / "external_catalog_snapshot.json"
    snap.write_text(json.dumps({
        "schema_version": 1,
        "sources": sources,
    }, ensure_ascii=False), encoding="utf-8")
    return snap


def test_load_literature_catalog_extracts_features(tmp_path):
    snap = _write_minimal_snapshot(tmp_path, [
        {"source_id": "paper_a", "size_bytes": 100000, "use_for": ["x", "y"],
         "adoption": "design_reference", "path": "/x", "exists": True, "sha256": "abc"},
        {"source_id": "paper_b", "size_bytes": 500000, "use_for": ["x"],
         "adoption": "active", "path": "/y", "exists": True, "sha256": "def"},
        {"source_id": "broken", "size_bytes": 0, "use_for": [],
         "adoption": "unknown", "path": "/z", "exists": True, "sha256": "0"},
    ])
    catalog = load_literature_catalog(snap)
    assert len(catalog) == 2  # 3rd has size_bytes=0, filtered out
    assert catalog[0]["n_use_for_tags"] == 2
    assert catalog[1]["n_use_for_tags"] == 1
    # log_size = log10(size_bytes)
    import math
    assert catalog[0]["log_size"] == math.log10(100000)


def test_run_bdk_on_literature_returns_metrics(tmp_path):
    sources = [
        {"source_id": f"p_{i}", "size_bytes": 100000 * (i + 1),
         "use_for": ["x"] * (i % 4 + 1), "adoption": "design_reference",
         "path": f"/p_{i}", "exists": True, "sha256": "x" * 5}
        for i in range(8)
    ]
    snap = _write_minimal_snapshot(tmp_path, sources)
    catalog = load_literature_catalog(snap)
    r = run_bdk_on_literature(catalog, mask=[True, True, True, True], epochs=20)
    assert r["ok"] is True
    assert r["n_sources"] == 8
    assert r["mean_rmse_log10"] >= 0


def test_run_bdk_on_literature_rejects_too_few_sources():
    catalog = [
        {"source_id": "a", "size_bytes": 1000, "n_use_for_tags": 1,
         "log_size": 3.0},
    ]
    r = run_bdk_on_literature(catalog, mask=[True, True, True, True])
    assert r["ok"] is False
    assert "only 1" in r["reason"]


def test_cli_writes_report_to_specified_output(tmp_path):
    snap = _write_minimal_snapshot(tmp_path, [
        {"source_id": f"p_{i}", "size_bytes": 100000 + i * 10000,
         "use_for": ["x"] * (i % 3 + 1), "adoption": "design_reference",
         "path": f"/p_{i}", "exists": True, "sha256": "y" * 5}
        for i in range(6)
    ])
    out = tmp_path / "report.json"
    import sys as _sys
    saved = _sys.argv
    _sys.argv = ["demo", "--snapshot", str(snap), "--output", str(out)]
    try:
        main()
    finally:
        _sys.argv = saved
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "results" in data
    assert "kernel_optimizer_recommendation" in data
    assert "honesty_notes" in data
    assert any("demonstration" in n.lower() for n in data["honesty_notes"])


def test_cli_handles_missing_snapshot(tmp_path, capsys):
    import sys as _sys
    saved = _sys.argv
    _sys.argv = ["demo", "--snapshot", str(tmp_path / "missing.json"),
                 "--output", str(tmp_path / "out.json")]
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    _sys.argv = saved
