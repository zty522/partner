"""Core primitives for Partner Harness task execution.

Harness is the evolution carrier — the living framework that an Agent can
inspect, modify, and improve at runtime (Loop+Harness survey, Tsinghua 2026).

Modules:
  - artifact_validator: Validate expected output artifacts
  - capability_model: Measure environment ceiling (3-axis)
  - evolution_carrier: Fast-path vs slow-path evolution routing
  - harness_self_modify: Self-inspection and modification interface
  - remediation_handler: Auto-fix common execution failures
  - robust_executor: Resilient task execution with retry/timeout
  - task_instance: Per-task execution context and logging
"""

from .artifact_validator import ArtifactValidationResult, ArtifactValidator, ExpectedArtifact
from .capability_model import CapabilityModel, CapabilityScore
from .evolution_carrier import EvolutionCarrier, EvolutionDecision, EvolutionPath, FastPathStats, ImprovementTarget
from .harness_self_modify import HarnessSelfModify, HarnessSnapshot, ModificationResult
from .remediation_handler import RemediationHandler
from .robust_executor import RobustExecutor, RobustResult, load_harness_config
from .task_instance import TaskInstance, parse_continue_project_marker

__all__ = [
    # Artifact validation
    "ArtifactValidationResult",
    "ArtifactValidator",
    "ExpectedArtifact",
    # Capability model
    "CapabilityModel",
    "CapabilityScore",
    # Evolution carrier
    "EvolutionCarrier",
    "EvolutionDecision",
    "EvolutionPath",
    "FastPathStats",
    "ImprovementTarget",
    # Harness self-modification
    "HarnessSelfModify",
    "HarnessSnapshot",
    "ModificationResult",
    # Execution
    "RemediationHandler",
    "RobustExecutor",
    "RobustResult",
    "TaskInstance",
    "load_harness_config",
    "parse_continue_project_marker",
]
