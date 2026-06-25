from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


JsonDict = dict[str, Any]


@dataclass
class SkillResult:
    ok: bool
    output: Any = None
    error: str = ""
    cached: bool = False
    metadata: JsonDict = field(default_factory=dict)


@dataclass
class Skill:
    name: str
    description: str
    input_schema: JsonDict = field(default_factory=dict)
    output_schema: JsonDict = field(default_factory=dict)
    estimated_ms: int = 0
    dependencies: list[str] = field(default_factory=list)
    kind: str = "atomic"
    external: bool = False
    endpoint: str = ""
    method: str = "POST"
    tags: list[str] = field(default_factory=list)
    metadata: JsonDict = field(default_factory=dict)

    def to_prompt_row(self) -> str:
        flags = []
        if self.external:
            flags.append("external_agent")
        if self.estimated_ms:
            flags.append(f"eta_ms={self.estimated_ms}")
        suffix = f" [{' '.join(flags)}]" if flags else ""
        return f"- {self.name}: {self.description}{suffix}"

    def to_dict(self) -> JsonDict:
        return asdict(self)
