"""Agent Manifest model and validation.

Any agent (Hermes, OpenClaw, Codex, CytoBridge, etc.) describes itself
using this manifest, which Partner uses for discovery and invocation.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional


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

    @classmethod
    def from_dict(cls, data: dict) -> 'AgentManifest':
        """Create manifest from a dictionary."""
        return cls(
            name=data.get("name", ""),
            version=data.get("version", ""),
            description=data.get("description", ""),
            capabilities=list(data.get("capabilities", [])),
            input_formats=list(data.get("input_formats", [])),
            output_formats=list(data.get("output_formats", [])),
            endpoint_type=data.get("endpoint_type", "cli"),
            endpoint_config=dict(data.get("endpoint_config", {})),
            timeout=int(data.get("timeout", 300)),
            health_check_cmd=str(data.get("health_check_cmd", "")),
        )

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
        return {
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

        return errors
