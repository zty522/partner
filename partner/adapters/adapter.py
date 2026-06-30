"""Agent Adapter Layer - unified interface for different agent backends."""

import json
import logging
import os
import re
import shutil
import signal
import time
import base64
import subprocess
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import List, Optional


INTERNAL_PROGRESS_SENTINEL = "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__"
# Backward-compatible name used by older executor/bridge code. This is an
# internal sentinel and must never be pushed to users.
USER_FRIENDLY_PROGRESS_REPLY = INTERNAL_PROGRESS_SENTINEL
_NTFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run_subprocess_tree(run_kwargs: dict):
    """Run a subprocess and kill its whole process tree on timeout.

    Agent backends may launch tools that launch their own children.  Plain
    subprocess.run(timeout=...) only guarantees the top-level process is
    handled; leaving child commands alive can block later Partner events.
    """
    kwargs = dict(run_kwargs)
    timeout = kwargs.pop("timeout", None)
    capture_output = bool(kwargs.pop("capture_output", False))
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    if os.name == "nt":
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | _NTFLAGS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs.pop("creationflags", None)
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(**kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            except Exception:
                proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                proc.kill()
        stdout, stderr = proc.communicate()
        exc.output = stdout
        exc.stderr = stderr
        raise exc
    return subprocess.CompletedProcess(kwargs.get("args"), proc.returncode, stdout, stderr)


def _cleanup_workspace_tool_processes(workspace: str) -> list[int]:
    """Kill orphaned tool commands that Hermes/Codex launched for one workspace."""
    if not workspace or os.name == "nt":
        return []
    killed: list[int] = []
    try:
        out = subprocess.check_output(["ps", "-eo", "pid=,args="], text=True, errors="replace")
    except Exception:
        return killed
    markers = (
        "hermes-snap-",
        "pip install",
        "npm install",
        "playwright",
        "yfinance",
    )
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        args = parts[1]
        if pid == os.getpid() or workspace not in args:
            continue
        if "python3 -m partner" in args or "python -m partner" in args:
            continue
        if not any(marker in args for marker in markers):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
        except Exception:
            continue
    if killed:
        time.sleep(1)
        for pid in list(killed):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                pass
    return killed


def _refresh_runtime_cost_summary(workspace: str) -> None:
    """Best-effort refresh of user-facing runtime/cost telemetry."""
    try:
        from ..monitoring.runtime_monitor import publish_runtime_cost_summary

        publish_runtime_cost_summary(workspace)
    except Exception as exc:
        logger.debug(f"failed to refresh runtime cost summary: {exc}")


def _load_agent_config(workspace: str) -> dict:
    """Load the per-instance agent config if it exists."""
    for rel in ("00_config/partner_config.json", "partner_config.json"):
        path = os.path.join(workspace, rel)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            agent = data.get("agent") if isinstance(data, dict) else None
            if isinstance(agent, dict):
                return agent
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.debug(f"failed to load agent config {path}: {exc}")
    return {}


def _env_or_config(name: str, config: dict, key: str, default: str) -> str:
    value = os.getenv(name)
    if value is not None and value != "":
        return value
    raw = config.get(key, default)
    if isinstance(raw, (list, tuple)):
        return ",".join(str(x) for x in raw)
    return str(raw)


def _project_timeout_sec(workspace: str, default: int = 240) -> Optional[int]:
    """Soft cap for a single agent project turn.

    Partner should keep working indefinitely across turns, but one stuck
    subprocess must not block the lifeline forever.  Use 0/none/off to opt out.
    """
    config = _load_agent_config(workspace)
    value = os.getenv("PARTNER_PROJECT_AGENT_TIMEOUT_SEC")
    if value is None or value == "":
        value = config.get("project_timeout_sec", default)
    text = str(value).strip().lower()
    if text in {"0", "none", "no", "off", "false", "disabled"}:
        return None
    try:
        return max(60, int(float(text)))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "").strip()))
    except Exception:
        return default


def _env_optional_timeout(name: str, default: int | None) -> int | None:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    text = str(value).strip().lower()
    if text in {"0", "none", "no", "off", "false", "disabled", "unlimited"}:
        return None
    try:
        parsed = int(float(text))
    except Exception:
        return default
    return None if parsed <= 0 else parsed


def _agent_optional_timeout(workspace: str, env_name: str, config_key: str, default: int | None) -> int | None:
    value = os.getenv(env_name)
    if value is None or str(value).strip() == "":
        value = _load_agent_config(workspace).get(config_key, default)
    text = str(value).strip().lower()
    if text in {"0", "none", "no", "off", "false", "disabled", "unlimited"}:
        return None
    try:
        parsed = int(float(text))
    except Exception:
        return default
    return None if parsed <= 0 else parsed


def _agent_failover_config(workspace: str) -> dict:
    config = _load_agent_config(workspace).get("failover", {})
    return config if isinstance(config, dict) else {}


def _agent_failed(reply: str) -> bool:
    if not reply:
        return True
    text = str(reply).strip()
    if text == USER_FRIENDLY_PROGRESS_REPLY:
        return True
    failure_markers = (
        "backend_not_available",
        "executable not found",
        "returned no usable output",
        "timeout waiting",
    )
    return any(marker in text for marker in failure_markers)


def _env_optional_float_timeout(name: str, default: float | None) -> float | None:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    text = str(value).strip().lower()
    if text in {"0", "none", "no", "off", "false", "disabled", "unlimited"}:
        return None
    try:
        parsed = float(text)
    except Exception:
        return default
    return None if parsed <= 0 else parsed


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class ExecutionResult:
    output: str
    exit_code: int
    success: bool


class AgentAdapter(ABC):
    """Abstract base class for agent backends.
    
    Partner talks to the outside world through this interface.
    Different backends (Hermes, Claude Code, Codex) implement this.
    """
    
    @abstractmethod
    def name(self) -> str:
        """Backend name."""
        pass
    
    @abstractmethod
    def search_web(self, query: str) -> List[SearchResult]:
        """Search the web."""
        pass
    
    @abstractmethod
    def execute_task(self, prompt: str) -> str:
        """Execute a research task given a natural language prompt.
        Returns the agent's response as text."""
        pass
    
    @abstractmethod
    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        """Have a conversation (used for the check-in interface)."""
        pass

    def chat_with_images(
        self,
        message: str,
        image_paths: list[str],
        max_tokens: int = None,
        purpose: str = "vision",
    ) -> str:
        """Analyze local images when the backend supports vision.

        Default implementation returns the internal sentinel so callers can
        fall back to OCR or primary agent without exposing backend details.
        """
        return USER_FRIENDLY_PROGRESS_REPLY


def _ensure_shell_api_key(env: dict) -> None:
    """Inject DEEPSEEK_API_KEY / OPENAI_API_KEY into env from shell if missing.

    The Partner process may be started (via systemd / auto-restart) without the
    API keys that the user exports in .bashrc.  This helper tries multiple
    methods to retrieve the key so Hermes subprocesses have it even when
    os.environ does not.

    Methods tried in order:
    1. os.environ (already present — fast path)
    2. bash -lic (login shell — works in terminals)
    3. bash -c 'source ~/.bashrc && echo ...' (systemd user services)
    4. bash -c 'source ~/.profile && echo ...' (fallback)
    """
    if env.get("DEEPSEEK_API_KEY", "").strip() or env.get("OPENAI_API_KEY", "").strip():
        return  # already present
    for cmd in (
        ["bash", "-lic", 'echo "$DEEPSEEK_API_KEY"'],
        ["bash", "-c", 'source ~/.bashrc 2>/dev/null; echo "$DEEPSEEK_API_KEY"'],
        ["bash", "-c", 'source ~/.profile 2>/dev/null; echo "$DEEPSEEK_API_KEY"'],
    ):
        try:
            r = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=10,
            )
            key = r.stdout.strip() if r.returncode == 0 else ""
            if key and key.startswith("sk-"):
                env.setdefault("DEEPSEEK_API_KEY", key)
                env.setdefault("OPENAI_API_KEY", key)
                logger.debug("[HermesAdapter] injected shell API key via: %s", " ".join(cmd))
                return
        except Exception:
            continue


