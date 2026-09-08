"""Bug #56 layer 3 fix regression — runtime daemon spawns instance subprocesses.

Architecture change (ADR 0064, 2026-09-05): the runtime daemon no longer
plays the role of an external business-progress watcher. Each instance is
now a real subprocess driven by its own inbox poller + event loop + QQ
bridge. The daemon only spawns, supervises (detect exits, respawn with
rate-limit backoff), and forwards shutdown signals.

Real failure that motivated this: 2026-09-05 the runtime daemon stayed
"alive" (pid 455 ran for 3h17min) but never spawned any instance
subprocess. As a result, the desktop inbox poller inside each instance
never started, the self-drive daemons wrote 5 inbox rows per tick into
the void, and the user saw no QQ-side progress at all. Heartbeat for
all 5 instances stayed frozen at 20:08 for hours while self-drive
silently filled the inbox with unread messages.
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path


def _load_runtime():
    path = "/mnt/e/work/partner/scripts/run_instance_native_runtime.py"
    spec = importlib.util.spec_from_file_location("runtime_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSpawnInstance(unittest.TestCase):
    """Verify _spawn_instance launches a real subprocess and writes the pid file."""

    def setUp(self):
        self.mod = _load_runtime()
        self.tmp = Path(tempfile.mkdtemp(prefix="runtime_spawn_test_"))
        # Build minimal instance layout: instances/0X/ directory
        for i in ["01", "02", "03"]:
            (self.tmp / "instances" / i).mkdir(parents=True, exist_ok=True)

    def _spawn_sleeping(self, instance_id: str):
        """Spawn a long-sleeping python so we can verify the subprocess is alive
        without depending on the real `python -m partner` entry point.
        Uses start_new_session=True to match the real _spawn_instance, so
        shutdown_children's os.killpg only targets the child pgid.
        """
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=str(self.tmp),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Write pid file the same way _spawn_instance does
        (self.tmp / "instances" / instance_id / "instance.pid").write_text(
            str(proc.pid), encoding="utf-8")
        return proc

    def test_is_alive_true_for_running_proc(self):
        proc = self._spawn_sleeping("01")
        try:
            assert self.mod._is_alive(proc) is True
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_is_alive_false_after_exit(self):
        proc = self._spawn_sleeping("02")
        proc.terminate()
        proc.wait(timeout=5)
        assert self.mod._is_alive(proc) is False

    def test_shutdown_children_terminates_running_procs(self):
        """Verify _shutdown_children issues SIGTERM to each child pgid and
        waits for them to exit. We mock Popen via subprocess so we don't
        rely on real sleep(60) child lifecycles that pytest cleanup races
        with."""
        import subprocess as _sp
        real_popen = _sp.Popen
        procs_created = []

        def _spy_popen(*args, **kwargs):
            # Use a sleep that responds to SIGTERM quickly so the test
            # can observe the graceful path. start_new_session=True matches
            # the real _spawn_instance contract.
            kwargs.setdefault("start_new_session", True)
            proc = real_popen([sys.executable, "-c",
                               "import signal, time; signal.signal(signal.SIGTERM, lambda *a: exit(0)); time.sleep(30)"],
                              **{k: v for k, v in kwargs.items() if k != "start_new_session"},
                              start_new_session=True)
            procs_created.append(proc)
            return proc

        with unittest.mock.patch.object(self.mod.subprocess, "Popen", side_effect=_spy_popen):
            # Spawn two children via the real _spawn_instance entry point
            self.mod._spawn_instance(self.tmp, "01", sys.executable)
            self.mod._spawn_instance(self.tmp, "03", sys.executable)
            # Children dict mirrors what main() builds
            children = {"01": procs_created[0], "03": procs_created[1]}
            try:
                t0 = time.time()
                self.mod._shutdown_children(children)
                elapsed = time.time() - t0
                assert procs_created[0].poll() is not None, "01 must have exited after shutdown"
                assert procs_created[1].poll() is not None, "03 must have exited after shutdown"
                # Grace path: SIGTERM + handler exits cleanly, no SIGKILL needed.
                # Should not be instant (giving children time to react) but
                # also not hit the 10s grace period.
                assert 0.0 < elapsed < 9.0, (
                    f"shutdown took {elapsed}s; expected fast graceful exit"
                )
            finally:
                for p in procs_created:
                    if p.poll() is None:
                        p.kill()
                        p.wait()


class TestReconcileSlotsPreserved(unittest.TestCase):
    """The arbiter behaviour must remain identical after the spawn refactor.

    Pin the contract: reconcile_slots returns selected instances, mutates
    instance_scheduler.json, and does NOT spawn processes.
    """

    def setUp(self):
        self.mod = _load_runtime()
        self.tmp = Path(tempfile.mkdtemp(prefix="runtime_reconcile_"))
        # Minimal scheduler state
        (self.tmp / "state").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "instance_scheduler.json").write_text(json.dumps({
            "version": 1, "max_active": 5, "active_slots": [],
            "paused_instances": [], "roles": {},
            "reason": "test", "previous_active_slots": [], "updated_at": "",
        }), encoding="utf-8")
        # All 5 instance dirs
        for i in ["01", "02", "03", "04", "05"]:
            (self.tmp / "instances" / i / "state").mkdir(parents=True, exist_ok=True)
            (self.tmp / "instances" / i / "state" / "native_state.json").write_text(
                json.dumps({"phase": "WAIT_TASK", "reason": "", "pending_message_id": "",
                            "pending_kind": ""}), encoding="utf-8")

    def test_reconcile_enforces_two_slots_when_empty_start(self):
        # A caller cannot widen the repository-wide concurrency contract.
        result = self.mod._reconcile_slots(self.tmp, ["01", "02", "03", "04", "05"], 2)
        assert result == ["01", "02"]

    def test_reconcile_does_not_spawn_processes(self):
        """Source-level guard: _reconcile_slots must NOT contain subprocess.Popen.
        Spawning is _spawn_instance's job. Without this, the runtime would
        accidentally spawn twice (once via spawn_instance, once via reconcile)."""
        import inspect
        src = inspect.getsource(self.mod._reconcile_slots)
        assert "subprocess" not in src and "Popen" not in src, (
            "_reconcile_slots must not spawn processes; that's _spawn_instance's job"
        )

    def test_resource_shrink_drains_busy_lanes_without_preemption(self):
        # Put five lanes in a busy phase and make them the current selection.
        scheduler = self.tmp / "state/instance_scheduler.json"
        value = json.loads(scheduler.read_text(encoding="utf-8"))
        value["active_slots"] = ["01", "02", "03", "04", "05"]
        scheduler.write_text(json.dumps(value), encoding="utf-8")
        for i in ["01", "02", "03", "04", "05"]:
            path = self.tmp / f"instances/{i}/state/native_state.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state["phase"] = "PROJECT_DISPATCHED"
            path.write_text(json.dumps(state), encoding="utf-8")
            legacy = self.tmp / f"state/instance_native/instance_{i}.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(json.dumps({"instance_id": i,
                                          "phase": "PROJECT_DISPATCHED"}),
                              encoding="utf-8")
        assert self.mod._reconcile_slots(
            self.tmp, ["01", "02", "03", "04", "05"], 4,
        ) == ["01", "02", "03", "04", "05"]

    def test_supervision_tick_uses_one_slot_snapshot(self):
        """Initial selection plus exactly one reconciliation inside the loop.

        A second call in the same tick can select another pair after shutdown
        mutates phases and briefly run three instance processes.
        """
        import inspect
        src = inspect.getsource(self.mod.main)
        assert src.count("_reconcile_slots(") == 2


class TestBackCompatExports(unittest.TestCase):
    """The runtime module must still export the helpers that the old main
    and any external callers / tests reference."""

    def test_exports_preserved(self):
        mod = _load_runtime()
        for name in ("handle_terminal", "recover_or_start", "TaskTerminalReceiver",
                      "_spawn_instance", "_reconcile_slots", "_shutdown_children",
                      "_is_alive"):
            assert hasattr(mod, name), f"runtime module must export {name}"


class TestExplicitInboxPrecedence(unittest.TestCase):
    def test_unseen_message_precedes_auto_seed(self):
        mod = _load_runtime()
        root = Path(tempfile.mkdtemp(prefix="runtime_inbox_precedence_"))
        state = root / "instances/05/state"
        state.mkdir(parents=True)
        (state / "desktop_inbox_seen_ids.json").write_text(
            json.dumps(["old"]), encoding="utf-8")
        (state / "desktop_inbox.jsonl").write_text(
            json.dumps({"message_id": "old"}) + "\n" +
            json.dumps({"message_id": "operator_acceptance"}) + "\n",
            encoding="utf-8")
        assert mod._unseen_inbox_exists(root, "05") is True
        (state / "desktop_inbox_seen_ids.json").write_text(
            json.dumps(["old", "operator_acceptance"]), encoding="utf-8")
        assert mod._unseen_inbox_exists(root, "05") is False


class TestShutdownChildrenIdempotent(unittest.TestCase):
    """_shutdown_children must tolerate already-dead children (no ProcessLookupError)."""

    def test_shutdown_swallows_dead_children(self):
        mod = _load_runtime()
        # Build fake Popen-like objects that look dead
        class _Dead:
            pid = 99999  # unlikely to exist
            def poll(self): return 0  # already exited
        # Must not raise
        mod._shutdown_children({"01": _Dead(), "02": _Dead()})


if __name__ == "__main__":
    unittest.main()
