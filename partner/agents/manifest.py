"""Agent Manifest model and validation.

Any agent (Hermes, OpenClaw, Codex, CytoBridge, etc.) describes itself
using this manifest, which Partner uses for discovery and invocation.
"""

import json
import os
from dataclasses import dataclass, field

@dataclass
class AgentManifest:
    """Standardized agent description.

    Any agent (Hermes, OpenClaw, Codex, CytoBridge, etc.) describes itself
    using this manifest, which Partner uses for discovery and invocation.
    """
    name: str  # e.g. "cytobridge", "hermes"
    version: str  # e.g. "1.0.0"
    description: str  # Human-readable description
    capabilities: list[str]  # e.g. ["trajectory_inference", "cell_dynamics"]
    input_formats: list[str]  # e.g. ["h5ad", "loom", "csv"]
    output_formats: list[str]  # e.g. ["h5ad", "pdf", "png", "json"]
    endpoint_type: str  # "cli", "http", "mcp", "python_api"
    endpoint_config: dict  # Depends on type
    timeout: int = 300  # Default timeout in seconds
    health_check_cmd: str = ""  # Command to check if agent is available
    install_info: dict = field(default_factory=dict)  # Auto-install instructions

    @classmethod
    def from_dict(cls, data: dict) -> 'AgentManifest':
        """Create manifest from a dictionary."""
        # ── Detect discoverer format (no standard endpoint_config) ──
        _has_standard_format = bool(data.get("endpoint_config")) or data.get("endpoint_type") == "http"
        _has_discoverer_format = not _has_standard_format and isinstance(data.get("install"), dict)

        if _has_discoverer_format:
            # Auto-convert discoverer format to standard AgentManifest format
            data = cls._convert_discoverer_format(data)

        # Resolve timeout: prefer top-level, fallback to endpoint_config.timeout,
        # then to default 300. This handles manifests where auto-discovery
        # stored timeout inside endpoint_config instead of at the top level.
        _timeout = data.get("timeout")
        if _timeout is None:
            _timeout = dict(data.get("endpoint_config", {})).get("timeout", 300)
        return cls(
            name=data.get("name", ""),
            version=data.get("version", ""),
            description=data.get("description", ""),
            capabilities=list(data.get("capabilities", [])),
            input_formats=list(data.get("input_formats", [])),
            output_formats=list(data.get("output_formats", [])),
            endpoint_type=data.get("endpoint_type", "cli"),
            endpoint_config=dict(data.get("endpoint_config", {})),
            timeout=int(_timeout),
            health_check_cmd=str(data.get("health_check_cmd", "")),
            install_info=dict(data.get("install_info", {})),
        )

    @classmethod
    def _convert_discoverer_format(cls, data: dict) -> dict:
        """Convert auto-discoverer format manifest to standard AgentManifest format."""
        name = str(data.get("name", ""))
        result = dict(data)  # shallow copy

        # ── Extract command name ──
        command = ""

        # Method 1: from test.command (first word before space/pipe/redirect)
        test_cmd = data.get("test", {}).get("command", "")
        if test_cmd:
            first_word = test_cmd.split()[0] if test_cmd.split() else ""
            if first_word and first_word not in ("python", "python3", "echo", "time") and not first_word.startswith("$"):
                command = first_word

        # Method 2: from install.command (last non-flag word)
        if not command:
            install_cmd = data.get("install", {}).get("command", "")
            if install_cmd:
                parts = install_cmd.split()
                if len(parts) >= 3 and parts[0] in ("conda", "pip", "npm", "brew"):
                    for p in reversed(parts):
                        if not p.startswith("-") and p not in ("install", "conda", "pip", "npm", "brew", "sudo"):
                            command = p
                            break

        # Method 3: use name as command
        if not command:
            command = name

        # Method 4: when test.command starts with python, the real binary is the name
        if test_cmd and test_cmd.startswith(("python ", "python3 ")):
            command = name

        # Note: no shutil.which() calls here — they are EXPENSIVE on WSL
        # (0.13s per missing command × 333 manifests = 45s).
        # The dispatcher will validate the command at call time.
        # If command doesn't exist on PATH, dispatch will return a clear error.

        # Build health check
        _health_cmd = test_cmd if test_cmd else f"{command} --version"

        # Build endpoint_config
        result["endpoint_type"] = "cli"
        result["endpoint_config"] = {"command": command, "args": []}
        if not result.get("health_check_cmd"):
            result["health_check_cmd"] = _health_cmd

        # Flatten capabilities
        caps = data.get("capabilities", {})
        if isinstance(caps, dict):
            flat_caps = []
            for action in caps.get("actions", []):
                flat_caps.append(action.replace(f"{name}_", "").replace("run_", "").replace("analyze_with_", name))
            for domain in data.get("domains", []):
                flat_caps.append(domain)
            category = data.get("category", "")
            if category:
                flat_caps.append(category)
            result["capabilities"] = flat_caps if flat_caps else [name]

        # input/output formats
        if isinstance(caps, dict):
            in_fmts = caps.get("input_formats", [])
            out_fmts = caps.get("output_formats", [])
            if in_fmts and not result.get("input_formats"):
                result["input_formats"] = in_fmts
            if out_fmts and not result.get("output_formats"):
                result["output_formats"] = out_fmts

        # Timeout from execution block
        exec_block = data.get("execution", {})
        if isinstance(exec_block, dict) and exec_block.get("timeout"):
            result["timeout"] = int(exec_block["timeout"])

        # Description fallback
        if not result.get("description"):
            result["description"] = f"{name} — auto-discovered tool"

        # Version fallback (use name as version, no which() call — too slow on WSL)
        if not result.get("version") or result["version"] == "latest":
            result["version"] = f"1.0.0 (command: {command})"

        return result

    @classmethod
    def from_file(cls, path: str) -> 'AgentManifest':
        """Load manifest from a JSON or YAML file.

        Supports .json extension. YAML support can be added if PyYAML is available.
        """
        path = os.path.expanduser(path)
        ext = os.path.splitext(path)[1].lower()

        if ext in (".json",):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        elif ext in (".yaml", ".yml"):
            try:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                return cls.from_dict(data)
            except ImportError:
                raise ImportError(
                    "PyYAML is required to load .yaml/.yml manifest files. "
                    "Install with: pip install pyyaml"
                )
        else:
            raise ValueError(f"Unsupported manifest format: {ext} (supported: .json, .yaml, .yml)")

    def to_dict(self) -> dict:
        """Serialize manifest to a dictionary."""
        d = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": self.capabilities,
            "input_formats": self.input_formats,
            "output_formats": self.output_formats,
            "endpoint_type": self.endpoint_type,
            "endpoint_config": self.endpoint_config,
            "timeout": self.timeout,
            "health_check_cmd": self.health_check_cmd,
        }
        if self.install_info:
            d["install_info"] = self.install_info
        return d

    def validate(self) -> list[str]:
        """Return list of validation errors, empty if valid."""
        errors = []

        if not self.name:
            errors.append("name is required")
        if not self.version:
            errors.append("version is required")
        if not self.description:
            errors.append("description is required")
        if not isinstance(self.capabilities, list):
            errors.append("capabilities must be a list")
        elif not self.capabilities:
            errors.append("capabilities must not be empty")
        if not isinstance(self.input_formats, list):
            errors.append("input_formats must be a list")
        elif not self.input_formats:
            errors.append("input_formats must not be empty")
        if not isinstance(self.output_formats, list):
            errors.append("output_formats must be a list")
        elif not self.output_formats:
            errors.append("output_formats must not be empty")

        valid_endpoint_types = ("cli", "http", "mcp", "python_api")
        if self.endpoint_type not in valid_endpoint_types:
            errors.append(
                f"endpoint_type must be one of {valid_endpoint_types}, "
                f"got '{self.endpoint_type}'"
            )
        if not isinstance(self.endpoint_config, dict):
            errors.append("endpoint_config must be a dict")
        if self.endpoint_type == "cli":
            cmd = self.endpoint_config.get("command", "")
            if not cmd:
                errors.append("cli endpoint_type requires endpoint_config.command")
        elif self.endpoint_type == "http":
            url = self.endpoint_config.get("url", "")
            if not url:
                errors.append("http endpoint_type requires endpoint_config.url")
        if not isinstance(self.timeout, int) or self.timeout < 1:
            errors.append("timeout must be a positive integer")

        # Validate install_info if present
        if self.install_info:
            method = self.install_info.get("method", "")
            valid_methods = ("pip", "git", "npm", "go", "cargo", "script")
            if method not in valid_methods:
                errors.append(
                    f"install_info.method must be one of {valid_methods}, got '{method}'"
                )
            if method == "pip" and not self.install_info.get("package"):
                errors.append("install_info.method='pip' requires install_info.package")
            if method == "git" and not self.install_info.get("source"):
                errors.append("install_info.method='git' requires install_info.source")
            if method == "script" and not self.install_info.get("script"):
                errors.append("install_info.method='script' requires install_info.script")

        return errors