class HermesAdapter(AgentAdapter):
    """Adapter for Hermes Agent via cronjob/subprocess."""

    def _resolve_model(self) -> str:
        """Resolve the model name, preferring explicit config then partner config."""
        if self.model:
            return self.model
        try:
            from ..state.config import load_partner_config_data
            data = load_partner_config_data(self.workspace)
            agent = data.get("agent", {}) if isinstance(data.get("agent"), dict) else {}
            m = str(agent.get("model") or "").strip()
            if m:
                return m
        except Exception:
            pass
        return ""

    def __init__(self, workspace_path: str, model: Optional[str] = None,
                 provider: Optional[str] = None):
        self.workspace = workspace_path
        self.model = model
        self.provider = provider

    def _log_chat_attempt(self, payload: dict):
        """Persist Hermes chat attempt metadata for timeout debugging."""
        try:
            log_dir = os.path.join(self.workspace, "state", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "hermes_chat.jsonl")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"failed to write Hermes chat log: {exc}")

        self._log_agent_run(payload)

    def _log_agent_run(self, payload: dict):
        """Persist normalized per-call runtime metrics."""
        try:
            log_dir = os.path.join(self.workspace, "state", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "agent_runs.jsonl")
            row = {
                "ts": payload.get("ts") or datetime.now().isoformat(),
                "backend": "hermes",
                "purpose": payload.get("purpose", ""),
                "attempt": payload.get("attempt"),
                "status": payload.get("status") or (
                    "ok" if payload.get("returncode") == 0 else "failed"
                ),
                "elapsed_ms": payload.get("elapsed_ms"),
                "timeout_sec": payload.get("timeout_sec"),
                "returncode": payload.get("returncode"),
                "model": payload.get("model"),
                "provider": payload.get("provider"),
                "message_chars": payload.get("message_chars"),
                "prompt_tokens_est": payload.get("prompt_tokens_est"),
                "completion_tokens_est": payload.get("completion_tokens_est"),
                "total_tokens_est": payload.get("total_tokens_est"),
                "context_tokens_reported": payload.get("context_tokens_reported"),
                "session_id": payload.get("session_id"),
                "resumed_session": payload.get("resumed_session", False),
                "stdout_preview": payload.get("stdout_preview", ""),
                "stderr_preview": payload.get("stderr_preview", ""),
                "error": payload.get("error", ""),
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _refresh_runtime_cost_summary(self.workspace)
        except Exception as exc:
            logger.warning(f"failed to write agent run log: {exc}")

    def _session_dir(self) -> str:
        d = os.path.join(self.workspace, "state", "agent_sessions")
        os.makedirs(d, exist_ok=True)
        return d

    def _session_path(self, purpose: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", purpose or "chat")
        return os.path.join(self._session_dir(), f"hermes_{safe}.session")

    def _session_uses_path(self, purpose: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", purpose or "chat")
        return os.path.join(self._session_dir(), f"hermes_{safe}.uses")

    def _read_session_id(self, purpose: str) -> str:
        if not self._should_resume_session(purpose):
            return ""
        try:
            with open(self._session_path(purpose), "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _write_session_id(self, purpose: str, session_id: str):
        if not session_id or not self._should_resume_session(purpose):
            return
        try:
            with open(self._session_path(purpose), "w", encoding="utf-8") as f:
                f.write(session_id.strip() + "\n")
        except OSError as exc:
            logger.debug(f"failed to write Hermes session id: {exc}")

    def _clear_session_id(self, purpose: str):
        try:
            os.remove(self._session_path(purpose))
        except OSError:
            pass
        try:
            os.remove(self._session_uses_path(purpose))
        except OSError:
            pass

    def _bump_session_uses(self, purpose: str) -> int:
        if not self._should_resume_session(purpose):
            return 0
        path = self._session_uses_path(purpose)
        count = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                count = int((f.read() or "0").strip() or "0")
        except Exception:
            count = 0
        count += 1
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(count))
        except OSError:
            pass
        return count

    def _max_resume_uses(self) -> int:
        try:
            return max(2, int(os.getenv("PARTNER_HERMES_MAX_RESUME_USES", "12")))
        except Exception:
            return 12

    def _should_resume_session(self, purpose: str) -> bool:
        # Partner owns long-term memory. Hermes sessions are an optional
        # short-lived acceleration cache only; leaving them on indefinitely
        # makes Hermes heavier over multi-day runs.
        configured = os.getenv("PARTNER_HERMES_RESUME_PURPOSES", "")
        purposes = {p.strip() for p in configured.split(",") if p.strip()}
        if purpose not in purposes and "*" not in purposes:
            return False
        # Check staleness: don't resume sessions older than 1 hour
        session_path = self._session_path(purpose)
        try:
            age = time.time() - os.path.getmtime(session_path)
            if age > 3600:  # 1 hour
                logger.info(f"[SESSION] stale session for {purpose}, age={age:.0f}s > 1h, clearing")
                self._clear_session_id(purpose)
                return False
        except OSError:
            return False
        return True

    @staticmethod
    def _extract_session_id(text: str) -> str:
        combined = text or ""
        patterns = (
            r"(?im)^session_id:\s*([A-Za-z0-9_.:-]+)\s*$",
            r"(?im)^Session:\s*([A-Za-z0-9_.:-]+)\s*$",
            r"Resume this session with:\s*hermes\s+(?:chat\s+)?--resume\s+([A-Za-z0-9_.:-]+)",
            r"hermes\s+--resume\s+([A-Za-z0-9_.:-]+)",
        )
        for pat in patterns:
            match = re.search(pat, combined)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _extract_context_tokens(text: str) -> int:
        match = re.search(r"Context:\s*\d+\s+msgs,\s*~?([\d,]+)\s+tokens", text or "")
        if match:
            return int(match.group(1).replace(",", ""))
        return 0

    @staticmethod
    def _strip_session_noise(text: str) -> str:
        lines = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("⚠ tirith security scanner"):
                continue
            if re.match(r"(?i)^session_id:", stripped):
                continue
            if re.match(r"(?i)^Session:\s*[A-Za-z0-9_.:-]+\s*$", stripped):
                continue
            if stripped.startswith("Resume this session with:"):
                continue
            if re.search(r"\bhermes\s+(?:chat\s+)?--resume\s+", stripped):
                continue
            if re.match(r"(?i)^Context:\s*\d+\s+msgs,\s*~?[\d,]+\s+tokens", stripped):
                continue
            # Suppress Hermes internal model normalization warning
            if re.match(r"(?i)^.*⚠️?\s*Normalized model\s+.*to\s+.*for\s+.*", stripped):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        # Mixed Chinese/English approximation. Used only for local metrics when
        # provider usage data is not available from the CLI.
        return max(1, int(len(text) / 2.2))

    def _max_resume_context_tokens(self) -> int:
        try:
            return max(2000, int(os.getenv("PARTNER_HERMES_MAX_RESUME_CONTEXT_TOKENS", "12000")))
        except Exception:
            return 12000

    def _lean_mode_enabled(self) -> bool:
        return _env_flag("PARTNER_HERMES_LEAN_MODE", True)

    def _native_home_enabled(self) -> bool:
        # Hermes is already a configured local CLI agent. By default Partner
        # should call that exact installation/config instead of creating a
        # shadow HOME with stale model settings.
        return _env_flag("PARTNER_HERMES_USE_NATIVE_HOME", True)

    def _prune_hermes_runtime(self, hermes_home: str) -> None:
        """Keep Hermes as a lean execution engine.

        Partner keeps durable research memory in its own state/user/system
        files. Hermes' per-instance home is only a runtime sandbox. We prune
        sessions/checkpoints/caches so repeated background cycles do not make
        Hermes progressively heavier.
        """
        if not self._lean_mode_enabled():
            return
        now = time.time()
        stamp_path = os.path.join(hermes_home, ".partner_lean_prune")
        interval = max(60, _env_int("PARTNER_HERMES_PRUNE_INTERVAL_SEC", 900))
        try:
            last = os.path.getmtime(stamp_path) if os.path.exists(stamp_path) else 0
            if now - last < interval:
                return
        except OSError:
            pass

        keep_sessions = _env_flag("PARTNER_HERMES_KEEP_SESSIONS", False) or bool(os.getenv("PARTNER_HERMES_RESUME_PURPOSES", "").strip())
        keep_checkpoints = _env_flag("PARTNER_HERMES_KEEP_CHECKPOINTS", False)
        removable_dirs = []
        if not keep_sessions:
            removable_dirs.append("sessions")
        if not keep_checkpoints:
            removable_dirs.extend(["checkpoints", "sandboxes"])
        removable_dirs.extend(["audio_cache", "image_cache"])

        for rel in removable_dirs:
            path = os.path.join(hermes_home, rel)
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.exists(path):
                    os.remove(path)
            except OSError as exc:
                logger.debug(f"failed to prune Hermes runtime path {path}: {exc}")
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                pass

        # The state DB is allowed to exist, but not to grow without bound.
        max_db_mb = max(5, _env_int("PARTNER_HERMES_MAX_STATE_DB_MB", 64))
        if not _env_flag("PARTNER_HERMES_KEEP_STATE_DB", True):
            max_db_mb = 0
        for rel in ("state.db", "state.db-wal", "state.db-shm"):
            path = os.path.join(hermes_home, rel)
            try:
                if os.path.exists(path) and (max_db_mb == 0 or os.path.getsize(path) > max_db_mb * 1024 * 1024):
                    os.remove(path)
            except OSError as exc:
                logger.debug(f"failed to prune Hermes db {path}: {exc}")

        # Skills are not injected because we pass --ignore-rules, but Hermes can
        # still scan the directory. Optionally remove them in strict isolation.
        if _env_flag("PARTNER_HERMES_STRICT_NO_SKILLS", False):
            skills_dir = os.path.join(hermes_home, "skills")
            try:
                if os.path.isdir(skills_dir):
                    shutil.rmtree(skills_dir)
                os.makedirs(skills_dir, exist_ok=True)
            except OSError as exc:
                logger.debug(f"failed to prune Hermes skills dir: {exc}")

        try:
            with open(stamp_path, "w", encoding="utf-8") as f:
                f.write(datetime.now().isoformat() + "\n")
        except OSError:
            pass

    def _build_hermes_env(self) -> dict:
        """Build a clean subprocess environment for Hermes CLI."""
        env = os.environ.copy()
        executable = self._hermes_executable()

        # A frozen Windows build can inherit Python/PyInstaller variables from
        # Partner.exe. Hermes owns its own Python venv; leaking Python 3.14
        # paths into the child process makes provider plugins fail to import.
        for name in (
            "PYTHONHOME",
            "PYTHONPATH",
            "PYTHONEXECUTABLE",
            "__PYVENV_LAUNCHER__",
            "_PYI_APPLICATION_HOME_DIR",
            "_PYI_PARENT_PROCESS_LEVEL",
            "PYINSTALLER_RESET_ENVIRONMENT",
        ):
            env.pop(name, None)

        exe_dir = os.path.dirname(executable) if executable and os.path.isabs(executable) else ""
        venv_root = os.path.dirname(exe_dir) if os.path.basename(exe_dir).lower() in {"scripts", "bin"} else ""
        path_parts = [p for p in (env.get("PATH") or "").split(os.pathsep) if p]
        cleaned_path = []
        for part in path_parts:
            lowered = part.replace("\\", "/").lower()
            if "python314" in lowered or "/_internal" in lowered or "pyinstaller" in lowered:
                continue
            cleaned_path.append(part)
        hermes_path_prefix = []
        if exe_dir:
            hermes_path_prefix.append(exe_dir)
        if venv_root:
            hermes_path_prefix.extend(
                [
                    os.path.join(venv_root, "Library", "bin"),
                    os.path.join(venv_root, "DLLs"),
                ]
            )
        env["PATH"] = os.pathsep.join([p for p in hermes_path_prefix + cleaned_path if p])
        if self._native_home_enabled():
            try:
                from .agent_config_sync import apply_workspace_hermes_config

                ok, msg = apply_workspace_hermes_config(self.workspace)
                if not ok:
                    logger.warning("[HermesAdapter] failed to apply workspace Hermes config: %s", msg)
            except Exception as exc:
                logger.debug("[HermesAdapter] workspace Hermes config sync skipped: %s", exc)
            env.pop("HERMES_HOME", None)
            env.pop("XDG_STATE_HOME", None)
            env.pop("XDG_CACHE_HOME", None)
            env.pop("XDG_CONFIG_HOME", None)
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            logger.debug("[HermesAdapter] using native Hermes HOME/config for workspace=%s", self.workspace)
            _ensure_shell_api_key(env)
            return env

        hermes_home = os.path.join(self.workspace, "system", "hermes_home")
        hermes_logs = os.path.join(hermes_home, "logs")
        os.makedirs(hermes_logs, exist_ok=True)
        env["HOME"] = hermes_home
        env["HERMES_HOME"] = hermes_home
        env["XDG_STATE_HOME"] = os.path.join(hermes_home, ".local", "state")
        env["XDG_CACHE_HOME"] = os.path.join(hermes_home, ".cache")
        env["XDG_CONFIG_HOME"] = os.path.join(hermes_home, ".config")
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        os.makedirs(env["XDG_STATE_HOME"], exist_ok=True)
        os.makedirs(env["XDG_CACHE_HOME"], exist_ok=True)
        os.makedirs(env["XDG_CONFIG_HOME"], exist_ok=True)
        self._sync_hermes_runtime_files(hermes_home)
        self._prune_hermes_runtime(hermes_home)
        _ensure_shell_api_key(env)
        return env

    def _sync_hermes_runtime_files(self, hermes_home: str):
        """Mirror the user's Hermes config/auth into the writable instance home."""
        source_homes = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes"),
            os.path.expanduser("~/.hermes"),
        ]
        existing_sources = []
        for source_home in source_homes:
            if source_home and os.path.isdir(source_home) and os.path.abspath(source_home) != os.path.abspath(hermes_home):
                existing_sources.append(source_home)
        if not existing_sources:
            return
        for filename in ("config.yaml", "auth.json", ".env"):
            for source_home in existing_sources:
                source_path = os.path.join(source_home, filename)
                target_path = os.path.join(hermes_home, filename)
                if not os.path.exists(source_path):
                    continue
                try:
                    source_mtime = os.path.getmtime(source_path)
                    target_mtime = os.path.getmtime(target_path) if os.path.exists(target_path) else -1
                    if source_mtime <= target_mtime:
                        break
                    with open(source_path, "rb") as src, open(target_path, "wb") as dst:
                        dst.write(src.read())
                    break
                except OSError as exc:
                    logger.warning(f"failed to mirror Hermes runtime file {filename}: {exc}")

    @staticmethod
    def _candidate_executables() -> list:
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        home = os.path.expanduser("~")
        candidates = [
            os.getenv("HERMES_BIN", ""),
            os.path.join(localappdata, "hermes", "hermes-agent", "venv", "Scripts", "hermes.exe"),
            os.path.join(localappdata, "hermes", "hermes-agent", "venv", "Scripts", "hermes"),
            shutil.which("hermes") or "",
            os.path.join(home, ".local", "bin", "hermes"),
            "/usr/local/bin/hermes",
            "/home/ubuntu/.local/bin/hermes",
            "/home/os/.local/bin/hermes",
            os.path.join(home, ".hermes", "hermes-agent", "venv", "bin", "hermes"),
            os.path.join(appdata, "Python", "Python312", "Scripts", "hermes.exe"),
            os.path.join(appdata, "Python", "Python313", "Scripts", "hermes.exe"),
            os.path.join(appdata, "Python", "Python314", "Scripts", "hermes.exe"),
            os.path.join(appdata, "npm", "hermes"),
            os.path.join(appdata, "npm", "hermes.cmd"),
        ]
        return [path for path in candidates if path]

    @staticmethod
    def detect_installation() -> dict:
        executable = ""
        for path in HermesAdapter._candidate_executables():
            if os.path.exists(path):
                executable = path
                break

        config_candidates = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "config.yaml"),
            os.path.expanduser("~/.hermes/config.yaml"),
        ]
        config_path = next((path for path in config_candidates if path and os.path.exists(path)), "")

        issues = []
        if not executable:
            issues.append("未检测到 Hermes 可执行文件")
        elif not config_path:
            issues.append("检测到 Hermes，但未找到 config.yaml")

        return {
            "available": bool(executable),
            "executable": executable,
            "config_path": config_path,
            "issues": issues,
        }

    @staticmethod
    def is_available() -> bool:
        return HermesAdapter.detect_installation()["available"]

    def _hermes_executable(self) -> str:
        for path in self._candidate_executables():
            if os.path.exists(path):
                return path
        return "hermes"
    
    def name(self) -> str:
        return "hermes"
    
    def search_web(self, query: str) -> List[SearchResult]:
        # Will be implemented via hermes web_search tool
        # For MVP, delegate to execute_task
        prompt = f"Search the web for: {query}. Return top 5 results with title, URL, and snippet."
        result = self.execute_task(prompt)
        # Parse result into SearchResult list
        return [SearchResult(title="Search Result", url="", snippet=result)]
    
    def execute_task(self, prompt: str) -> str:
        """Execute a task by writing a prompt file and invoking hermes.
        
        For MVP, this writes the prompt to a file that can be picked up
        by the cron-triggered hermes session.
        """
        import subprocess
        import tempfile
        import os
        
        # Write prompt to temp file
        prompt_file = os.path.join(self.workspace, "state", "current_task.md")
        with open(prompt_file, 'w') as f:
            f.write(prompt)
        
        # For MVP, return a placeholder - in production this would invoke hermes
        return "Task queued for execution by Hermes agent."

    def _resolve_tools_for_purpose(self, purpose: str) -> str:
        """Resolve the toolset string for a given chat purpose.

        Uses environment variable overrides:
          PARTNER_HERMES_{PURPOSE}_TOOLS (e.g. PARTNER_HERMES_ACTION_TOOLS)
        Falls back to the default: "terminal,file,web"
        """
        import os
        env_key = f"PARTNER_HERMES_{purpose.upper()}_TOOLS"
        override = os.environ.get(env_key, "")
        if override:
            logger.debug("[HermesAdapter] using env override %s=%s for purpose=%s", env_key, override, purpose)
            return override
        return "terminal,file,web"

    def _add_purpose_flags(self, cmd: list[str], purpose: str) -> None:
        """Add purpose-specific CLI flags to cmd (used for Ollama fallback rebuild)."""
        if purpose == "classify":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "interaction":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "batch_plan":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "project":
            cmd.extend(["-t", self._resolve_tools_for_purpose("project"), "--ignore-rules"])
        elif purpose == "action":
            cmd.extend(["-t", self._resolve_tools_for_purpose("action"), "--ignore-rules"])
        elif purpose == "action_think":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "report":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "direct_reply":
            cmd.extend(["-t", "terminal,file,web", "--ignore-rules", "--max-turns", "2"])
            self._clear_session_id(purpose)
            import os as _os
            _os.environ.setdefault("PARTNER_HERMES_USE_NATIVE_HOME", "true")
            del _os

    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        """Chat via hermes subprocess."""
        import subprocess
        import time

        # Load timeout from external_calls.yaml config
        def _load_per_event_timeout(purpose: str) -> int | None:
            try:
                from ..harness_core.robust_executor import load_harness_config

                config = load_harness_config(self.workspace)
                external = config.get("external_calls", {})
                per_event = external.get("per_event", {})
                event_map = {
                    "action": "agent_call",
                    "classify": "classify",
                    "interaction": "interaction",
                    "batch_plan": "batch_planner",
                    "project": "agent_call",
                    "action_think": "classify",
                    "direct_reply": "direct_reply",
                    "report": "report",
                }
                config_key = event_map.get(purpose)
                if config_key and config_key in per_event:
                    raw = per_event[config_key].get("timeout")
                    if raw is not None:
                        return int(raw)
                # Fallback to global default
                global_timeout = external.get("timeout")
                if global_timeout is not None:
                    return int(global_timeout)
            except Exception:
                pass
            return None

        cmd = [self._hermes_executable(), "chat", "-q", message, "-Q"]
        session_id = self._read_session_id(purpose)
        if session_id:
            cmd.extend(["--resume", session_id])
        if self.model:
            cmd.extend(["-m", self.model])
        if self.provider:
            cmd.extend(["--provider", self.provider])
        # Ollama routing for lightweight tasks — use configured model but with no agent tools
        _ollama_env = {}
        if purpose in ("classify", "action_think", "report"):
            _ollama_env["OPENAI_BASE_URL"] = "http://localhost:11434/v1"
            _ollama_env["OPENAI_API_KEY"] = "ollama"
            # Remove any purpose-specific model override — use the adapter's configured model
            # (set by self.model / self.provider above), just with restricted tools.
            # The old hardcoded -m qwen2.5:7b --provider ollama caused "context window
            # too small (32K vs 64K minimum)" errors. Let Hermes choose the right model.
        if purpose == "classify":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "interaction":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "batch_plan":
            # Batch planner needs lightweight single-turn, no tools
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "project":
            # project 需要 terminal/file/web 工具来实际执行代码和操作文件
            cmd.extend(["-t", self._resolve_tools_for_purpose("project"), "--ignore-rules"])
        elif purpose == "action":
            # action event 也需要真实工具，但不能继承长期 project 的超长超时。
            cmd.extend(["-t", self._resolve_tools_for_purpose("action"), "--ignore-rules"])
        elif purpose == "action_think":
            # Thinking-only events should not start terminal/file/web tool chains.
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "report":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "direct_reply":
            # Fast path: two turns for: propose tool call -> execute -> respond
            cmd.extend(["-t", "terminal,file,web", "--ignore-rules", "--max-turns", "2"])
            # Clear any stale session to avoid resumption overhead
            self._clear_session_id(purpose)
            # Force native Hermes home for web search capabilities
            import os as _os
            _os.environ.setdefault("PARTNER_HERMES_USE_NATIVE_HOME", "true")
            del _os

        # Always pass model flag explicitly to avoid default model confusion
        model = self._resolve_model()
        if model:
            has_model = any(cmd[i] == "-m" for i in range(len(cmd) - 1))
            if not has_model:
                cmd.extend(["-m", model])

        timeout_sec = _load_per_event_timeout(purpose)
        max_retries = 2
        if timeout_sec is None:
            # Fallback to env vars (preserving backward compatibility)
            if purpose == "classify":
                timeout_sec = _env_optional_timeout("PARTNER_CLASSIFY_TIMEOUT_SEC", None)
                max_retries = 0
            elif purpose == "interaction":
                timeout_sec = _env_optional_timeout("PARTNER_INTERACTION_TIMEOUT_SEC", None)
                max_retries = 0
            elif purpose == "project":
                timeout_sec = _project_timeout_sec(self.workspace)
                max_retries = 0
            elif purpose == "action":
                timeout_sec = _env_optional_timeout("PARTNER_ACTION_AGENT_TIMEOUT_SEC", None)
                max_retries = 0
            elif purpose == "action_think":
                timeout_sec = _env_optional_timeout("PARTNER_ACTION_THINK_TIMEOUT_SEC", None)
                max_retries = 0
            elif purpose == "report":
                timeout_sec = _env_optional_timeout("PARTNER_REPORT_TIMEOUT_SEC", None)
                max_retries = 0
        else:
            # When config provides the timeout, no retries
            max_retries = 0

        logger.info("[HermesAdapter] chat purpose=%s timeout_sec=%s max_retries=%s", purpose, timeout_sec, max_retries)

        for attempt in range(max_retries + 1):
            started_at = time.time()
            try:
                _env = self._build_hermes_env()
                if _ollama_env:
                    _env.update(_ollama_env)
                    # Also update PATH for ollama binary
                    _ollama_bin = os.path.dirname(self._hermes_executable())
                    _env["PATH"] = _ollama_bin + os.pathsep + _env.get("PATH", "")
                run_kwargs = {
                    "args": cmd,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                    "cwd": os.makedirs(os.path.join(self.workspace, "system", "hermes_work"), exist_ok=True) or os.path.join(self.workspace, "system", "hermes_work"),
                    "env": _env,
                    "creationflags": _NTFLAGS,
                }
                if timeout_sec is not None:
                    run_kwargs["timeout"] = timeout_sec
                result = _run_subprocess_tree(run_kwargs)
                out = result.stdout.strip()
                err = (result.stderr or "").strip()
                # Filter Hermes CLI internal timeout/denial messages from stdout
                if out:
                    out = re.sub(
                        r"(?im)^.*⏳?\s*Timeout\s*[—–-]\s*denying command.*$\n?",
                        "", out
                    ).strip()
                    out = re.sub(
                        r"(?im)^.*⚠️?\s*Normalized model\s+.*to\s+.*for\s+.*$\n?",
                        "", out
                    ).strip()
                    out = re.sub(
                        r"(?im)^.*Reached maximum iterations.*$\n?",
                        "", out
                    ).strip()
                elapsed_ms = int((time.time() - started_at) * 1000)
                combined = out  # no longer merge stderr

                # DEBUG: capture stderr when subprocess fails
                if result.returncode != 0 or not out:
                    import os as _debug_os
                    _debug_path = _debug_os.path.join(self.workspace, "system", "hermes_work", ".last_crash_stderr.txt")
                    try:
                        with open(_debug_path, "w", encoding="utf-8") as _debug_f:
                            _debug_f.write(f"returncode={result.returncode}\nout_len={len(out)}\nerr_len={len(err)}\n")
                            _debug_f.write(f"cmd={' '.join(cmd)[:2000]}\n")
                            _debug_f.write(f"--- STDOUT ---\n{out[:2000]}\n--- STDERR ---\n{err[:2000]}\n")
                    except Exception:
                        pass
                    del _debug_os, _debug_path

                # Handle stderr separately — log filtered warnings, don't mix into combined
                if err:
                    filtered_err = re.sub(
                        r"(?im)^.*⚠️?\s*Normalized model\s+.*to\s+.*for\s+.*$\n?", "", err
                    ).strip()
                    if filtered_err:
                        logger.warning(
                            "[HermesAdapter] stderr from hermes subprocess: %s",
                            filtered_err[:500],
                        )
                new_session_id = self._extract_session_id(combined) or session_id
                context_tokens_reported = self._extract_context_tokens(combined)
                if result.returncode == 0 and new_session_id:
                    self._write_session_id(purpose, new_session_id)
                    session_uses = self._bump_session_uses(purpose)
                    if context_tokens_reported and context_tokens_reported > self._max_resume_context_tokens():
                        self._clear_session_id(purpose)
                        logger.info(
                            f"Hermes session for purpose={purpose} reset after "
                            f"context reached {context_tokens_reported} tokens"
                        )
                    elif session_uses and session_uses >= self._max_resume_uses():
                        self._clear_session_id(purpose)
                        logger.info(
                            f"Hermes session for purpose={purpose} reset after "
                            f"{session_uses} resumed calls"
                        )
                elif result.returncode != 0 and session_id and re.search(r"invalid session|not found|no such session", combined, re.I):
                    self._clear_session_id(purpose)
                prompt_tokens_est = self._estimate_tokens(message)
                completion_tokens_est = self._estimate_tokens(out)
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": attempt + 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": elapsed_ms,
                    "returncode": result.returncode,
                    "status": "ok" if result.returncode == 0 and out else "failed",
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(message),
                    "prompt_tokens_est": prompt_tokens_est,
                    "completion_tokens_est": completion_tokens_est,
                    "total_tokens_est": prompt_tokens_est + completion_tokens_est,
                    "context_tokens_reported": context_tokens_reported,
                    "session_id": new_session_id,
                    "resumed_session": bool(session_id),
                    "stdout_preview": out[:500],
                    "stderr_preview": err[:500],
                    "message_preview": message[:500],
                })

                # Success
                if result.returncode == 0 and out:
                    return self._strip_session_noise(out) or out

                # Ollama fallback: if Ollama-routed call failed, retry without Ollama (using default API)
                if _ollama_env and result.returncode != 0 and not (out and self._strip_session_noise(out)):
                    logger.warning("[HermesAdapter] Ollama call failed (rc=%s), falling back to default API for purpose=%s",
                                   result.returncode, purpose)
                    # Rebuild cmd without Ollama routing
                    fallback_cmd = [self._hermes_executable(), "chat", "-q", message, "-Q"]
                    if self.model:
                        fallback_cmd.extend(["-m", self.model])
                    if self.provider:
                        fallback_cmd.extend(["--provider", self.provider])
                    # Re-apply purpose-specific flags
                    _add_purpose_flags(fallback_cmd, purpose)
                    fallback_env = self._build_hermes_env()
                    run_kwargs["args"] = fallback_cmd
                    run_kwargs["env"] = fallback_env
                    result = _run_subprocess_tree(run_kwargs)
                    out = result.stdout.strip()
                    err = (result.stderr or "").strip()
                    if result.returncode == 0 and out:
                        return self._strip_session_noise(out) or out
                    logger.warning("[HermesAdapter] API fallback also failed for purpose=%s", purpose)

                # Check for 429 rate limit in stdout OR stderr
                if "429" in combined or "Too many requests" in combined:
                    if attempt < max_retries:
                        wait = 15 * (attempt + 1)
                        logger.warning(f"Rate limited (429), retry {attempt+1}/{max_retries} in {wait}s")
                        time.sleep(wait)
                        continue
                    return USER_FRIENDLY_PROGRESS_REPLY

                # Check for other API failures in stdout
                if "API call failed" in out:
                    if attempt < max_retries:
                        wait = 10 * (attempt + 1)
                        logger.warning(f"API failed, retry {attempt+1}/{max_retries} in {wait}s")
                        time.sleep(wait)
                        continue
                    return USER_FRIENDLY_PROGRESS_REPLY

                if result.returncode != 0:
                    logger.warning(f"hermes chat exit {result.returncode}: {combined[:200]}")
                    if attempt < max_retries:
                        time.sleep(5)
                        continue

                return self._strip_session_noise(out) or USER_FRIENDLY_PROGRESS_REPLY

            except subprocess.TimeoutExpired:
                killed_tools = _cleanup_workspace_tool_processes(self.workspace)
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": attempt + 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": int((time.time() - started_at) * 1000),
                    "returncode": None,
                    "status": "timeout",
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(message),
                    "prompt_tokens_est": self._estimate_tokens(message),
                    "completion_tokens_est": 0,
                    "total_tokens_est": self._estimate_tokens(message),
                    "session_id": session_id,
                    "resumed_session": bool(session_id),
                    "message_preview": message[:500],
                    "error": f"workspace_tool_processes_killed={killed_tools}" if killed_tools else "",
                })
                logger.warning(f"hermes chat timeout ({timeout_sec}s), attempt {attempt+1}/{max_retries+1}")
                if attempt < max_retries:
                    continue
                return USER_FRIENDLY_PROGRESS_REPLY
            except FileNotFoundError:
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": attempt + 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": int((time.time() - started_at) * 1000),
                    "returncode": None,
                    "status": "backend_not_available",
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(message),
                    "prompt_tokens_est": self._estimate_tokens(message),
                    "completion_tokens_est": 0,
                    "total_tokens_est": self._estimate_tokens(message),
                    "session_id": session_id,
                    "resumed_session": bool(session_id),
                    "error": "hermes executable not found",
                    "message_preview": message[:500],
                })
                return USER_FRIENDLY_PROGRESS_REPLY
            except Exception as e:
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": attempt + 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": int((time.time() - started_at) * 1000),
                    "returncode": None,
                    "status": "exception",
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(message),
                    "prompt_tokens_est": self._estimate_tokens(message),
                    "completion_tokens_est": 0,
                    "total_tokens_est": self._estimate_tokens(message),
                    "session_id": session_id,
                    "resumed_session": bool(session_id),
                    "error": str(e),
                    "message_preview": message[:500],
                })
                logger.warning(f"hermes chat exception: {e}")
                return USER_FRIENDLY_PROGRESS_REPLY

        return USER_FRIENDLY_PROGRESS_REPLY

    def chat_with_images(
        self,
        message: str,
        image_paths: list[str],
        max_tokens: int = None,
        purpose: str = "vision",
    ) -> str:
        """Use Hermes CLI multimodal support (`hermes chat --image`)."""
        import subprocess
        import time

        valid = [p for p in (image_paths or []) if p and os.path.exists(p)]
        if not valid:
            return USER_FRIENDLY_PROGRESS_REPLY

        max_images = 8
        selected = valid[:max_images]
        outputs: list[str] = []
        for idx, image_path in enumerate(selected, start=1):
            prompt = (
                f"{message}\n\n"
                f"这是第 {idx}/{len(selected)} 张图片或长截图切片。请读取图片中的文字和视觉信息，"
                "不要编造看不见的内容。"
            )
            cmd = [self._hermes_executable(), "chat", "-q", prompt, "--image", image_path, "-Q"]
            if self.model:
                cmd.extend(["-m", self.model])
            if self.provider:
                cmd.extend(["--provider", self.provider])
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
            timeout_sec = None
            started_at = time.time()
            try:
                run_kwargs = {
                    "args": cmd,
                    "capture_output": True,
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                    "cwd": os.path.join(self.workspace, "system", "hermes_work"),
                    "env": self._build_hermes_env(),
                    "creationflags": _NTFLAGS,
                }
                if timeout_sec is not None:
                    run_kwargs["timeout"] = timeout_sec
                result = subprocess.run(**run_kwargs)
                out = self._strip_session_noise((result.stdout or "").strip())
                out = re.sub(r"(?im)^\s*⚠️?\s*Reached maximum iterations.*(?:\n|$)", "", out).strip()
                err = (result.stderr or "").strip()
                elapsed_ms = int((time.time() - started_at) * 1000)
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": elapsed_ms,
                    "returncode": result.returncode,
                    "status": "ok" if result.returncode == 0 and out else "failed",
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(prompt),
                    "prompt_tokens_est": self._estimate_tokens(prompt),
                    "completion_tokens_est": self._estimate_tokens(out),
                    "total_tokens_est": self._estimate_tokens(prompt) + self._estimate_tokens(out),
                    "stdout_preview": out[:500],
                    "stderr_preview": err[:500],
                    "message_preview": prompt[:500],
                    "image_path": image_path,
                })
                if result.returncode == 0 and out:
                    outputs.append(f"## 图片{idx}\n{out}")
            except Exception as exc:
                self._log_chat_attempt({
                    "ts": datetime.now().isoformat(),
                    "attempt": 1,
                    "purpose": purpose,
                    "timeout_sec": timeout_sec,
                    "elapsed_ms": int((time.time() - started_at) * 1000),
                    "returncode": None,
                    "status": "exception",
                    "model": self.model,
                    "provider": self.provider,
                    "message_chars": len(prompt),
                    "prompt_tokens_est": self._estimate_tokens(prompt),
                    "completion_tokens_est": 0,
                    "total_tokens_est": self._estimate_tokens(prompt),
                    "error": str(exc),
                    "message_preview": prompt[:500],
                    "image_path": image_path,
                })
        return "\n\n".join(outputs).strip() or USER_FRIENDLY_PROGRESS_REPLY


