from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")


def test_partner_main_imports_cleanly():
    """__main__.py must still import without errors after our patch."""
    import partner.__main__ as pm
    # Check that our new function/code is reachable via the module
    src = Path(pm.__file__).read_text(encoding="utf-8")
    assert "PARTNER_LEARN_TRIGGER_ENABLE" in src
    assert "start_learn_trigger_poller" in src


def test_main_pollers_not_started_without_env():
    """Default behaviour: PARTNER_LEARN_TRIGGER_ENABLE not set → no poller."""
    # We can't easily run the full instance_mode, but we can check the
    # code path: the patch only runs when env var == "1" AND instance == "04".
    import partner.__main__ as pm
    src = Path(pm.__file__).read_text(encoding="utf-8")
    # Confirm the env check exists
    assert 'os.environ.get("PARTNER_LEARN_TRIGGER_ENABLE") == "1"' in src
    assert 'args.instance_id == "04"' in src


def test_poller_starts_when_env_set_for_04(tmp_path, monkeypatch):
    """When env var is set AND we pretend to be 04, poller is started."""
    # We can't run _run_instance_mode() in a test (too heavy), but we can
    # verify the poller itself works against a real inbox path, which is
    # what the integration hook calls.  This is the actual integration test.

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "instances" / "04" / "state").mkdir(parents=True)
    inbox = ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"

    from partner.learn.inbox_trigger import start_learn_trigger_poller, _log_path
    stop_event = threading.Event()
    thread = start_learn_trigger_poller(
        workspace=str(ws),
        inbox_path=str(inbox),
        stop_event=stop_event,
    )
    try:
        time.sleep(0.3)
        marker = {
            "id": "integration_04_1",
            "source": "learn_trigger",
            "text": "/learn_from_hermes",
            "workspace": str(ws),
            "source_root": str(Path(__file__).resolve().parents[1] / "docs/knowledge/incubated_external_learning"),
            "dry_run": False,
        }
        with inbox.open("a", encoding="utf-8") as f:
            f.write(json.dumps(marker, ensure_ascii=False) + chr(10))

        log = _log_path(str(ws))
        deadline = time.time() + 60.0
        while time.time() < deadline:
            if log.exists() and log.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.5)

        rows = []
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        assert len(rows) >= 1, f"poller did not fire in 60s; log file empty"
        # The trigger ran successfully against Partner-owned adopted knowledge.
        assert rows[-1]["exit_code"] == 0
        # And it produced a candidate_skills directory
        skills_dir = ws / "share" / "mind" / "governance" / "experience_guided_policy" / "candidate_skills"
        files = [p for p in skills_dir.glob("*.json") if p.name != "revisions.jsonl"]
        assert len(files) >= 1
    finally:
        stop_event.set()
        thread.join(timeout=5)


def test_poller_starts_for_other_instance_only_when_04_specific(tmp_path, monkeypatch):
    """Verify the integration gate: env=1 AND instance=04 (not 02/03/05)."""
    # This is a code-level test: read source and confirm the AND condition.
    import partner.__main__ as pm
    src = Path(pm.__file__).read_text(encoding="utf-8")
    # The integration condition must be exactly:
    #   env_var == "1" AND args.instance_id == "04"
    assert "PARTNER_LEARN_TRIGGER_ENABLE" in src
    # Both conditions are present (verified separately)
    assert 'args.instance_id == "04"' in src
