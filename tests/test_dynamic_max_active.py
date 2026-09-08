"""ADR 0062 — dynamic slot + external retrieval tests."""
import json, subprocess
from pathlib import Path
import pytest


def test_dynamic_max_active_falls_back_when_no_override(tmp_path, monkeypatch):
    """No operator override -> /proc estimation should yield a sane positive integer."""
    monkeypatch.setenv("HOME", str(tmp_path))
    import sys
    sys.path.insert(0, "/mnt/e/work/partner")
    from partner.governance.scheduler import effective_max_active, _read_host_resources
    no_override = effective_max_active(str(tmp_path))
    assert 1 <= no_override <= 5, no_override
    host = _read_host_resources()
    assert host["cpu_count"] > 0


def test_dynamic_max_active_respects_explicit_ceiling(tmp_path):
    """Operator value is a ceiling; host pressure can still reduce it."""
    import sys
    sys.path.insert(0, "/mnt/e/work/partner")
    from partner.governance.scheduler import effective_max_active
    cfg_path = tmp_path / "config/partner_config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"runtime": {
        "instance_native_max_active": 3,
    }}, indent=2), encoding="utf-8")
    explicit = effective_max_active(str(tmp_path), host={
        "cpu_count": 32, "mem_available_mb": 32768, "loadavg_5m": 0,
    })
    assert explicit == 3, f"override should cap at 3 (got {explicit})"


def test_dynamic_capacity_shrinks_under_memory_pressure(tmp_path):
    from partner.governance.scheduler import effective_max_active
    assert effective_max_active(str(tmp_path), host={
        "cpu_count": 32, "mem_available_mb": 2600, "loadavg_5m": 0,
    }) == 1


def test_dynamic_capacity_can_use_all_five_when_host_is_idle(tmp_path):
    from partner.governance.scheduler import effective_max_active
    assert effective_max_active(str(tmp_path), host={
        "cpu_count": 32, "mem_available_mb": 32768, "loadavg_5m": 0,
    }) == 5


def test_external_retrieval_cache_roundtrip(tmp_path):
    """First fetch writes, second fetch reads from cache."""
    import sys
    sys.path.insert(0, "/mnt/e/work/partner")
    from partner.governance.external_retrieval import fetch, _cache_path
    target = Path("/mnt/e/work/partner/docs/decisions/0062-dynamic-slot-and-external-retrieval.md")
    url = f"file://{target}"
    first = fetch(tmp_path, url)
    assert first["cache_hit"] is False, first
    assert "snippet" in first
    second = fetch(tmp_path, url)
    assert second["cache_hit"] is True
    cache_path = _cache_path(tmp_path, "http", url)
    assert cache_path.exists(), cache_path


def test_external_retrieval_search_returns_records(tmp_path):
    """harvest_for_topic returns records (or empty list — never raises)."""
    import sys
    sys.path.insert(0, "/mnt/e/work/partner")
    from partner.governance.external_retrieval import harvest_for_topic
    results = harvest_for_topic(tmp_path, "self-evolving agents")
    assert isinstance(results, list)