class CustomEndpointHermesAdapter(HermesAdapter):
    """Hermes adapter pinned to an OpenAI-compatible endpoint.

    Hermes reads custom provider base_url/api_key from config.yaml.  This
    adapter patches the per-instance Hermes home before each call so the
    caller can safely switch a single instance to a local/remote Ollama
    endpoint without changing the user's global Hermes config.
    """

    def __init__(
        self,
        workspace_path: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: str = "ollama",
    ):
        super().__init__(workspace_path, model=model, provider=provider or "custom")
        self.base_url = (base_url or os.getenv("PARTNER_DYNAMIC_OLLAMA_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or "ollama"

    def _build_hermes_env(self) -> dict:
        env = super()._build_hermes_env()
        if self.base_url:
            self._patch_hermes_config(
                os.path.join(self.workspace, "system", "hermes_home"),
                model=self.model or "",
                provider=self.provider or "custom",
                base_url=self.base_url,
                api_key=self.api_key,
            )
        return env

    @staticmethod
    def _patch_hermes_config(
        hermes_home: str,
        model: str,
        provider: str,
        base_url: str,
        api_key: str,
    ) -> None:
        os.makedirs(hermes_home, exist_ok=True)
        path = os.path.join(hermes_home, "config.yaml")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = ""
        else:
            text = ""

        lines = text.splitlines()
        out = []
        in_model = False
        saw_model = saw_default = saw_provider = saw_base = saw_key = False
        for line in lines:
            stripped = line.strip()
            top_level = line and not line.startswith((" ", "\t"))
            if top_level and stripped == "model:":
                in_model = True
                saw_model = True
                out.append(line)
                continue
            if in_model and top_level and stripped != "model:":
                if not saw_default:
                    out.append(f"  default: {model}")
                if not saw_provider:
                    out.append(f"  provider: {provider}")
                if not saw_base:
                    out.append(f"  base_url: {base_url}")
                if not saw_key:
                    out.append(f"  api_key: {api_key}")
                in_model = False
            if in_model and line.startswith("  "):
                key = stripped.split(":", 1)[0]
                if key == "default":
                    out.append(f"  default: {model}")
                    saw_default = True
                    continue
                if key == "provider":
                    out.append(f"  provider: {provider}")
                    saw_provider = True
                    continue
                if key == "base_url":
                    out.append(f"  base_url: {base_url}")
                    saw_base = True
                    continue
                if key == "api_key":
                    out.append(f"  api_key: {api_key}")
                    saw_key = True
                    continue
            out.append(line)

        if in_model:
            if not saw_default:
                out.append(f"  default: {model}")
            if not saw_provider:
                out.append(f"  provider: {provider}")
            if not saw_base:
                out.append(f"  base_url: {base_url}")
            if not saw_key:
                out.append(f"  api_key: {api_key}")
        if not saw_model:
            out = [
                "model:",
                f"  default: {model}",
                f"  provider: {provider}",
                f"  base_url: {base_url}",
                f"  api_key: {api_key}",
                *out,
            ]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(out).rstrip() + "\n")
        except OSError as exc:
            logger.warning(f"failed to patch Hermes custom endpoint config: {exc}")


class DynamicOllamaProjectAdapter(AgentAdapter):
    """Use Ollama for configured lightweight/project work when responsive.

    Selection is intentionally based on a small real completion rather than
    only static GPU numbers.  If a remote server is busy and Ollama falls back
    to CPU, the probe times out and the project run falls back to the primary
    API-backed agent.
    """

    def __init__(self, primary: AgentAdapter, workspace_path: str):
        self.primary = primary
        self.workspace = workspace_path
        config = _load_agent_config(workspace_path).get("dynamic_ollama", {})
        if not isinstance(config, dict):
            config = {}
        self.base_url = _env_or_config(
            "PARTNER_DYNAMIC_OLLAMA_BASE_URL",
            config,
            "base_url",
            "",
        ).rstrip("/")
        self.candidates = [
            m.strip()
            for m in _env_or_config(
                "PARTNER_DYNAMIC_OLLAMA_MODELS",
                config,
                "models",
                "qwen3:1.7b,qwen3:4b,qwen2.5:14b,qwen2.5:7b",
            ).split(",")
            if m.strip()
        ]
        self.probe_timeout_sec = float(
            _env_or_config("PARTNER_DYNAMIC_OLLAMA_PROBE_TIMEOUT_SEC", config, "probe_timeout_sec", "20")
        )
        self.unavailable_cooldown_sec = int(
            _env_or_config("PARTNER_DYNAMIC_OLLAMA_COOLDOWN_SEC", config, "cooldown_sec", "300")
        )
        self._unavailable_until = 0.0

    def name(self) -> str:
        return f"{self.primary.name()}+dynamic_ollama"

    def search_web(self, query: str) -> List[SearchResult]:
        return self.primary.search_web(query)

    def execute_task(self, prompt: str) -> str:
        return self.chat(prompt, purpose="project")

    def _status_path(self) -> str:
        state_dir = os.path.join(self.workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        return os.path.join(state_dir, "dynamic_ollama_status.json")

    def _write_status(self, payload: dict) -> None:
        try:
            payload = {"ts": datetime.now().isoformat(), **payload}
            with open(self._status_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            _refresh_runtime_cost_summary(self.workspace)
        except Exception as exc:
            logger.debug(f"failed to write dynamic Ollama status: {exc}")

    @staticmethod
    def _api_root(base_url: str) -> str:
        return base_url[:-3] if base_url.endswith("/v1") else base_url

    def _available_models(self) -> set:
        import urllib.request

        tags_url = self._api_root(self.base_url) + "/api/tags"
        with urllib.request.urlopen(tags_url, timeout=self.probe_timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            item.get("name") or item.get("model")
            for item in data.get("models", [])
            if item.get("name") or item.get("model")
        }

    def _probe_model(self, model: str) -> tuple[bool, str]:
        import time
        import urllib.error
        import urllib.request

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "只回复 OK"}],
            "stream": False,
            "temperature": 0,
            "max_tokens": 4,
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"},
            method="POST",
        )
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.probe_timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            elapsed_ms = int((time.time() - started) * 1000)
            if reply.strip():
                return True, f"probe_ok:{elapsed_ms}ms"
            return False, "empty_probe_reply"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            return False, f"{type(exc).__name__}: {str(exc)[:160]}"

    def _select_model(self, purpose: str = "project") -> Optional[str]:
        try:
            from ..ollama_pool import select_ollama

            selected = select_ollama(self.workspace, purpose)
            if selected:
                self.base_url = selected.api_base_url
                self._write_status({
                    "selected": selected.model,
                    "endpoint": selected.name,
                    "fallback": "",
                    "reason": selected.reason,
                    "base_url": selected.api_base_url,
                    "mode": selected.mode,
                    "purpose": purpose,
                })
                return selected.model
        except Exception as exc:
            logger.debug(f"ollama pool selection failed for {purpose}: {exc}")

        import time

        if not self.base_url or not self.candidates:
            self._write_status({
                "selected": "",
                "fallback": "primary_agent",
                "reason": "dynamic Ollama not configured",
                "base_url": self.base_url,
                "candidates": self.candidates,
            })
            return None
        now = time.time()
        if now < self._unavailable_until:
            self._write_status({
                "selected": "",
                "fallback": "primary_agent",
                "reason": "cooldown_after_unavailable",
                "base_url": self.base_url,
                "candidates": self.candidates,
            })
            return None
        try:
            available = self._available_models()
        except Exception as exc:
            self._unavailable_until = now + self.unavailable_cooldown_sec
            self._write_status({
                "selected": "",
                "fallback": "primary_agent",
                "reason": f"model_list_failed: {str(exc)[:160]}",
                "base_url": self.base_url,
                "candidates": self.candidates,
            })
            return None

        probe_results = []
        for model in self.candidates:
            if model not in available:
                probe_results.append({"model": model, "ok": False, "reason": "model_not_installed"})
                continue
            ok, reason = self._probe_model(model)
            probe_results.append({"model": model, "ok": ok, "reason": reason})
            if ok:
                self._write_status({
                    "selected": model,
                    "fallback": "",
                    "reason": reason,
                    "base_url": self.base_url,
                    "candidates": self.candidates,
                    "probe_results": probe_results,
                })
                return model

        self._unavailable_until = now + self.unavailable_cooldown_sec
        self._write_status({
            "selected": "",
            "fallback": "primary_agent",
            "reason": "all_candidates_unresponsive_or_missing",
            "base_url": self.base_url,
            "candidates": self.candidates,
            "probe_results": probe_results,
        })
        return None

    def _event_execution_profile(self, message: str) -> dict:
        text = message or ""
        event_type = ""
        match = re.search(r"(?m)^event_type[：:]\s*([A-Za-z0-9_.-]+)\s*$", text)
        if match:
            event_type = match.group(1).strip()
        heavy_events = {
            "data_fetch",
            "data_analysis",
            "visualization",
            "artifact_build",
            "pdf_report",
            "web_search",
            "web_capture",
            "project",
            "content_digest",
            "literature_review",
        }
        input_chars = len(text)
        if event_type in heavy_events or input_chars > 7000:
            return {
                "try_ollama": False,
                "event_type": event_type,
                "difficulty": "heavy",
                "reason": "event_or_large_context",
                "input_chars": input_chars,
            }
        if event_type:
            return {
                "try_ollama": False,
                "event_type": event_type,
                "difficulty": "event",
                "reason": "event_execution_requires_primary",
                "input_chars": input_chars,
            }
        return {
            "try_ollama": input_chars <= 1200,
            "event_type": event_type,
            "difficulty": "simple" if input_chars <= 1200 else "non_event_long",
            "reason": "simple_direct_reply" if input_chars <= 1200 else "long_reply_requires_primary",
            "input_chars": input_chars,
        }

    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        profile = self._event_execution_profile(message)
        if purpose not in {"chat", "interaction"}:
            self._write_status({
                "selected": "",
                "fallback": "primary_agent",
                "reason": "ollama_only_simple_direct_reply",
                "purpose": purpose,
                "event_type": profile.get("event_type", ""),
                "difficulty": profile.get("difficulty", ""),
                "input_chars": profile.get("input_chars", 0),
            })
            return self.primary.chat(message, max_tokens=max_tokens, purpose=purpose)
        if not profile.get("try_ollama"):
            self._write_status({
                "selected": "",
                "fallback": "primary_agent",
                "reason": f"event_policy:{profile.get('reason')}",
                "purpose": purpose,
                "event_type": profile.get("event_type", ""),
                "difficulty": profile.get("difficulty", ""),
                "input_chars": profile.get("input_chars", 0),
            })
            return self.primary.chat(message, max_tokens=max_tokens, purpose=purpose)
        selected = self._select_model(purpose)
        if selected:
            local = CustomEndpointHermesAdapter(
                self.workspace,
                model=selected,
                provider="custom",
                base_url=self.base_url,
                api_key="ollama",
            )
            reply = local.chat(message, max_tokens=max_tokens, purpose=purpose)
            if reply and reply != USER_FRIENDLY_PROGRESS_REPLY:
                return reply
        return self.primary.chat(message, max_tokens=max_tokens, purpose=purpose)

    def chat_with_images(
        self,
        message: str,
        image_paths: list[str],
        max_tokens: int = None,
        purpose: str = "vision",
    ) -> str:
        # Vision support depends on model capabilities.  Use the primary
        # backend by default; HybridLiteAdapter may try Ollama vision first.
        return self.primary.chat_with_images(message, image_paths, max_tokens=max_tokens, purpose=purpose)


