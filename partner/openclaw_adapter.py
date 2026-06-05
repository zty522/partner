"""OpenClaw adapter for Partner.

The integration uses the official OpenClaw CLI as the stable boundary:
`openclaw agent --message ...`. OpenClaw owns its gateway, model credentials,
channels, skills, and session state; Partner only delegates a turn and records
runtime metadata in the same shape as other backends.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from typing import List, Optional

from .adapter import (
    AgentAdapter,
    SearchResult,
    USER_FRIENDLY_PROGRESS_REPLY,
    _project_timeout_sec,
    _refresh_runtime_cost_summary,
)

logger = logging.getLogger(__name__)
_NTFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _openclaw_env() -> dict:
    env = os.environ.copy()
    home = os.path.expanduser("~")
    preferred = [
        os.path.join(home, ".n", "bin"),
        os.path.join(home, ".npm-global", "bin"),
    ]
    current = env.get("PATH", "")
    parts = [p for p in preferred if p and os.path.isdir(p)]
    parts.extend([p for p in current.split(os.pathsep) if p])
    env["PATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env


class OpenClawAdapter(AgentAdapter):
    """Adapter for OpenClaw via its official CLI."""

    def __init__(
        self,
        workspace_path: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self.workspace = workspace_path
        self.model = model
        self.provider = provider

    @staticmethod
    def _candidate_executables() -> list[str]:
        home = os.path.expanduser("~")
        candidates = [
            os.getenv("OPENCLAW_BIN", ""),
            shutil.which("openclaw") or "",
            os.path.join(home, ".npm-global", "bin", "openclaw"),
            os.path.join(home, ".n", "bin", "openclaw"),
            "/usr/local/bin/openclaw",
            "/usr/bin/openclaw",
            os.path.join(os.environ.get("APPDATA", ""), "npm", "openclaw.cmd"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "OpenClaw", "openclaw.exe"),
        ]
        return [p for p in candidates if p]

    @classmethod
    def _openclaw_executable(cls) -> str:
        for path in cls._candidate_executables():
            if os.path.exists(path) or shutil.which(path):
                return path
        return "openclaw"

    @staticmethod
    def _node_version_ok(text: str) -> bool:
        match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
        if not match:
            return False
        major, minor, patch = (int(x) for x in match.groups())
        return major > 22 or (major == 22 and (minor > 19 or (minor == 19 and patch >= 0)))

    @classmethod
    def detect_installation(cls) -> dict:
        executable = cls._openclaw_executable()
        resolved = shutil.which(executable, path=_openclaw_env().get("PATH")) or executable
        issues: list[str] = []
        version = ""
        node_version = ""
        available = False

        try:
            node = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                env=_openclaw_env(),
                creationflags=_NTFLAGS,
            )
            node_version = (node.stdout or node.stderr or "").strip()
            if not cls._node_version_ok(node_version):
                issues.append(f"Node.js 版本过低: {node_version or 'unknown'}，需要 22.19+")
        except Exception as exc:
            issues.append(f"无法检测 Node.js: {exc}")

        try:
            result = subprocess.run(
                [resolved, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                env=_openclaw_env(),
                creationflags=_NTFLAGS,
            )
            output = (result.stdout or result.stderr or "").strip()
            version = output.splitlines()[0] if output else ""
            available = result.returncode == 0 and not issues
            if result.returncode != 0:
                issues.append(output[:240] or f"openclaw exited {result.returncode}")
        except FileNotFoundError:
            issues.append("未检测到 OpenClaw 可执行文件")
            resolved = ""
        except Exception as exc:
            issues.append(f"OpenClaw 检测失败: {exc}")

        config_path = os.getenv("OPENCLAW_CONFIG_PATH") or os.path.join(
            os.path.expanduser("~"),
            ".openclaw",
            "openclaw.json",
        )
        return {
            "available": bool(available),
            "executable": resolved,
            "path": resolved,
            "version": version,
            "node_version": node_version,
            "config_path": config_path if os.path.exists(config_path) else "",
            "issues": issues,
        }

    @classmethod
    def is_available(cls) -> bool:
        return cls.detect_installation()["available"]

    def name(self) -> str:
        return "openclaw"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / 2.2))

    def _log_chat_attempt(self, payload: dict) -> None:
        try:
            log_dir = os.path.join(self.workspace, "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "openclaw_chat.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"failed to write OpenClaw chat log: {exc}")

        try:
            log_dir = os.path.join(self.workspace, "logs")
            os.makedirs(log_dir, exist_ok=True)
            row = {
                "ts": payload.get("ts") or datetime.now().isoformat(),
                "backend": "openclaw",
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
                "reply_preview": payload.get("reply_preview", ""),
                "error": payload.get("error", ""),
            }
            with open(os.path.join(log_dir, "agent_runs.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _refresh_runtime_cost_summary(self.workspace)
        except Exception as exc:
            logger.warning(f"failed to write agent run log: {exc}")

    def _timeout_for(self, purpose: str) -> Optional[int]:
        if purpose == "classify":
            return 45
        if purpose == "interaction":
            return 90
        if purpose == "project":
            return _project_timeout_sec(self.workspace)
        if purpose == "report":
            return 90
        return 180

    def _session_key(self, purpose: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", purpose or "chat").strip("_") or "chat"
        return f"agent:partner:{safe}"

    def _build_prompt(self, message: str, purpose: str) -> str:
        if purpose == "project":
            return (
                f"Partner workspace: {self.workspace}\n"
                "Work inside this workspace when files or commands are needed. "
                "Actually inspect or edit files before summarizing concrete results.\n\n"
                f"{message}"
            )
        return message

    @staticmethod
    def _extract_reply(stdout: str, stderr: str) -> str:
        text = (stdout or "").strip()
        if text:
            try:
                data = json.loads(text)
                for key in ("reply", "message", "content", "text", "output"):
                    value = data.get(key) if isinstance(data, dict) else None
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                if isinstance(data, dict):
                    result = data.get("result")
                    if isinstance(result, dict):
                        payloads = result.get("payloads")
                        if isinstance(payloads, list):
                            texts = [
                                item.get("text", "").strip()
                                for item in payloads
                                if isinstance(item, dict) and item.get("text")
                            ]
                            if texts:
                                return "\n".join(texts).strip()
                        for key in ("reply", "message", "content", "text", "output"):
                            value = result.get(key)
                            if isinstance(value, str) and value.strip():
                                return value.strip()
            except json.JSONDecodeError:
                pass
            return text
        return (stderr or "").strip()

    def search_web(self, query: str) -> List[SearchResult]:
        prompt = (
            f"Search the web for: {query}\n"
            "Return up to 5 items as: title | url | snippet"
        )
        result = self.chat(prompt, purpose="project")
        rows = []
        for line in (result or "").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                rows.append(SearchResult(title=parts[0], url=parts[1], snippet=" | ".join(parts[2:])))
            if len(rows) >= 5:
                break
        snippet = (result or "").strip()
        if len(snippet) > 200:
            snippet = snippet[:199].rstrip() + "..."
        return rows or [SearchResult(title="Search Result", url="", snippet=snippet)]

    def execute_task(self, prompt: str) -> str:
        return self.chat(prompt, purpose="project")

    def chat(self, message: str, max_tokens: int = None, purpose: str = "chat") -> str:
        timeout_sec = self._timeout_for(purpose)
        prompt = self._build_prompt(message or "", purpose)
        cmd = [
            self._openclaw_executable(),
            "agent",
            "--message",
            prompt,
            "--json",
            "--session-key",
            self._session_key(purpose),
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        thinking = os.getenv("PARTNER_OPENCLAW_THINKING", "").strip()
        if thinking:
            cmd.extend(["--thinking", thinking])
        if timeout_sec is not None:
            cmd.extend(["--timeout", str(timeout_sec)])

        started_at = time.time()
        try:
            run_kwargs = {
                "args": cmd,
                "capture_output": True,
                "text": True,
                "cwd": self.workspace,
                "env": _openclaw_env(),
                "creationflags": _NTFLAGS,
            }
            if timeout_sec is not None:
                run_kwargs["timeout"] = timeout_sec + 30
            result = subprocess.run(**run_kwargs)
            elapsed_ms = int((time.time() - started_at) * 1000)
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            reply = self._extract_reply(out, err) if result.returncode == 0 else ""
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "timeout_sec": timeout_sec,
                "elapsed_ms": elapsed_ms,
                "returncode": result.returncode,
                "status": "ok" if result.returncode == 0 and reply else "failed",
                "model": self.model,
                "provider": self.provider,
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(prompt),
                "completion_tokens_est": self._estimate_tokens(reply),
                "total_tokens_est": self._estimate_tokens(prompt) + self._estimate_tokens(reply),
                "stdout_preview": out[:500],
                "stderr_preview": err[:500],
                "reply_preview": reply[:500],
                "message_preview": prompt[:500],
            })
            if result.returncode == 0 and reply:
                return reply
            if err or out:
                logger.warning(f"openclaw agent exit {result.returncode}: {(err or out)[:240]}")
            return USER_FRIENDLY_PROGRESS_REPLY
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
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(prompt),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(prompt),
                "message_preview": prompt[:500],
            })
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
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(prompt),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(prompt),
                "error": "openclaw executable not found",
                "message_preview": prompt[:500],
            })
            return USER_FRIENDLY_PROGRESS_REPLY
        except Exception as exc:
            self._log_chat_attempt({
                "ts": datetime.now().isoformat(),
                "purpose": purpose,
                "timeout_sec": timeout_sec,
                "elapsed_ms": int((time.time() - started_at) * 1000),
                "returncode": None,
                "status": "exception",
                "model": self.model,
                "provider": self.provider,
                "message_chars": len(message or ""),
                "prompt_tokens_est": self._estimate_tokens(prompt),
                "completion_tokens_est": 0,
                "total_tokens_est": self._estimate_tokens(prompt),
                "error": str(exc),
                "message_preview": prompt[:500],
            })
            logger.warning(f"openclaw agent exception: {exc}")
            return USER_FRIENDLY_PROGRESS_REPLY
