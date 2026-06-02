"""Agent Adapter Layer - unified interface for different agent backends."""

import json
import logging
import os
import re
import shutil
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import List, Optional


INTERNAL_PROGRESS_SENTINEL = "__PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE__"
# Backward-compatible name used by older executor/bridge code. This is an
# internal sentinel and must never be pushed to users.
USER_FRIENDLY_PROGRESS_REPLY = INTERNAL_PROGRESS_SENTINEL


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


class HermesAdapter(AgentAdapter):
    """Adapter for Hermes Agent via cronjob/subprocess."""
    
    def __init__(self, workspace_path: str, model: Optional[str] = None,
                 provider: Optional[str] = None):
        self.workspace = workspace_path
        self.model = model
        self.provider = provider

    def _log_chat_attempt(self, payload: dict):
        """Persist Hermes chat attempt metadata for timeout debugging."""
        try:
            log_dir = os.path.join(self.workspace, "logs")
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
            log_dir = os.path.join(self.workspace, "logs")
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
            return max(5, int(os.getenv("PARTNER_HERMES_MAX_RESUME_USES", "80")))
        except Exception:
            return 80

    def _should_resume_session(self, purpose: str) -> bool:
        configured = os.getenv("PARTNER_HERMES_RESUME_PURPOSES", "project")
        purposes = {p.strip() for p in configured.split(",") if p.strip()}
        return purpose in purposes or "*" in purposes

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
            return max(2000, int(os.getenv("PARTNER_HERMES_MAX_RESUME_CONTEXT_TOKENS", "30000")))
        except Exception:
            return 30000

    def _build_hermes_env(self) -> dict:
        """Give Hermes a writable per-instance home under the workspace."""
        env = os.environ.copy()
        hermes_home = os.path.join(self.workspace, "system", "hermes_home")
        hermes_logs = os.path.join(hermes_home, "logs")
        os.makedirs(hermes_logs, exist_ok=True)
        env["HOME"] = hermes_home
        env["HERMES_HOME"] = hermes_home
        env["XDG_STATE_HOME"] = os.path.join(hermes_home, ".local", "state")
        env["XDG_CACHE_HOME"] = os.path.join(hermes_home, ".cache")
        env["XDG_CONFIG_HOME"] = os.path.join(hermes_home, ".config")
        os.makedirs(env["XDG_STATE_HOME"], exist_ok=True)
        os.makedirs(env["XDG_CACHE_HOME"], exist_ok=True)
        os.makedirs(env["XDG_CONFIG_HOME"], exist_ok=True)
        self._sync_hermes_runtime_files(hermes_home)
        return env

    def _sync_hermes_runtime_files(self, hermes_home: str):
        """Mirror the user's Hermes config/auth into the writable instance home."""
        source_home = os.path.expanduser("~/.hermes")
        if not os.path.isdir(source_home):
            return
        for filename in ("config.yaml", "auth.json", ".env"):
            source_path = os.path.join(source_home, filename)
            target_path = os.path.join(hermes_home, filename)
            if not os.path.exists(source_path):
                continue
            try:
                source_mtime = os.path.getmtime(source_path)
                target_mtime = os.path.getmtime(target_path) if os.path.exists(target_path) else -1
                if source_mtime <= target_mtime:
                    continue
                with open(source_path, "rb") as src, open(target_path, "wb") as dst:
                    dst.write(src.read())
            except OSError as exc:
                logger.warning(f"failed to mirror Hermes runtime file {filename}: {exc}")

    @staticmethod
    def _candidate_executables() -> list:
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")
        home = os.path.expanduser("~")
        candidates = [
            os.getenv("HERMES_BIN", ""),
            shutil.which("hermes") or "",
            os.path.join(home, ".local", "bin", "hermes"),
            "/usr/local/bin/hermes",
            "/home/ubuntu/.local/bin/hermes",
            "/home/os/.local/bin/hermes",
            os.path.join(home, ".hermes", "hermes-agent", "venv", "bin", "hermes"),
            os.path.join(appdata, "Python", "Python314", "Scripts", "hermes.exe"),
            os.path.join(appdata, "Python", "Python313", "Scripts", "hermes.exe"),
            os.path.join(appdata, "Python", "Python312", "Scripts", "hermes.exe"),
            os.path.join(appdata, "npm", "hermes"),
            os.path.join(appdata, "npm", "hermes.cmd"),
            os.path.join(localappdata, "hermes", "hermes-agent", "venv", "Scripts", "hermes"),
            os.path.join(localappdata, "hermes", "hermes-agent", "venv", "Scripts", "hermes.exe"),
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
    
    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        """Chat via hermes subprocess."""
        import subprocess
        import time

        cmd = [self._hermes_executable(), "chat", "-q", message, "-Q"]
        session_id = self._read_session_id(purpose)
        if session_id:
            cmd.extend(["--resume", session_id])
        if self.model:
            cmd.extend(["-m", self.model])
        if self.provider:
            cmd.extend(["--provider", self.provider])
        if purpose == "classify":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "interaction":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])
        elif purpose == "project":
            # project 需要 terminal/file/web 工具来实际执行代码和操作文件
            cmd.extend(["-t", "terminal,file,web", "--ignore-rules"])
        elif purpose == "report":
            cmd.extend(["-t", "", "--ignore-rules", "--max-turns", "1"])

        timeout_sec = 120
        max_retries = 2
        if purpose == "classify":
            timeout_sec = 45
            max_retries = 0
        elif purpose == "interaction":
            timeout_sec = 90
            max_retries = 0
        elif purpose == "project":
            timeout_sec = None  # 项目执行交给 agent 跑完，不做本地超时降级
            max_retries = 0
        elif purpose == "report":
            timeout_sec = 90
            max_retries = 0

        for attempt in range(max_retries + 1):
            started_at = time.time()
            try:
                run_kwargs = {
                    "args": cmd,
                    "capture_output": True,
                    "text": True,
                    "cwd": self.workspace,
                    "env": self._build_hermes_env(),
                }
                if timeout_sec is not None:
                    run_kwargs["timeout"] = timeout_sec
                result = subprocess.run(**run_kwargs)
                out = result.stdout.strip()
                err = (result.stderr or "").strip()
                elapsed_ms = int((time.time() - started_at) * 1000)
                combined = f"{out}\n{err}"
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