class CodexAdapter(AgentAdapter):
    """Adapter for OpenAI Codex CLI."""

    def __init__(self, workspace_path: str, model: Optional[str] = None,
                 provider: Optional[str] = None):
        self.workspace = workspace_path
        self.model = model
        self.provider = provider

    def _log_chat_attempt(self, payload: dict):
        try:
            log_dir = os.path.join(self.workspace, "state", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "codex_chat.jsonl")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"failed to write Codex chat log: {exc}")
        try:
            log_dir = os.path.join(self.workspace, "state", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "agent_runs.jsonl")
            row = {
                "ts": payload.get("ts") or datetime.now().isoformat(),
                "backend": "codex",
                "purpose": payload.get("purpose", ""),
                "status": payload.get("status") or ("ok" if payload.get("returncode") == 0 else "failed"),
                "elapsed_ms": payload.get("elapsed_ms"),
                "timeout_sec": payload.get("timeout_sec"),
                "returncode": payload.get("returncode"),
                "model": payload.get("model"),
                "provider": payload.get("provider"),
                "message_chars": payload.get("message_chars"),
                "prompt_tokens_est": payload.get("prompt_tokens_est"),
                "completion_tokens_est": payload.get("completion_tokens_est"),
                "total_tokens_est": payload.get("total_tokens_est"),
                "stdout_preview": payload.get("stdout_preview", ""),
                "stderr_preview": payload.get("stderr_preview", ""),
                "error": payload.get("error", ""),
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _refresh_runtime_cost_summary(self.workspace)
        except Exception as exc:
            logger.warning(f"failed to write agent run log: {exc}")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / 2.2))

    def name(self) -> str:
        return "codex"

    @staticmethod
    def detect_installation() -> dict:
        executable = shutil.which("codex")
        candidates = []
        home = os.path.expanduser("~")
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        candidates.extend(
            [
                os.path.join(appdata, "npm", "codex.cmd"),
                os.path.join(appdata, "npm", "codex.ps1"),
                os.path.join(appdata, "npm", "codex"),
                os.path.join(localappdata, "Programs", "Codex", "codex.exe"),
                os.path.join(home, ".local", "bin", "codex"),
                os.path.join(home, ".npm-global", "bin", "codex"),
                "/usr/local/bin/codex",
                "/usr/bin/codex",
            ]
        )
        if not executable:
            executable = next((p for p in candidates if p and os.path.exists(os.path.expandvars(p))), "")
        info = {"available": bool(executable), "path": executable or "", "version": "", "issues": []}
        if executable:
            try:
                result = subprocess.run(
                    [executable, "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    creationflags=_NTFLAGS,
                )
                info["version"] = (result.stdout or result.stderr or "").strip().splitlines()[0].strip()
                if result.returncode != 0:
                    info["issues"].append(f"version check exited {result.returncode}")
            except Exception as exc:
                info["issues"].append(str(exc))
        return info

    @classmethod
    def is_available(cls) -> bool:
        return bool(cls.detect_installation().get("available"))

    def search_web(self, query: str) -> List[SearchResult]:
        prompt = (
            f"Search the web for: {query}\n"
            f"Return up to 5 items in plain text, each item on one line as:\n"
            f"title | url | snippet"
        )
        result = self.execute_task(prompt)
        rows = []
        for line in (result or "").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                rows.append(SearchResult(title=parts[0], url=parts[1], snippet=" | ".join(parts[2:])))
            if len(rows) >= 5:
                break
        snippet = (result or "").strip()
        if len(snippet) > 200:
            snippet = snippet[:199].rstrip() + "…"
        return rows or [SearchResult(title="Search Result", url="", snippet=snippet)]

    def execute_task(self, prompt: str) -> str:
        return self.chat(prompt, purpose="project")

    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        import subprocess
        import tempfile
        import time

        timeout_sec = None
        if purpose == "classify":
            timeout_sec = _agent_optional_timeout(self.workspace, "PARTNER_CLASSIFY_TIMEOUT_SEC", "classify_timeout_sec", 90)
        elif purpose == "interaction":
            timeout_sec = _env_optional_timeout("PARTNER_INTERACTION_TIMEOUT_SEC", None)
        elif purpose == "project":
            timeout_sec = _project_timeout_sec(self.workspace)
        elif purpose == "report":
            timeout_sec = _env_optional_timeout("PARTNER_REPORT_TIMEOUT_SEC", None)

        out_dir = os.path.join(self.workspace, "99_temp")
        os.makedirs(out_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="codex_last_", suffix=".txt", dir=out_dir, delete=False) as tf:
            output_path = tf.name

        executable = str(self.detect_installation().get("path") or "codex")
        cmd = [
            executable, "exec",
            "--skip-git-repo-check",
            "--color", "never",
            "--sandbox", "workspace-write",
            "--output-last-message", output_path,
            "-C", self.workspace,
        ]
        if self.model:
            cmd.extend(["-m", self.model])
        if purpose == "classify":
            cmd.extend(["-c", "model_reasoning_effort=\"low\""])
        elif purpose == "report":
            cmd.extend(["-c", "model_reasoning_effort=\"low\""])
        elif purpose == "project":
            cmd.extend(["-c", "model_reasoning_effort=\"medium\""])
        cmd.append(message)

        started_at = time.time()
        try:
            run_kwargs = {
                "args": cmd,
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "cwd": self.workspace,
                "creationflags": _NTFLAGS,
                "stdin": subprocess.DEVNULL,
            }
            if timeout_sec is not None:
                run_kwargs["timeout"] = timeout_sec
            result = _run_subprocess_tree(run_kwargs)
            elapsed_ms = int((time.time() - started_at) * 1000)
            reply = ""
            if os.path.exists(output_path):
                try:
                    with open(output_path, "r", encoding="utf-8") as f:
                        reply = f.read().strip()
                except OSError:
                    reply = ""
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "timeout_sec": timeout_sec,
                "elapsed_ms": elapsed_ms,
                "returncode": result.returncode,
                "status": "ok" if result.returncode == 0 and reply else "failed",
                "model": self.model,
                "provider": self.provider,
                "message_chars": len(message),
                "prompt_tokens_est": self._estimate_tokens(message),
                "completion_tokens_est": self._estimate_tokens(reply),
                "total_tokens_est": self._estimate_tokens(message) + self._estimate_tokens(reply),
                "stdout_preview": out[:500],
                "stderr_preview": err[:500],
                "reply_preview": reply[:500],
                "message_preview": message[:500],
            })
            if result.returncode == 0 and reply:
                return reply
            combined = f"{out}\n{err}\n{reply}".strip()
            if "429" in combined or "Too many requests" in combined:
                return USER_FRIENDLY_PROGRESS_REPLY
            return reply or USER_FRIENDLY_PROGRESS_REPLY
        except subprocess.TimeoutExpired:
            killed_tools = _cleanup_workspace_tool_processes(self.workspace)
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "timeout_sec": timeout_sec,
                "elapsed_ms": int((time.time() - started_at) * 1000),
                "returncode": None,
                "status": "timeout",
                "model": self.model,
                "provider": self.provider,
                "message_chars": len(message),
                "prompt_tokens_est": self._estimate_tokens(message),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(message),
                "message_preview": message[:500],
                "error": f"workspace_tool_processes_killed={killed_tools}" if killed_tools else "",
            })
            logger.warning(f"codex exec timeout ({timeout_sec}s)")
            return USER_FRIENDLY_PROGRESS_REPLY
        except FileNotFoundError:
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "timeout_sec": timeout_sec,
                "elapsed_ms": int((time.time() - started_at) * 1000),
                "returncode": None,
                "status": "backend_not_available",
                "model": self.model,
                "provider": self.provider,
                "message_chars": len(message),
                "prompt_tokens_est": self._estimate_tokens(message),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(message),
                "error": "codex executable not found",
                "message_preview": message[:500],
            })
            return USER_FRIENDLY_PROGRESS_REPLY
        except Exception as e:
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "timeout_sec": timeout_sec,
                "elapsed_ms": int((time.time() - started_at) * 1000),
                "returncode": None,
                "status": "exception",
                "model": self.model,
                "provider": self.provider,
                "message_chars": len(message),
                "prompt_tokens_est": self._estimate_tokens(message),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(message),
                "status": "exception",
                "error": str(e),
                "message_preview": message[:500],
            })
            logger.warning(f"codex exec exception: {e}")
            return USER_FRIENDLY_PROGRESS_REPLY
        finally:
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except OSError:
                pass


