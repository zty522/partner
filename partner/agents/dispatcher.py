"""Agent Dispatcher — unified invocation for any registered agent.

Supports invocation methods:
- CLI: subprocess.run(command)
- HTTP: send request to REST endpoint
- Python API: direct import and call
- MCP: MCP protocol (future/stub)
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from .manifest import AgentManifest
from .registry import AgentRegistry


@dataclass
class AgentTask:
    """Standardized task definition for any agent."""
    agent: str  # Agent name
    task: str  # Task description (natural language)
    parameters: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)  # working_dir, files, env
    callback_url: str = ""  # Optional callback for async results

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentResult:
    """Standardized result from any agent."""
    status: str  # "success", "error", "partial"
    output: dict  # type, files, text
    error: str = ""
    metadata: dict = field(default_factory=dict)  # duration, model_used, stats

    def to_dict(self) -> dict:
        return asdict(self)


# ── Credential injection cache ──

_hermes_api_key_cache: str | None = None


def _inject_hermes_api_key(env: dict) -> None:
    """Try to inject Hermes' resolved API key into the subprocess environment.

    Attempts in order:
    1. os.environ['DEEPSEEK_API_KEY'] (already tried by caller)
    2. Read from bash login shell (sources .bashrc which contains the export)
    3. Fallback: parse 'hermes dump --show-keys' output (may be redacted)
    The result is cached so the subprocess is only called once per process lifetime.
    """
    global _hermes_api_key_cache
    if _hermes_api_key_cache is None:
        # Try bash login shell first (reliable: sources .bashrc/.profile)
        try:
            r = subprocess.run(
                ["bash", "-lic", 'echo "$DEEPSEEK_API_KEY"'],
                capture_output=True, text=True, timeout=10,
            )
            key = r.stdout.strip() if r.returncode == 0 else ""
            if key and key.startswith("sk-"):
                _hermes_api_key_cache = key
        except Exception:
            pass

        # Fallback: try 'hermes dump --show-keys'
        if not _hermes_api_key_cache:
            try:
                r = subprocess.run(
                    ["hermes", "dump", "--show-keys"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    for line in r.stdout.splitlines():
                        line = line.strip()
                        if "deepseek" in line.lower() and "sk-" in line:
                            parts = line.split()
                            for p in parts:
                                if p.startswith("sk-") and "..." not in p:  # reject masked keys
                                    _hermes_api_key_cache = p
                                    break
                            if _hermes_api_key_cache:
                                break
            except Exception:
                pass

        if _hermes_api_key_cache is None:
            _hermes_api_key_cache = ""  # Sentinel: don't retry

    if _hermes_api_key_cache:
        env.setdefault("OPENAI_API_KEY", _hermes_api_key_cache)
        env.setdefault("DEEPSEEK_API_KEY", _hermes_api_key_cache)
        # Also set K_CODEX if it exists in parent env (for backward compat)
        codex_val = os.environ.get("K_CODEX") or ""
        if codex_val and not env.get("K_CODEX"):
            env["K_CODEX"] = codex_val


class AgentDispatcher:
    """Dispatches tasks to registered agents through unified interface."""

    def __init__(self, registry: AgentRegistry):
        self._registry = registry

    async def dispatch(self, task: AgentTask) -> AgentResult:
        """Dispatch a task to the specified agent."""
        manifest = self._registry.get_agent(task.agent)
        if not manifest:
            return AgentResult(
                status="error",
                error=f"Agent '{task.agent}' not found",
                output={},
            )

        if manifest.endpoint_type == "cli":
            return await self._dispatch_cli(manifest, task)
        elif manifest.endpoint_type == "http":
            return await self._dispatch_http(manifest, task)
        elif manifest.endpoint_type == "python_api":
            return await self._dispatch_python(manifest, task)
        elif manifest.endpoint_type == "mcp":
            return await self._dispatch_mcp(manifest, task)
        else:
            return AgentResult(
                status="error",
                error=f"Unknown endpoint type: {manifest.endpoint_type}",
                output={},
            )

    async def _dispatch_cli(
        self, manifest: AgentManifest, task: AgentTask
    ) -> AgentResult:
        """Invoke agent via CLI subprocess.

        Supports:
        - subcommand: optional subcommand (e.g. "exec" for "cytobridge-agent exec")
        - {placeholder} substitution in args from task.parameters
        - task text appended as final positional arg
        """
        start = time.time()

        # Build command
        cmd_config = manifest.endpoint_config
        command = cmd_config.get("command", "")
        subcommand = cmd_config.get("subcommand", "")
        args = list(cmd_config.get("args", []))

        if not command:
            return AgentResult(
                status="error",
                error="CLI endpoint missing 'command' in endpoint_config",
                output={},
            )

        # Check if the CLI binary is actually installed before attempting dispatch
        main_cmd = command.split()[0]
        resolved = shutil.which(main_cmd)
        if not resolved:
            # Fallback: search common conda/miniconda bin directories — the
            # Partner process may not have conda in PATH when launched from
            # VS Code or a non-conda shell context
            _CONDA_BIN_DIRS = [
                os.path.expanduser("~/miniconda3/bin"),
                os.path.expanduser("~/anaconda3/bin"),
                os.path.expanduser("~/miniforge3/bin"),
                os.path.expanduser("~/mambaforge/bin"),
                os.path.expanduser("~/.local/bin"),
                "/opt/conda/bin",
                "/usr/local/miniconda3/bin",
            ]
            for _bin_dir in _CONDA_BIN_DIRS:
                _candidate = os.path.join(_bin_dir, main_cmd)
                if os.path.isfile(_candidate) and os.access(_candidate, os.X_OK):
                    resolved = _candidate
                    break

        if not resolved:
            desc = (manifest.description or "").strip()
            install_hint = f"Installation: {desc}" if desc else f"Is '{manifest.name}' installed?"
            return AgentResult(
                status="error",
                error=f"Agent CLI not found: '{main_cmd}'. {install_hint}",
                output={},
            )

        # Use resolved full path for the command, not just the basename
        command = resolved

        # Build full command: [command] + [preamble_args] + [subcommand] + [args]
        full_cmd = [command]

        # Substitute {placeholders} from task.parameters and context
        all_vars = dict(task.parameters or {})
        all_vars.update({k: str(v) for k, v in (task.context or {}).items()})

        # Pre-populate LLM credential placeholders from parent env so they
        # can be substituted into preamble_args and args before the command is built.
        # These are also injected into the subprocess env later.
        _llm_env_api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
        _llm_env_base_url = os.environ.get("OPENAI_BASE_URL") or ""
        _llm_env_model = os.environ.get("HERMES_MODEL") or os.environ.get("HERMES_DEFAULT_MODEL") or ""
        _llm_env_provider = os.environ.get("PARTNER_PROVIDER") or os.environ.get("HERMES_PROVIDER") or ""
        # Derive defaults from API key pattern when env vars are not set.
        # Supports: DeepSeek (sk-d*), OpenAI (sk-*), Anthropic (sk-ant-*), and custom.
        if _llm_env_api_key and not _llm_env_base_url:
            if _llm_env_api_key.startswith("sk-d"):
                _llm_env_base_url = "https://api.deepseek.com"
            elif _llm_env_api_key.startswith("sk-ant"):
                _llm_env_base_url = "https://api.anthropic.com"
            elif _llm_env_api_key.startswith("sk-"):
                _llm_env_base_url = "https://api.openai.com"
        if _llm_env_api_key and not _llm_env_model:
            if _llm_env_api_key.startswith("sk-d"):
                _llm_env_model = "deepseek-v4-flash"
            elif _llm_env_api_key.startswith("sk-ant"):
                _llm_env_model = "claude-sonnet-4-20250514"
            elif _llm_env_api_key.startswith("sk-"):
                _llm_env_model = "gpt-4o"
        if _llm_env_api_key and not _llm_env_provider:
            if _llm_env_api_key.startswith("sk-d"):
                _llm_env_provider = "deepseek"
            elif _llm_env_api_key.startswith("sk-ant"):
                _llm_env_provider = "anthropic"
            elif _llm_env_api_key.startswith("sk-"):
                _llm_env_provider = "openai"
        if _llm_env_api_key:
            all_vars["__llm_api_key__"] = _llm_env_api_key
        if _llm_env_base_url:
            all_vars["__llm_base_url__"] = _llm_env_base_url
        if _llm_env_model:
            all_vars["__llm_model__"] = _llm_env_model
        if _llm_env_provider:
            all_vars["__llm_provider__"] = _llm_env_provider
        if os.environ.get("DEEPSEEK_API_KEY"):
            all_vars["__llm_deepseek_key__"] = os.environ["DEEPSEEK_API_KEY"]

        # preamble_args go before the subcommand (e.g., --llm-base-url for cellcompass)
        preamble_args = list(cmd_config.get("preamble_args", []))
        resolved_preamble = []
        for arg in preamble_args:
            for key, val in all_vars.items():
                placeholder = "{" + key + "}"
                if placeholder in arg:
                    arg = arg.replace(placeholder, str(val))
                    break
            resolved_preamble.append(arg)
        full_cmd.extend(resolved_preamble)

        if subcommand:
            full_cmd.append(subcommand)

        # Substitute {placeholders} in args from task.parameters and context
        resolved_args = []
        for arg in args:
            for key, val in all_vars.items():
                placeholder = "{" + key + "}"
                if placeholder in arg:
                    arg = arg.replace(placeholder, str(val))
                    break
            resolved_args.append(arg)
        # Normalize {output} placeholder: if output path is a directory (no filename),
        # append a default output filename so tools like pandoc don't try to write
        # to a directory path.
        for i, _arg in enumerate(resolved_args):
            if "{output}" in str(args[i]) if i < len(args) else False:
                _out_val = resolved_args[i]
                if _out_val and os.path.isdir(_out_val):
                    resolved_args[i] = os.path.join(_out_val, "output.pdf")
                elif _out_val and not os.path.splitext(_out_val)[1]:
                    # No extension — likely a directory path
                    resolved_args[i] = os.path.join(_out_val, "output.pdf")
        full_cmd.extend(resolved_args)

        # Append the task text as final positional arg (skip for "run" subcommand
        # where the positional is the input path, not free-form task text)
        if task.task and subcommand != "run":
            full_cmd.append(task.task)

        # Prepare working directory
        cwd = task.context.get("working_dir") or os.getcwd()

        # Prepare environment
        env = os.environ.copy()
        task_env = task.context.get("env", {})
        if isinstance(task_env, dict):
            env.update(task_env)
        env["PARTNER_AGENT_TASK"] = task.task
        env["PARTNER_AGENT_PARAMS"] = json.dumps(task.parameters)
        if task.context.get("files"):
            env["PARTNER_AGENT_FILES"] = json.dumps(task.context["files"])

        # OpenBLAS / MKL / OMP memory safety — limit threads to prevent
        # memory allocation failures in subprocesses with heavy imports
        env.setdefault("OPENBLAS_NUM_THREADS", "2")
        env.setdefault("MKL_NUM_THREADS", "2")
        env.setdefault("OMP_NUM_THREADS", "2")
        env.setdefault("NUMEXPR_NUM_THREADS", "2")

        # Inject Partner's API credentials into the subprocess environment
        # so all specialized agents share Partner's configured API key and endpoint
        _INJECTED_ENV_KEYS = [
            "OPENAI_API_KEY", "OPENAI_BASE_URL",
            "DEEPSEEK_API_KEY",
            "ANTHROPIC_API_KEY",
            "PARTNER_PROVIDER",
            "K_CODEX",
            "HERMES_MODEL", "HERMES_PROVIDER",
        ]
        for key in _INJECTED_ENV_KEYS:
            val = os.environ.get(key) or ""
            if val:
                env.setdefault(key, val)

        # If we have DEEPSEEK_API_KEY but not OPENAI_API_KEY, also set OPENAI_API_KEY
        # (most agents read OPENAI_API_KEY, not DEEPSEEK_API_KEY)
        if env.get("DEEPSEEK_API_KEY") and not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = env["DEEPSEEK_API_KEY"]
        # Always inject OPENAI_BASE_URL when using DeepSeek key — agents default to
        # api.openai.com which rejects DeepSeek keys with 401
        if env.get("OPENAI_API_KEY", "").startswith("sk-d") and not env.get("OPENAI_BASE_URL"):
            env["OPENAI_BASE_URL"] = "https://api.deepseek.com"

        # ── no_proxy for LLM endpoint ──
        # Now that OPENAI_BASE_URL is fully resolved (from env, DeepSeek detection,
        # or workspace config), add the LLM host to no_proxy so httpx/requests
        # connects directly to the LLM API, bypassing the http_proxy that may not
        # support long-lived streaming connections.
        _proxy_hosts = set()
        for _url_candidate in [
            env.get("OPENAI_BASE_URL", ""),
            env.get("HERMES_BASE_URL", ""),
        ]:
            if _url_candidate:
                try:
                    from urllib.parse import urlparse
                    _parsed = urlparse(_url_candidate)
                    if _parsed.hostname:
                        _proxy_hosts.add(_parsed.hostname)
                except Exception:
                    pass
        _proxy_hosts.add("localhost")
        _proxy_hosts.add("127.0.0.1")
        _existing_no_proxy = os.environ.get("no_proxy", "").strip()
        if _existing_no_proxy:
            for _h in _existing_no_proxy.split(","):
                _proxy_hosts.add(_h.strip())
        env["no_proxy"] = ",".join(sorted(_proxy_hosts))
        env["NO_PROXY"] = env["no_proxy"]

        # Auto-populate LLM credential placeholders for {llm_*} substitutions in args.
        # This lets agent manifests use {llm_api_key}, {llm_base_url}, {llm_model},
        # {llm_provider} in their endpoint_config.args, and the dispatcher fills them
        # from Partner's own configuration automatically.
        # These are prefixed with __ to avoid collision with task.parameters keys.
        # NOTE: Some of these may already be set from os.environ above.
        # We only overwrite if the env-injected value is more specific (e.g. OPENAI_BASE_URL
        # was resolved after env prep).
        if "OPENAI_API_KEY" in env and "__llm_api_key__" not in all_vars:
            all_vars["__llm_api_key__"] = env["OPENAI_API_KEY"]
        if "OPENAI_BASE_URL" in env:
            all_vars["__llm_base_url__"] = env["OPENAI_BASE_URL"]
        if "HERMES_MODEL" in env and "__llm_model__" not in all_vars:
            all_vars["__llm_model__"] = env["HERMES_MODEL"]
        if "PARTNER_PROVIDER" in env and "__llm_provider__" not in all_vars:
            all_vars["__llm_provider__"] = env["PARTNER_PROVIDER"]
        if "DEEPSEEK_API_KEY" in env and "__llm_deepseek_key__" not in all_vars:
            all_vars["__llm_deepseek_key__"] = env["DEEPSEEK_API_KEY"]

        # Also try to resolve model config from Partner's workspace config
        _model_from_config = None
        _provider_from_config = None
        _base_url_from_config = None
        try:
            from ..agent_config_sync import desired_hermes_model_config
            config = desired_hermes_model_config(cwd)
            if config.get("model") and "__llm_model__" not in all_vars:
                all_vars["__llm_model__"] = config["model"]
            if config.get("provider") and "__llm_provider__" not in all_vars:
                all_vars["__llm_provider__"] = config["provider"]
            if config.get("base_url") and "__llm_base_url__" not in all_vars:
                all_vars["__llm_base_url__"] = config["base_url"]
        except Exception:
            pass

        # Also try reading from Partner's agent API config
        try:
            ws_dir = task.context.get("working_dir", "") or os.getcwd()
            from ..agent_config_sync import desired_hermes_model_config
            config = desired_hermes_model_config(ws_dir)
            if config.get("api_key") and not env.get("OPENAI_API_KEY"):
                env["OPENAI_API_KEY"] = config["api_key"]
            if config.get("base_url") and not env.get("OPENAI_BASE_URL"):
                env["OPENAI_BASE_URL"] = config["base_url"]
            if config.get("provider") and not env.get("PARTNER_PROVIDER"):
                env["PARTNER_PROVIDER"] = config["provider"]
        except Exception:
            pass

        # Fallback: try to resolve API key from Hermes' credential system
        # (e.g., env vars set via shell startup, keychain, or .env file)
        if not env.get("DEEPSEEK_API_KEY") and not env.get("OPENAI_API_KEY"):
            try:
                _inject_hermes_api_key(env)
            except Exception:
                pass

        # Build input payload for stdin
        stdin_payload = json.dumps({
            "task": task.task,
            "parameters": task.parameters,
            "context": task.context,
        })

        try:
            r = subprocess.run(
                full_cmd,
                input=stdin_payload,
                capture_output=True,
                text=True,
                timeout=manifest.timeout,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError:
            return AgentResult(
                status="error",
                error=f"Command not found: {command}. Is {manifest.name} installed?",
                output={},
                metadata={"duration": time.time() - start, "command": command},
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                status="error",
                error=f"Agent '{manifest.name}' timed out after {manifest.timeout}s",
                output={},
                metadata={"duration": time.time() - start, "timeout": manifest.timeout},
            )
        except Exception as exc:
            return AgentResult(
                status="error",
                error=str(exc),
                output={},
                metadata={"duration": time.time() - start},
            )

        duration = time.time() - start

        # Try to parse stdout as JSON result
        output: dict = {}
        parse_error = ""
        if r.stdout.strip():
            try:
                output = json.loads(r.stdout)
            except json.JSONDecodeError:
                # Plain text output
                output = {"type": "text", "text": r.stdout}

        result = AgentResult(
            # If stdout has content, treat as success even with non-zero exit code.
            # Some agents (e.g., cytobridge-agent) log httpx INFO to stderr and
            # return non-zero even after successful execution (the INFO logs
            # are not actual errors). Stdout content is the authoritative signal.
            status="success" if r.stdout.strip() or r.returncode == 0 else "error",
            output=output,
            error=r.stderr.strip() if r.returncode != 0 else "",
            metadata={
                "duration": round(duration, 3),
                "returncode": r.returncode,
                "command": " ".join(full_cmd),
                "parse_error": parse_error,
            },
        )

        return result

    async def _dispatch_http(
        self, manifest: AgentManifest, task: AgentTask
    ) -> AgentResult:
        """Invoke agent via HTTP REST API."""
        import urllib.request
        import urllib.error

        start = time.time()
        url = manifest.endpoint_config.get("url", "")
        method = manifest.endpoint_config.get("method", "POST").upper()
        headers = dict(manifest.endpoint_config.get("headers", {}))

        if not url:
            return AgentResult(
                status="error",
                error="HTTP endpoint missing 'url' in endpoint_config",
                output={},
            )

        payload = {
            "task": task.task,
            "parameters": task.parameters,
            "context": task.context,
        }

        data = json.dumps(payload).encode("utf-8")
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=manifest.timeout) as resp:
                response_data = resp.read().decode("utf-8")
                status_code = resp.status
        except urllib.error.HTTPError as e:
            duration = time.time() - start
            return AgentResult(
                status="error",
                error=f"HTTP {e.code}: {e.reason}",
                output={},
                metadata={"duration": round(duration, 3), "status_code": e.code},
            )
        except urllib.error.URLError as e:
            duration = time.time() - start
            return AgentResult(
                status="error",
                error=f"Connection failed: {e.reason}",
                output={},
                metadata={"duration": round(duration, 3)},
            )
        except Exception as exc:
            duration = time.time() - start
            return AgentResult(
                status="error",
                error=str(exc),
                output={},
                metadata={"duration": round(duration, 3)},
            )

        duration = time.time() - start

        # Try to parse response JSON
        output: dict = {}
        try:
            output = json.loads(response_data)
        except json.JSONDecodeError:
            output = {"type": "text", "text": response_data}

        return AgentResult(
            status="success" if 200 <= status_code < 300 else "error",
            output=output,
            error="" if 200 <= status_code < 300 else f"HTTP {status_code}",
            metadata={
                "duration": round(duration, 3),
                "status_code": status_code,
                "url": url,
            },
        )

    async def _dispatch_python(
        self, manifest: AgentManifest, task: AgentTask
    ) -> AgentResult:
        """Invoke agent via direct Python import.

        The endpoint_config must specify:
        - module: Python module path (e.g. "cytobridge.run")
        - function: function name to call (default: "run")
        """
        start = time.time()
        module_path = manifest.endpoint_config.get("module", "")
        func_name = manifest.endpoint_config.get("function", "run")

        if not module_path:
            return AgentResult(
                status="error",
                error="python_api endpoint missing 'module' in endpoint_config",
                output={},
            )

        try:
            import importlib
            mod = importlib.import_module(module_path)
            func = getattr(mod, func_name, None)
            if func is None:
                return AgentResult(
                    status="error",
                    error=f"Function '{func_name}' not found in module '{module_path}'",
                    output={},
                    metadata={"duration": time.time() - start},
                )

            # Call synchronously; wrap if async
            result = func(
                task=task.task,
                parameters=task.parameters,
                context=task.context,
            )

            duration = time.time() - start
            if isinstance(result, dict):
                return AgentResult(
                    status=result.get("status", "success"),
                    output=result.get("output", {}),
                    error=result.get("error", ""),
                    metadata={
                        "duration": round(duration, 3),
                        "module": module_path,
                        "function": func_name,
                    },
                )
            else:
                return AgentResult(
                    status="success",
                    output={"type": "text", "text": str(result)},
                    metadata={
                        "duration": round(duration, 3),
                        "module": module_path,
                        "function": func_name,
                    },
                )

        except Exception as exc:
            duration = time.time() - start
            return AgentResult(
                status="error",
                error=str(exc),
                output={},
                metadata={"duration": round(duration, 3)},
            )

    async def _dispatch_mcp(
        self, manifest: AgentManifest, task: AgentTask
    ) -> AgentResult:
        """Invoke agent via MCP protocol (stub for future implementation)."""
        return AgentResult(
            status="error",
            error="MCP endpoint type is not yet implemented",
            output={},
            metadata={"note": "MCP support coming soon"},
        )

    # ── Auto-agent selection ──

    def select_agent(self, capability_hints: list[str] | None = None, task_description: str = "") -> str | None:
        """Auto-select the best agent for a task based on capability hints or task description.
        
        Priority:
        1. Match by capability hints (exact capability match)
        2. Match by keyword in task description against agent capabilities/description
        3. Return the first available general-purpose agent
        
        Args:
            capability_hints: List of preferred capabilities (e.g. ["coding", "data_analysis"])
            task_description: Natural language task description for keyword matching
        
        Returns:
            Agent name (str) or None if no agents registered
        """
        agents = self._registry.list_agents()
        if not agents:
            return None

        # 1. Exact capability match
        if capability_hints:
            for cap in capability_hints:
                matches = self._registry.find_by_capability(cap)
                if matches:
                    return matches[0].name

        # 2. Keyword matching on task description
        if task_description:
            keywords = task_description.lower().split()
            scored = []
            for agent in agents:
                score = 0
                agent_text = f"{agent.name} {agent.description} {' '.join(agent.capabilities)}".lower()
                for kw in keywords:
                    if kw in agent_text:
                        score += 1
                if score > 0:
                    scored.append((score, agent.name))
            if scored:
                scored.sort(key=lambda x: -x[0])
                return scored[0][1]

        # 3. Fallback: first general agent (not 'specialized')
        for agent in agents:
            if agent.endpoint_config.get("category", "general") != "specialized":
                return agent.name

        # 4. Last resort: first agent
        return agents[0].name


# ── Sync convenience wrapper ──

def dispatch_sync(task: AgentTask, workspace: str | None = None) -> AgentResult:
    """Synchronous convenience wrapper around AgentDispatcher.dispatch.

    Usage:
        result = dispatch_sync(AgentTask(agent="hermes", task="analyze data"))
    """
    registry = AgentRegistry(workspace=workspace)
    dispatcher = AgentDispatcher(registry)
    return asyncio.run(dispatcher.dispatch(task))