class CodexAdapter(AgentAdapter):
    """Adapter for OpenAI Codex CLI."""

    def __init__(self, workspace_path: str, model: Optional[str] = None,
                 provider: Optional[str] = None):
        self.workspace = workspace_path
        self.model = model
        self.provider = provider

    def _log_chat_attempt(self, payload: dict):
        try:
            log_dir = os.path.join(self.workspace, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "codex_chat.jsonl")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"failed to write Codex chat log: {exc}")
        try:
            log_dir = os.path.join(self.workspace, "logs")
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
        except Exception as exc:
            logger.warning(f"failed to write agent run log: {exc}")

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / 2.2))

    def name(self) -> str:
        return "codex"

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

        timeout_sec = 180
        if purpose == "classify":
            timeout_sec = 45
        elif purpose == "interaction":
            timeout_sec = 60
        elif purpose == "project":
            timeout_sec = None
        elif purpose == "report":
            timeout_sec = 90

        out_dir = os.path.join(self.workspace, "99_temp")
        os.makedirs(out_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="codex_last_", suffix=".txt", dir=out_dir, delete=False) as tf:
            output_path = tf.name

        cmd = [
            "codex", "exec",
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
                "cwd": self.workspace,
            }
            if timeout_sec is not None:
                run_kwargs["timeout"] = timeout_sec
            result = subprocess.run(**run_kwargs)
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


def create_adapter(backend: str, workspace_path: str, model: Optional[str] = None,
                   provider: Optional[str] = None) -> AgentAdapter:
    """Factory function to create the appropriate adapter."""
    adapters = {
        "hermes": HermesAdapter,
        "codex": CodexAdapter,
        "direct": DirectAdapter,
    }
    
    # Try to import optional adapters
    try:
        from .openclaw_adapter import OpenClawAdapter
        adapters["openclaw"] = OpenClawAdapter
    except ImportError:
        pass
    
    try:
        from .other_adapters import AutoGPTAdapter, OpenHandsAdapter, CrewAIAdapter, GptmeAdapter
        adapters["autogpt"] = AutoGPTAdapter
        adapters["openhands"] = OpenHandsAdapter
        adapters["crewai"] = CrewAIAdapter
        adapters["gptme"] = GptmeAdapter
    except ImportError:
        pass
    
    adapter_class = adapters.get(backend, DirectAdapter)
    try:
        return adapter_class(workspace_path, model=model, provider=provider)
    except TypeError:
        return adapter_class(workspace_path)


def list_available_adapters(workspace_path: str) -> list:
    """List all available agent adapters."""
    all_adapters = [
        ("hermes", "Hermes Agent", "🔮"),
        ("openclaw", "OpenClaw (小龙虾)", "🦞"),
        ("crewai", "CrewAI", "👥"),
        ("autogpt", "AutoGPT", "🤖"),
        ("openhands", "OpenHands", "👐"),
        ("gptme", "gptme", "💻"),
        ("codex", "OpenAI Codex", "⚡"),
        ("claude_code", "Claude Code", "🧠"),
        ("direct", "Direct (no agent)", "📌"),
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