class OllamaLiteAdapter(AgentAdapter):
    """Small local-model adapter for cheap short responses.

    This deliberately does not implement a general tool runner.  It is meant
    for low-risk classification, interaction, and reporting prompts where a
    short deterministic answer is useful and API cost matters.
    """

    def __init__(self, workspace_path: str, model: Optional[str] = None,
                 provider: Optional[str] = None):
        self.workspace = workspace_path
        self.model = model or os.getenv("PARTNER_OLLAMA_MODEL", "qwen3:1.7b")
        self.base_url = (
            provider if provider and provider.startswith(("http://", "https://"))
            else os.getenv("PARTNER_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        ).rstrip("/")
        self.timeout_sec = _env_optional_timeout("PARTNER_OLLAMA_TIMEOUT_SEC", None)
        self.probe_timeout_sec = _env_optional_float_timeout("PARTNER_OLLAMA_PROBE_TIMEOUT_SEC", None)
        self.unavailable_cooldown_sec = int(os.getenv("PARTNER_OLLAMA_UNAVAILABLE_COOLDOWN_SEC", "300"))
        self.max_input_chars = int(os.getenv("PARTNER_OLLAMA_MAX_INPUT_CHARS", "4000"))
        self._unavailable_until = 0.0

    def name(self) -> str:
        return "ollama_lite"

    def _status_path(self) -> str:
        state_dir = os.path.join(self.workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        return os.path.join(state_dir, "ollama_lite_status.json")

    def _write_status(self, available: bool, reason: str = "") -> None:
        try:
            payload = {
                "ts": datetime.now().isoformat(),
                "available": available,
                "model": self.model,
                "base_url": self.base_url,
                "reason": reason,
                "mode": os.getenv("PARTNER_OLLAMA_LITE", "auto"),
                "fallback": "primary_agent",
            }
            with open(self._status_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            _refresh_runtime_cost_summary(self.workspace)
        except Exception as exc:
            logger.debug(f"failed to write ollama lite status: {exc}")

    def _log_chat_attempt(self, payload: dict):
        try:
            log_dir = os.path.join(self.workspace, "state", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "ollama_lite_chat.jsonl")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"failed to write OllamaLite chat log: {exc}")
        try:
            log_dir = os.path.join(self.workspace, "state", "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "agent_runs.jsonl")
            row = {
                "ts": payload.get("ts") or datetime.now().isoformat(),
                "backend": "ollama_lite",
                "purpose": payload.get("purpose", ""),
                "status": payload.get("status", ""),
                "elapsed_ms": payload.get("elapsed_ms"),
                "timeout_sec": payload.get("timeout_sec"),
                "model": payload.get("model"),
                "provider": payload.get("provider"),
                "message_chars": payload.get("message_chars"),
                "prompt_tokens_est": payload.get("prompt_tokens_est"),
                "completion_tokens_est": payload.get("completion_tokens_est"),
                "total_tokens_est": payload.get("total_tokens_est"),
                "reply_preview": payload.get("reply_preview", ""),
                "error": payload.get("error", ""),
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _refresh_runtime_cost_summary(self.workspace)
        except Exception as exc:
            logger.warning(f"failed to write agent run log: {exc}")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / 2.2))

    @staticmethod
    def _ollama_api_root(base_url: str) -> str:
        return base_url[:-3] if base_url.endswith("/v1") else base_url

    @classmethod
    def is_available(cls) -> bool:
        import urllib.request
        from urllib.parse import urlparse

        base_url = os.getenv("PARTNER_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
        version_url = cls._ollama_api_root(base_url) + "/api/version"
        try:
            timeout = _env_optional_float_timeout("PARTNER_OLLAMA_PROBE_TIMEOUT_SEC", None)
            req = urllib.request.Request(version_url)
            # Bypass proxy for localhost
            parsed = urlparse(version_url)
            if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
                orig = os.environ.pop("http_proxy", None)
                os.environ.pop("https_proxy", None)
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        return resp.status == 200
                finally:
                    if orig is not None:
                        os.environ["http_proxy"] = orig
            else:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status == 200
        except Exception:
            return False

    def _probe_available(self) -> bool:
        try:
            from ..ollama_pool import select_ollama

            selected = select_ollama(self.workspace, "report")
            if selected:
                self.base_url = selected.api_base_url
                self.model = self._choose_lite_model(selected.model)
                self._write_status(True, f"pool:{selected.name}:{selected.reason}")
                return True
        except Exception as exc:
            logger.debug(f"ollama pool lite probe failed: {exc}")

        import time
        import urllib.request

        now = time.time()
        if now < self._unavailable_until:
            return False

        # Fallback: probe Ollama API directly
        try:
            version_url = self._ollama_api_root(self.base_url) + "/api/version"
            req = urllib.request.Request(version_url)
            # Bypass proxy for localhost
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(version_url)
            if parsed.hostname in ("127.0.0.1", "localhost", "::1"):
                orig = os.environ.pop("http_proxy", None)
                os.environ.pop("https_proxy", None)
                try:
                    with urllib.request.urlopen(req, timeout=self.probe_timeout_sec) as resp:
                        ok = resp.status == 200
                finally:
                    if orig is not None:
                        os.environ["http_proxy"] = orig
            else:
                with urllib.request.urlopen(req, timeout=self.probe_timeout_sec) as resp:
                    ok = resp.status == 200
            self._write_status(ok, "" if ok else f"HTTP status {resp.status}")
            return ok
        except Exception as exc:
            self._unavailable_until = now + self.unavailable_cooldown_sec
            self._write_status(False, str(exc)[:200])
            return False

    @staticmethod
    def _model_size_rank(model: str) -> float:
        name = (model or "").lower()
        match = re.search(r"(\d+(?:\.\d+)?)\s*b\b", name)
        if match:
            try:
                return float(match.group(1))
            except Exception:
                pass
        if "0.5" in name or "0_5" in name:
            return 0.5
        if "1.5" in name or "1_5" in name:
            return 1.5
        if "3b" in name:
            return 3.0
        if "7b" in name:
            return 7.0
        if "14b" in name:
            return 14.0
        return 999.0

    def _choose_lite_model(self, fallback: str) -> str:
        return fallback
        version_url = self._ollama_api_root(self.base_url) + "/api/version"
        try:
            with urllib.request.urlopen(version_url, timeout=self.probe_timeout_sec) as resp:
                ok = resp.status == 200
            self._write_status(ok, "" if ok else f"HTTP status {resp.status}")
            return ok
        except Exception as exc:
            self._unavailable_until = now + self.unavailable_cooldown_sec
            self._write_status(False, str(exc)[:200])
            return False

    def search_web(self, query: str) -> List[SearchResult]:
        return [SearchResult(
            title="Search unavailable",
            url="",
            snippet="ollama_lite does not provide web search.",
        )]

    def execute_task(self, prompt: str) -> str:
        return self.chat(prompt, purpose="project")

    def _system_prompt(self, purpose: str) -> str:
        if purpose == "classify":
            return (
                "You are a strict local classifier. Return only compact JSON. "
                "No markdown, no explanation."
            )
        if purpose == "interaction":
            return (
                "You write concise Chinese user-facing replies. Do not mention "
                "internal logs, files, queues, tools, backend names, or JSON."
            )
        if purpose == "report":
            return (
                "You summarize progress in concise Chinese. Focus on concrete "
                "results, risks, and next action. No markdown unless requested."
            )
        return (
            "You are a small local model. Answer briefly and honestly. Do not "
            "claim that you executed commands, edited files, or used tools."
        )

    def _max_tokens(self, purpose: str, requested: Optional[int]) -> int:
        if requested:
            return requested
        if purpose == "classify":
            return 512 if "qwen3" in (self.model or "").lower() else 160
        if purpose == "interaction":
            return 420 if "qwen3" in (self.model or "").lower() else 220
        if purpose == "report":
            return 520 if "qwen3" in (self.model or "").lower() else 360
        return 420 if "qwen3" in (self.model or "").lower() else 240

    def _timeout_for_purpose(self, purpose: str) -> int | None:
        defaults = {
            "classify": None,
            "interaction": None,
            "report": None,
            "chat": None,
        }
        env_name = f"PARTNER_OLLAMA_{str(purpose or 'CHAT').upper()}_TIMEOUT_SEC"
        raw = os.getenv(env_name)
        if raw is None:
            raw = os.getenv("PARTNER_OLLAMA_LITE_TIMEOUT_SEC")
        if raw is not None and str(raw).strip().lower() in {"0", "none", "no", "off", "false", "disabled", "unlimited"}:
            return None
        try:
            limit = int(float(raw)) if raw else defaults.get(purpose, self.timeout_sec)
        except Exception:
            limit = defaults.get(purpose, self.timeout_sec)
        if limit is None:
            return None
        if self.timeout_sec is None:
            return max(1, int(limit))
        return max(1, min(int(self.timeout_sec), int(limit)))

    @staticmethod
    def _clean_reply(reply: str, purpose: str) -> str:
        cleaned = (reply or "").strip()
        if purpose != "classify":
            return cleaned
        fenced = re.match(r"(?is)^```(?:json)?\s*(.*?)\s*```$", cleaned)
        if fenced:
            cleaned = fenced.group(1).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1].strip()
        return cleaned

    @staticmethod
    def _is_vision_model_name(model: str) -> bool:
        name = (model or "").lower()
        needles = (
            "vision",
            "vl",
            "llava",
            "bakllava",
            "minicpm-v",
            "minicpmv",
            "qwen2-vl",
            "qwen2.5-vl",
            "qwen2.5vl",
            "gemma3",
        )
        return any(x in name for x in needles)

    @staticmethod
    def _looks_like_no_image_reply(reply: str) -> bool:
        text = (reply or "").lower()
        if not text:
            return True
        cn_needles = (
            "没有提供实际的图片",
            "没有提供实际的截图",
            "没有提供图片",
            "无法直接读取",
            "请您上传",
            "请上传",
            "没有收到图片",
            "未提供图片",
        )
        en_needles = (
            "no image",
            "no actual image",
            "cannot access the image",
            "please upload",
            "image was not provided",
        )
        return any(x in text for x in cn_needles + en_needles)

    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        import time
        import urllib.error
        import urllib.request

        if purpose == "project":
            timeout_sec = self._timeout_for_purpose(purpose)
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "status": "unsupported_project",
                "timeout_sec": timeout_sec,
                "elapsed_ms": 0,
                "model": self.model,
                "provider": self.base_url,
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(message),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(message),
                "error": "ollama_lite intentionally does not run project tasks",
            })
            return USER_FRIENDLY_PROGRESS_REPLY

        if not self._probe_available():
            # Fast fallback path: when the user's laptop/tunnel is offline, do
            # not block interaction/report calls. HybridLiteAdapter will route
            # the same prompt to the primary backend.
            return USER_FRIENDLY_PROGRESS_REPLY

        clipped = message or ""
        if len(clipped) > self.max_input_chars:
            clipped = clipped[:self.max_input_chars] + "\n\n[truncated for local model]"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt(purpose)},
                {"role": "user", "content": clipped},
            ],
            "stream": False,
            "temperature": 0.1,
            "max_tokens": self._max_tokens(purpose, max_tokens),
            "keep_alive": os.getenv("PARTNER_OLLAMA_KEEP_ALIVE", "30m"),
        }
        if purpose == "classify":
            payload["response_format"] = {"type": "json_object"}
        started_at = time.time()
        timeout_sec = self._timeout_for_purpose(purpose)
        try:
            if "qwen3" in (self.model or "").lower():
                native_payload = {
                    "model": self.model,
                    "messages": payload["messages"],
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": payload["temperature"],
                        "num_predict": payload["max_tokens"],
                    },
                }
                if purpose == "classify":
                    native_payload["format"] = "json"
                req = urllib.request.Request(
                    self._ollama_api_root(self.base_url) + "/api/chat",
                    data=json.dumps(native_payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                reply = data.get("message", {}).get("content", "").strip()
            else:
                req = urllib.request.Request(
                    self.base_url + "/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                reply = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
            reply = self._clean_reply(reply, purpose)
            elapsed_ms = int((time.time() - started_at) * 1000)
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "status": "ok" if reply else "empty",
                "timeout_sec": timeout_sec,
                "elapsed_ms": elapsed_ms,
                "model": self.model,
                "provider": self.base_url,
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(clipped),
                "completion_tokens_est": self._estimate_tokens(reply),
                "total_tokens_est": self._estimate_tokens(clipped) + self._estimate_tokens(reply),
                "reply_preview": reply[:500],
            })
            return f"[ollama]\n{reply}" if reply else USER_FRIENDLY_PROGRESS_REPLY
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "status": "failed",
                "timeout_sec": timeout_sec,
                "elapsed_ms": int((time.time() - started_at) * 1000),
                "model": self.model,
                "provider": self.base_url,
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(clipped),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(clipped),
                "error": str(exc),
            })
            return USER_FRIENDLY_PROGRESS_REPLY

    def chat_with_images(
        self,
        message: str,
        image_paths: list[str],
        max_tokens: int = None,
        purpose: str = "vision",
    ) -> str:
        """Use Ollama native multimodal chat when the selected model supports it."""
        import time
        import urllib.error
        import urllib.request

        valid = [p for p in (image_paths or []) if p and os.path.exists(p)]
        if not valid or not self._probe_available():
            return USER_FRIENDLY_PROGRESS_REPLY
        if not self._is_vision_model_name(self.model):
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "status": "unsupported_vision_model",
                "timeout_sec": 0,
                "elapsed_ms": 0,
                "model": self.model,
                "provider": self.base_url,
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(message),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(message),
                "error": "selected Ollama model is not known to support images",
                "image_count": len(valid),
            })
            return USER_FRIENDLY_PROGRESS_REPLY
        images: list[str] = []
        for path in valid[:8]:
            try:
                with open(path, "rb") as f:
                    images.append(base64.b64encode(f.read()).decode("ascii"))
            except OSError:
                continue
        if not images:
            return USER_FRIENDLY_PROGRESS_REPLY
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": message,
                    "images": images,
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": self._max_tokens(purpose, max_tokens),
            },
        }
        started_at = time.time()
        api_root = self._ollama_api_root(self.base_url)
        try:
            req = urllib.request.Request(
                api_root + "/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            reply = str((data.get("message") or {}).get("content") or "").strip()
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "status": "no_image_reply" if self._looks_like_no_image_reply(reply) else ("ok" if reply else "empty"),
                "timeout_sec": self.timeout_sec,
                "elapsed_ms": int((time.time() - started_at) * 1000),
                "model": self.model,
                "provider": api_root,
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(message),
                "completion_tokens_est": self._estimate_tokens(reply),
                "total_tokens_est": self._estimate_tokens(message) + self._estimate_tokens(reply),
                "reply_preview": reply[:500],
                "image_count": len(images),
            })
            if self._looks_like_no_image_reply(reply):
                return USER_FRIENDLY_PROGRESS_REPLY
            return reply or USER_FRIENDLY_PROGRESS_REPLY
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "status": "failed",
                "timeout_sec": self.timeout_sec,
                "elapsed_ms": int((time.time() - started_at) * 1000),
                "model": self.model,
                "provider": api_root,
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(message),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(message),
                "error": str(exc),
                "image_count": len(images),
            })
            return USER_FRIENDLY_PROGRESS_REPLY


