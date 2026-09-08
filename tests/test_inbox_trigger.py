from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, "/mnt/e/work/partner")

from partner.learn.inbox_trigger import (  # noqa: E402
    _is_trigger,
    _log_path,
    _seen_path,
    start_learn_trigger_poller,
    trigger_learn_now,
)


from partner.learn.learn_from_hermes import HERMES_DEFAULT_ROOT
HERMES_ROOT = HERMES_DEFAULT_ROOT


def _make_workspace(tmp_path) -> Path:
    """Set up a workspace skeleton so register_candidate_skill can write to it."""
    (tmp_path / "instances" / "04" / "state").mkdir(parents=True)
    return tmp_path


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def test_is_trigger_recognises_marker():
    e = {"id": "x", "source": "learn_trigger", "text": "/learn_from_hermes"}
    assert _is_trigger(e) is True


def test_is_trigger_rejects_other_text():
    e = {"id": "x", "source": "learn_trigger", "text": "/learn_molecular"}
    assert _is_trigger(e) is False


def test_is_trigger_rejects_other_source():
    e = {"id": "x", "source": "qq_user", "text": "/learn_from_hermes"}
    assert _is_trigger(e) is False


def test_trigger_learn_now_writes_marker_to_inbox(tmp_path):
    ws = _make_workspace(tmp_path)
    inbox = ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"
    marker_id = trigger_learn_now(str(ws), source_root=str(HERMES_ROOT), dry_run=False)
    assert inbox.exists()
    entries = _read_jsonl(inbox)
    assert len(entries) == 1
    assert entries[0]["source"] == "learn_trigger"
    assert entries[0]["text"] == "/learn_from_hermes"
    assert entries[0]["id"] == marker_id


def test_trigger_learn_now_actually_runs_learn_and_writes_log(tmp_path):
    ws = _make_workspace(tmp_path)
    marker_id = trigger_learn_now(str(ws), source_root=str(HERMES_ROOT), dry_run=False)
    log = _log_path(str(ws))
    assert log.exists()
    rows = _read_jsonl(log)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == marker_id
    assert row["exit_code"] == 0
    assert "skills_registered" in row["stdout_tail"]
    # And the registry should contain Partner-owned adopted knowledge.
    skills_dir = ws / "share" / "mind" / "governance" / "experience_guided_policy" / "candidate_skills"
    assert skills_dir.exists()
    files = [p for p in skills_dir.glob("*.json") if p.name != "revisions.jsonl"]
    assert len(files) >= 1


def test_trigger_learn_now_dry_run_does_not_register(tmp_path):
    ws = _make_workspace(tmp_path)
    trigger_learn_now(str(ws), source_root=str(HERMES_ROOT), dry_run=True)
    skills_dir = ws / "share" / "mind" / "governance" / "experience_guided_policy" / "candidate_skills"
    # skills_dir is created only after first register, so check it doesn't exist
    assert not skills_dir.exists(), "dry_run should NOT register any skills"


def test_trigger_learn_now_is_idempotent_per_id(tmp_path):
    """Calling twice with the same workspace writes TWO markers, but each one
    runs independently.  This is by design — the user can re-trigger."""
    ws = _make_workspace(tmp_path)
    trigger_learn_now(str(ws), source_root=str(HERMES_ROOT), dry_run=False)
    trigger_learn_now(str(ws), source_root=str(HERMES_ROOT), dry_run=False)
    inbox = ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"
    entries = _read_jsonl(inbox)
    assert len(entries) == 2


def test_poller_thread_runs_in_background(tmp_path):
    """Start the poller, write a marker, wait, verify log written."""
    ws = _make_workspace(tmp_path)
    stop_event = threading.Event()
    thread = start_learn_trigger_poller(
        str(ws), inbox_path=str(ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"),
        stop_event=stop_event,
    )
    try:
        # Give the poller time to start its loop
        time.sleep(0.3)
        # Write a trigger marker to inbox
        marker = {
            "id": "bg_test_1",
            "source": "learn_trigger",
            "text": "/learn_from_hermes",
            "workspace": str(ws),
            "source_root": str(HERMES_ROOT),
            "dry_run": False,
        }
        inbox = ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"
        with inbox.open("a", encoding="utf-8") as f:
            f.write(json.dumps(marker, ensure_ascii=False) + "\n")

        # Wait up to 30 seconds for the log entry to appear
        log = _log_path(str(ws))
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if log.exists() and len(_read_jsonl(log)) >= 1:
                break
            time.sleep(0.5)
        rows = _read_jsonl(log)
        assert len(rows) >= 1, f"poller did not produce a log entry in 30s"
        # Most recent row should have exit_code == 0
        assert rows[-1]["exit_code"] == 0

        # Poller should mark the marker as seen so it does not re-run
        seen_path = _seen_path(str(ws))
        seen = json.loads(seen_path.read_text(encoding="utf-8"))
        assert "bg_test_1" in seen
    finally:
        stop_event.set()
        thread.join(timeout=5)


def test_poller_thread_skips_non_trigger_entries(tmp_path):
    """Non-trigger entries must be ignored — they belong to the normal poller."""
    ws = _make_workspace(tmp_path)
    stop_event = threading.Event()
    thread = start_learn_trigger_poller(
        str(ws), inbox_path=str(ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"),
        stop_event=stop_event,
    )
    try:
        time.sleep(0.3)
        # Write a non-trigger entry
        marker = {
            "id": "non_trigger_1",
            "source": "qq_user",  # not learn_trigger
            "text": "hello partner",
            "workspace": str(ws),
        }
        inbox = ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"
        with inbox.open("a", encoding="utf-8") as f:
            f.write(json.dumps(marker, ensure_ascii=False) + "\n")

        time.sleep(3.0)  # wait for poller to scan
        log = _log_path(str(ws))
        if log.exists():
            rows = _read_jsonl(log)
            for row in rows:
                assert row.get("id") != "non_trigger_1"
        # And the seen file should not contain non_trigger_1
        seen_path = _seen_path(str(ws))
        if seen_path.exists():
            seen = json.loads(seen_path.read_text(encoding="utf-8"))
            assert "non_trigger_1" not in seen
    finally:
        stop_event.set()
        thread.join(timeout=5)


def test_poller_thread_does_not_double_consume(tmp_path):
    """A marker written once must trigger exactly one learn run."""
    ws = _make_workspace(tmp_path)
    stop_event = threading.Event()
    thread = start_learn_trigger_poller(
        str(ws), inbox_path=str(ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"),
        stop_event=stop_event,
    )
    try:
        time.sleep(0.3)
        marker = {
            "id": "idempotent_1",
            "source": "learn_trigger",
            "text": "/learn_from_hermes",
            "workspace": str(ws),
            "source_root": str(HERMES_ROOT),
            "dry_run": True,  # fast — no register
        }
        inbox = ws / "instances" / "04" / "state" / "desktop_inbox.jsonl"
        with inbox.open("a", encoding="utf-8") as f:
            f.write(json.dumps(marker, ensure_ascii=False) + "\n")

        # Wait for first run
        log = _log_path(str(ws))
        deadline = time.time() + 20.0
        while time.time() < deadline:
            if log.exists() and len(_read_jsonl(log)) >= 1:
                break
            time.sleep(0.5)
        rows = _read_jsonl(log)
        first_count = len(rows)
        # Wait a bit more to see if any duplicates appear
        time.sleep(3.0)
        rows_after = _read_jsonl(log)
        assert len(rows_after) == first_count, (
            f"poller re-triggered the same marker: {len(rows_after)} runs vs expected {first_count}"
        )
    finally:
        stop_event.set()
        thread.join(timeout=5)
