from __future__ import annotations

import atexit
import json
import os
from datetime import datetime


class InstanceAlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, workspace: str, instance_id: str) -> None:
        self.workspace = workspace
        self.instance_id = instance_id
        self.path = os.path.join(workspace, "state", "instance_runtime.lock")
        self._file = None
        self._locked = False

    def acquire(self) -> "InstanceLock":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._file = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                try:
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise InstanceAlreadyRunning(self._existing_owner_message()) from exc
            else:
                import fcntl

                try:
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    # DrvFS has occasionally retained an orphaned lock inode
                    # after the recorded WSL process disappeared.  Confirm
                    # the exact owner PID is absent, then replace only that
                    # stale inode and retry once.  A live or unreadable owner
                    # remains fail-closed.
                    owner_pid = self._owner_pid()
                    if owner_pid and not self._pid_exists(owner_pid):
                        self._file.close()
                        self._file = None
                        try:
                            os.replace(self.path, self.path + f".stale.{owner_pid}")
                        except OSError:
                            raise InstanceAlreadyRunning(self._existing_owner_message()) from exc
                        self._file = open(self.path, "a+", encoding="utf-8")
                        try:
                            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except OSError as retry_exc:
                            raise InstanceAlreadyRunning(self._existing_owner_message()) from retry_exc
                    else:
                        raise InstanceAlreadyRunning(self._existing_owner_message()) from exc
            self._locked = True
            self._write_owner()
            atexit.register(self.release)
            return self
        except Exception:
            if not self._locked and self._file is not None:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None
            raise

    def _owner_pid(self) -> int:
        try:
            self._file.seek(0)
            value = json.loads(self._file.read().strip())
            return int(value.get("pid") or 0)
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return 0

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except (OSError, ValueError):
            return True

    def _existing_owner_message(self) -> str:
        owner = ""
        try:
            self._file.seek(0)
            owner = self._file.read().strip()
        except Exception:
            owner = ""
        if owner:
            try:
                data = json.loads(owner)
                pid = data.get("pid")
                started = data.get("started_at")
                return f"instance {self.instance_id} is already running (pid={pid}, started_at={started})"
            except Exception:
                return f"instance {self.instance_id} is already running ({owner[:120]})"
        return f"instance {self.instance_id} is already running"

    def _write_owner(self) -> None:
        if self._file is None:
            return
        payload = {
            "pid": os.getpid(),
            "instance_id": self.instance_id,
            "workspace": self.workspace,
            "started_at": datetime.now().isoformat(),
        }
        try:
            self._file.seek(0)
            self._file.truncate()
            self._file.write(json.dumps(payload, ensure_ascii=False))
            self._file.flush()
            try:
                os.fsync(self._file.fileno())
            except Exception:
                pass
        except OSError:
            # Lock was acquired (msvcrt.locking succeeded), owner metadata
            # is best-effort only — don't crash on drvfs/9P EIO errors
            pass

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if self._locked:
                if os.name == "nt":
                    import msvcrt

                    self._file.seek(0)
                    msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._file.close()
        except Exception:
            pass
        self._file = None
        self._locked = False


def acquire_instance_lock(workspace: str, instance_id: str) -> InstanceLock:
    return InstanceLock(workspace, instance_id).acquire()