class DirectAdapter(AgentAdapter):
    """Direct adapter - Partner operates without an external agent.
    
    This is the simplest backend: Partner uses its own logic
    to execute tasks, without delegating to another agent.
    """
    
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
    
    def name(self) -> str:
        return "direct"
    
    def search_web(self, query: str) -> List[SearchResult]:
        # For MVP, use a simple HTTP search
        import urllib.request
        import urllib.parse
        import json
        
        try:
            url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "Partner/0.1"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            results = []
            for item in data.get("RelatedTopics", [])[:5]:
                if "Text" in item:
                    results.append(SearchResult(
                        title=item.get("Text", "")[:80],
                        url=item.get("FirstURL", ""),
                        snippet=item.get("Text", ""),
                    ))
            return results
        except Exception as e:
            return [SearchResult(title="Search failed", url="", snippet=str(e))]
    
    def execute_task(self, prompt: str) -> str:
        """Execute a task directly using Partner's own capabilities."""
        # For MVP, just return the prompt for the cron job to handle
        return f"Task recorded: {prompt}"
    
    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        return "Direct mode: I can only work through scheduled tasks."


class HybridLiteAdapter(AgentAdapter):
    """Route small language tasks to Ollama and heavy project work to primary."""

    def __init__(self, primary: AgentAdapter, lite: OllamaLiteAdapter):
        self.primary = primary
        self.lite = lite
        self.workspace = getattr(primary, "workspace", getattr(lite, "workspace", ""))

    def name(self) -> str:
        return f"{self.primary.name()}+ollama_lite"

    def search_web(self, query: str) -> List[SearchResult]:
        return self.primary.search_web(query)

    def execute_task(self, prompt: str) -> str:
        return self.primary.execute_task(prompt)

    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        if purpose in {"chat", "interaction"} and len(message or "") <= 1200:
            reply = self.lite.chat(message, max_tokens=max_tokens, purpose=purpose)
            if reply and reply != USER_FRIENDLY_PROGRESS_REPLY:
                return reply
        return self.primary.chat(message, max_tokens=max_tokens, purpose=purpose)

    def chat_with_images(
        self,
        message: str,
        image_paths: list[str],
        max_tokens: int = None,
        purpose: str = "vision",
    ) -> str:
        # Try local/remote Ollama vision first if configured with a vision model;
        # fall back to primary backend such as Hermes --image.
        reply = self.lite.chat_with_images(message, image_paths, max_tokens=max_tokens, purpose=purpose)
        if reply and reply != USER_FRIENDLY_PROGRESS_REPLY:
            return reply
        return self.primary.chat_with_images(message, image_paths, max_tokens=max_tokens, purpose=purpose)


