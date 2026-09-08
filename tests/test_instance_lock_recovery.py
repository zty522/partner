import fcntl
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from partner.monitoring.instance_lock import InstanceAlreadyRunning, InstanceLock


def _hold(path: str, owner_pid: int, ready, release):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w+", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": owner_pid, "started_at": "old"}))
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        release.wait(10)


def test_live_owner_remains_fail_closed(tmp_path):
    path = tmp_path / "instance" / "state" / "instance_runtime.lock"
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    process = multiprocessing.Process(target=_hold, args=(str(path), os.getpid(), ready, release))
    process.start()
    assert ready.wait(5)
    try:
        with pytest.raises(InstanceAlreadyRunning):
            InstanceLock(str(tmp_path / "instance"), "04").acquire()
    finally:
        release.set()
        process.join(5)


def test_orphaned_drvfs_style_inode_is_recoverable(tmp_path):
    path = tmp_path / "instance" / "state" / "instance_runtime.lock"
    ready, release = multiprocessing.Event(), multiprocessing.Event()
    process = multiprocessing.Process(target=_hold, args=(str(path), 99999999, ready, release))
    process.start()
    assert ready.wait(5)
    lock = None
    try:
        lock = InstanceLock(str(tmp_path / "instance"), "04").acquire()
        assert json.loads(path.read_text())["pid"] == os.getpid()
        assert (path.parent / "instance_runtime.lock.stale.99999999").exists()
    finally:
        if lock:
            lock.release()
        release.set()
        process.join(5)