class FallbackAgentAdapter(AgentAdapter):
    """Try the primary backend first, then configured backups on unusable output."""

    def __init__(self, primary: AgentAdapter, backups: list[AgentAdapter], workspace: str):
        self.primary = primary
        self.backups = backups
        self.workspace = workspace

    def name(self) -> str:
        names = [self.primary.name()] + [adapter.name() for adapter in self.backups]
        return "+failover(".join([names[0], ",".join(names[1:])]) + ")" if self.backups else self.primary.name()

    def search_web(self, query: str) -> List[SearchResult]:
        try:
            return self.primary.search_web(query)
        except Exception:
            for adapter in self.backups:
                try:
                    return adapter.search_web(query)
                except Exception:
                    continue
        return []

    def execute_task(self, prompt: str) -> str:
        return self.chat(prompt, purpose="project")

    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        reply = self.primary.chat(message, max_tokens=max_tokens, purpose=purpose)
        if not _agent_failed(reply):
            return reply
        self._log_failover(self.primary.name(), "", purpose, reply)
        for adapter in self.backups:
            backup_reply = adapter.chat(message, max_tokens=max_tokens, purpose=purpose)
            if not _agent_failed(backup_reply):
                self._log_failover(self.primary.name(), adapter.name(), purpose, "switched")
                # Mark backup replies with [backend_name] prefix
                backend_tag = adapter.name().replace("_lite", "").replace("_", "")
                return f"[{backend_tag}]\n{backup_reply}"
            self._log_failover(adapter.name(), "", purpose, backup_reply)
        return reply

    def chat_with_images(
        self,
        message: str,
        image_paths: list[str],
        max_tokens: int = None,
        purpose: str = "vision",
    ) -> str:
        reply = self.primary.chat_with_images(message, image_paths, max_tokens=max_tokens, purpose=purpose)
        if not _agent_failed(reply):
            return reply
        for adapter in self.backups:
            backup_reply = adapter.chat_with_images(message, image_paths, max_tokens=max_tokens, purpose=purpose)
            if not _agent_failed(backup_reply):
                self._log_failover(self.primary.name(), adapter.name(), purpose, "vision_switched")
                backend_tag = adapter.name().replace("_lite", "").replace("_", "")
                return f"[{backend_tag}]\n{backup_reply}"
            return reply

    def _log_failover(self, from_backend: str, to_backend: str, purpose: str, reason: str) -> None:
        try:
            log_dir = os.path.join(self.workspace, "state", "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "agent_failover.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now().isoformat(),
                    "from": from_backend,
                    "to": to_backend,
                    "purpose": purpose,
                    "reason": str(reason or "")[:500],
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass


def create_adapter(backend: str, workspace_path: str, model: Optional[str] = None,
                   provider: Optional[str] = None) -> AgentAdapter:
    """Factory function to create the appropriate adapter."""
    adapters = {
        "hermes": HermesAdapter,
        "codex": CodexAdapter,
        "ollama_lite": OllamaLiteAdapter,
        "direct": DirectAdapter,
    }
    
    # Try to import optional adapters
    try:
        from ..agents.openclaw_adapter import OpenClawAdapter
        adapters["openclaw"] = OpenClawAdapter
    except ImportError:
        pass
    
    try:
        from ..other_adapters import AutoGPTAdapter, OpenHandsAdapter, CrewAIAdapter, GptmeAdapter
        adapters["autogpt"] = AutoGPTAdapter
        adapters["openhands"] = OpenHandsAdapter
        adapters["crewai"] = CrewAIAdapter
        adapters["gptme"] = GptmeAdapter
    except ImportError:
        pass
    
    adapter_class = adapters.get(backend, DirectAdapter)
    try:
        primary = adapter_class(workspace_path, model=model, provider=provider)
    except TypeError:
        primary = adapter_class(workspace_path)

    failover_cfg = _agent_failover_config(workspace_path)
    if bool(failover_cfg.get("enabled", False)):
        backup_names = [
            str(x).strip().lower()
            for x in (failover_cfg.get("fallback_backends") or [])
            if str(x).strip().lower() and str(x).strip().lower() != str(backend).strip().lower()
        ]
        backups: list[AgentAdapter] = []
        for name in backup_names:
            cls = adapters.get(name)
            if not cls:
                continue
            try:
                try:
                    backups.append(cls(workspace_path))
                except TypeError:
                    backups.append(cls(workspace_path, model=None, provider=None))
            except Exception as exc:
                logger.debug("failed to initialize failover adapter %s: %s", name, exc)
        if backups:
            primary = FallbackAgentAdapter(primary, backups, workspace_path)

    agent_config = _load_agent_config(workspace_path)
    pool_config = agent_config.get("ollama_pool", {})
    if not isinstance(pool_config, dict):
        pool_config = {}
    pool_enabled = bool(pool_config.get("enabled", False))
    pool_mode = str(pool_config.get("mode") or "").strip().lower()
    dynamic_config = agent_config.get("dynamic_ollama", {})
    if not isinstance(dynamic_config, dict):
        dynamic_config = {}
    dynamic_ollama = os.getenv("PARTNER_DYNAMIC_OLLAMA_PROJECT")
    if dynamic_ollama is None:
        dynamic_ollama = str(dynamic_config.get("enabled", "0"))
    dynamic_ollama = dynamic_ollama.strip().lower()
    pool_project_enabled = pool_enabled and pool_mode in {"project", "all"}
    if backend != "ollama_lite" and (
        pool_project_enabled
        or dynamic_ollama in {"1", "true", "on", "enabled", "yes", "auto"}
    ):
        primary = DynamicOllamaProjectAdapter(primary, workspace_path)

    lite_mode = os.getenv("PARTNER_OLLAMA_LITE")
    if lite_mode is None:
        lite_mode = "auto" if pool_enabled and pool_mode in {"lite", "all"} else "off"
    lite_mode = str(lite_mode).strip().lower()
    if backend != "ollama_lite" and lite_mode not in {"0", "false", "off", "disabled", "no"}:
        try:
            if lite_mode == "auto" or lite_mode in {"1", "true", "on", "enabled", "yes"} or OllamaLiteAdapter.is_available():
                return HybridLiteAdapter(primary, OllamaLiteAdapter(workspace_path))
        except Exception:
            pass

    # Ollama 可用时优先使用 Ollama（非降级，是主动选择）
    # 用户可感知：回复前缀 [ollama]
    # 注意：使用 HybridLiteAdapter 包装，确保 batch_plan/action/classify 等
    # 复杂任务走主适配器（HermesAdapter），只有 chat/interaction 走 Ollama
    if backend != "ollama_lite":
        try:
            from ..llm.ollama_probe import is_ollama_available
            if is_ollama_available():
                ollama_adapter = OllamaLiteAdapter(workspace_path)
                logger.info("[ADAPTER] Ollama available, using HybridLiteAdapter (primary=%s, lite=%s)", type(primary).__name__, ollama_adapter.model)
                return HybridLiteAdapter(primary, ollama_adapter)
        except Exception as exc:
            logger.debug("[ADAPTER] Ollama check failed: %s", exc)

    return primary


def list_available_adapters(workspace_path: str) -> list:
    """List user-selectable agent adapters for setup/diagnostics.

    Runtime keeps legacy adapters for backward compatibility, but new CLI setup
    intentionally exposes only the supported production backends.
    """
    all_adapters = [
        ("hermes", "Hermes Agent", "🔮"),
        ("codex", "OpenAI Codex", "⌘"),
        ("openclaw", "OpenClaw (小龙虾)", "🦞"),
    ]
    
    result = []
    for name, display, emoji in all_adapters:
        try:
            adapter = create_adapter(name, workspace_path)
            available = adapter.is_available() if hasattr(adapter, 'is_available') else True
        except:
            available = False
        result.append({
            "name": name,
            "display": display,
            "emoji": emoji,
            "available": available,
        })
    
    return result
