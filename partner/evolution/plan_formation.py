"""Plan Formation — maps external knowledge to concrete improvement plans for Partner's modules.

Part of the 5-step self-evolution cycle. Consumes ``Knowledge`` records produced by
:mod:`partner.evolution.knowledge_acquisition` and converts them into structured,
actionable improvement plans targeting specific Partner modules.

The module maintains a built-in mapping table of "external concept → Partner module"
so that insights about frameworks, libraries, or design patterns are matched to the
Partner subsystem most likely to benefit from adopting them.

Plan types
----------
- **config_change**: Modify YAML/JSON/TOML configuration files (risk: low).
- **prompt_change**: Modify prompt templates in ``prompts/*.txt`` (risk: medium).
- **modify_function**: Modify existing Python code (risk: high).
- **add_feature**: Add new functionality — new files, new classes, new methods (risk: high).

Risk levels
-----------
- **low**: Configuration changes — safe to auto-apply during the A-scheme cycle.
- **medium**: Prompt template changes — may affect behaviour; user approval recommended.
- **high**: Code changes — require user approval (B-scheme).

Usage::

    from partner.evolution.plan_formation import PlanFormation

    plans = PlanFormation.from_knowledge(knowledge_record)
    for plan in plans:
        print(f"[{plan.risk_level}] {plan.target_module}: {plan.description}")

The module also offers a standalone ``generate_plans()`` function for use from
architecture_mapper or gap_discovery pipelines.

Typical integration within the evolution cycle::

    KnowledgeAcquisition  →  PlanFormation  →  ArchitectureImprover
        (fetch)              (map→plan)         (apply)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Default namespace for plan IDs (incremented per generation batch)
_PLAN_ID_COUNTER: dict[str, int] = {"next": 1}

# Supported change types
CONFIG_CHANGE = "config_change"
PROMPT_CHANGE = "prompt_change"
MODIFY_FUNCTION = "modify_function"
ADD_FEATURE = "add_feature"
VALID_CHANGE_TYPES = (CONFIG_CHANGE, PROMPT_CHANGE, MODIFY_FUNCTION, ADD_FEATURE)

# Risk levels
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
VALID_RISK_LEVELS = (RISK_LOW, RISK_MEDIUM, RISK_HIGH)

# Risk → change_type mapping (for validation and auto-assignment)
_CHANGE_TYPE_RISK: dict[str, str] = {
    CONFIG_CHANGE: RISK_LOW,
    PROMPT_CHANGE: RISK_MEDIUM,
    MODIFY_FUNCTION: RISK_HIGH,
    ADD_FEATURE: RISK_HIGH,
}

# ═══════════════════════════════════════════════════════════════════════════════
# Concept → Partner Module Mapping Table
# ═══════════════════════════════════════════════════════════════════════════════

CONCEPT_TO_MODULE: dict[str, str] = {
    # ── Planning & Task Execution ────────────────────────────────────────
    "task_planner": "planner",
    "planning_algorithm": "planner",
    "hierarchical_planning": "planner",
    "replanning": "planner",
    "task_decomposition": "planner",
    "step_dependency": "planner",
    "dependency_graph": "planner",
    "parallel_execution": "harness_core",
    "event_parallelism": "harness_core",
    "event_dispatching": "events",
    "event_bus": "events",
    "event_driven_architecture": "events",
    "agentic_loop": "agents",
    "agent_orchestration": "agents",
    "multi_agent": "agents",
    "agent_registry": "agents",
    "tool_discovery": "agents",
    "tool_use": "agents",
    "function_calling": "agents",
    # ── LLM & Prompting ──────────────────────────────────────────────────
    "prompt_engineering": "llm",
    "prompt_template": "prompts",
    "prompt_optimization": "prompts",
    "few_shot_learning": "llm",
    "chain_of_thought": "llm",
    "reasoning_pattern": "llm",
    "model_routing": "llm",
    "llm_configuration": "llm",
    "token_management": "llm",
    "context_window": "llm",
    # ── Memory & Knowledge ───────────────────────────────────────────────
    "memory_management": "memory",
    "vector_database": "memory",
    "semantic_search": "memory",
    "embeddings": "memory",
    "retrieval_augmented": "knowledge",
    "rag_pipeline": "knowledge",
    "knowledge_graph": "knowledge",
    "information_retrieval": "knowledge",
    "web_scraping": "knowledge",
    "document_parsing": "knowledge",
    # ── CLI & User Interface ─────────────────────────────────────────────
    "cli_interface": "cli",
    "command_line_tool": "cli",
    "terminal_ui": "cli",
    "interactive_mode": "cli",
    "user_onboarding": "cli",
    "progress_reporting": "monitoring",
    "logging_strategy": "monitoring",
    "telemetry": "monitoring",
    "metrics_collection": "monitoring",
    "observability": "monitoring",
    # ── State & Persistence ──────────────────────────────────────────────
    "state_management": "state",
    "state_persistence": "state",
    "checkpointing": "state",
    "session_management": "state",
    "serialization": "state",
    "configuration": "core",
    "config_management": "core",
    "settings": "core",
    "database_schema": "data",
    "data_storage": "data",
    "data_migration": "data",
    # ── Goals & Intent ───────────────────────────────────────────────────
    "goal_tracking": "goal",
    "intent_detection": "goal",
    "intent_classification": "goal",
    "objective_decomposition": "goal",
    "progress_tracking": "goal",
    # ── Execution & Safety ───────────────────────────────────────────────
    "sandboxing": "harness_core",
    "execution_isolation": "harness_core",
    "timeout_handling": "harness_core",
    "error_handling": "harness_core",
    "retry_logic": "harness_core",
    "validation_pipeline": "harness_core",
    "output_validation": "harness_core",
    "guardrails": "harness_core",
    "safety_filter": "harness_core",
    # ── Learning & Adaptation ────────────────────────────────────────────
    "continuous_learning": "evolution",
    "self_improvement": "evolution",
    "pattern_extraction": "evolution",
    "lesson_extraction": "evolution",
    "behavior_tuning": "evolution",
    "evolution_cycle": "evolution",
    "feedback_loop": "dialogue",
    "conversation_history": "dialogue",
    "dialog_management": "dialogue",
    "user_preferences": "dialogue",
    # ── Workspace & Projects ─────────────────────────────────────────────
    "workspace_management": "workspace",
    "file_operations": "workspace",
    "project_management": "projects",
    "project_templates": "projects",
    "directory_structure": "workspace",
    # ── Skills & Capabilities ────────────────────────────────────────────
    "skill_registry": "skills",
    "skill_discovery": "skills",
    "capability_discovery": "capability_discovery",
    "plugin_architecture": "skills",
    "modular_extension": "skills",
    # ── Adapters & Integrations ──────────────────────────────────────────
    "api_adapter": "adapters",
    "external_integration": "adapters",
    "third_party": "adapters",
    "protocol_bridge": "adapters",
    "wsl_integration": "adapters",
    "windows_compatibility": "adapters",
    # ── Benchmarking & Evaluation ────────────────────────────────────────
    "benchmarking": "benchmark",
    "evaluation": "benchmark",
    "performance_testing": "benchmark",
    "quality_assurance": "benchmark",
    # ── Bioinformatics ───────────────────────────────────────────────────
    "bioinformatics": "bioinformatics",
    "sequence_analysis": "bioinformatics",
    "alignment": "bioinformatics",
    "molecule_processing": "bioinformatics",
    # ── Bioscience ───────────────────────────────────────────────────────
    "bioscience": "bioscience",
    "protein_analysis": "bioscience",
    "bionemo": "bioscience",
    "molecular_dynamics": "bioscience",
    # ── Meta & Growth ────────────────────────────────────────────────────
    "learning_tracking": "meta",
    "growth_milestone": "meta",
    "experience_stats": "meta",
    "self_description": "evolution",
    "architecture_mapping": "evolution",
}

def _build_reverse_mapping() -> dict[str, list[str]]:
    """Build reverse lookup from module name to list of related concepts."""
    mapping: dict[str, list[str]] = {}
    for concept, module in CONCEPT_TO_MODULE.items():
        mapping.setdefault(module, []).append(concept)
    return mapping


# Reverse mapping: module → list of associated concepts (useful for discovery)
_MODULE_TO_CONCEPTS: dict[str, list[str]] = _build_reverse_mapping()


# ═══════════════════════════════════════════════════════════════════════════════
# Data Types
# ═══════════════════════════════════════════════════════════════════════════════


class ChangeType(str, Enum):
    """Enumeration of supported change types for improvement plans.

    Each type maps to a specific kind of modification that PlanFormation can
    recommend, from low-risk configuration tweaks to high-risk code changes.
    """

    CONFIG_CHANGE = "config_change"
    """Modify YAML/JSON/TOML configuration files (risk: low)."""
    PROMPT_CHANGE = "prompt_change"
    """Modify prompt templates in ``prompts/*.txt`` (risk: medium)."""
    MODIFY_FUNCTION = "modify_function"
    """Modify existing Python code — function, class, or method (risk: high)."""
    ADD_FEATURE = "add_feature"
    """Add new functionality — new file, new class, or new public function (risk: high)."""


class RiskLevel(str, Enum):
    """Enumeration of risk levels for improvement plans.

    The risk level determines whether a plan can be auto-applied during the
    A-scheme (low risk) or requires user approval via the B-scheme.
    """

    LOW = "low"
    """Configuration-level changes. Safe to auto-apply without human review."""
    MEDIUM = "medium"
    """Prompt-level changes. May alter agent behaviour; human review recommended."""
    HIGH = "high"
    """Code-level changes. Require explicit user approval before application."""


@dataclass
class ImprovementPlan:
    """A structured improvement plan targeting a specific Partner module.

    Each plan is the product of mapping one or more insights from external
    knowledge onto a Partner subsystem.  Plans are actionable: they specify
    *what* to change, *where* to change it, *how* to change it (including
    the actual new code or configuration), and *why*.

    Attributes:
        id: Unique plan identifier (e.g. ``"plan_001"``). Auto-generated if
            not provided.
        target_module: Name of the Partner module to modify (e.g. ``"planner"``,
            ``"prompts"``, ``"harness_core"``).
        change_type: Type of modification — one of the ``ChangeType`` values
            or the equivalent string (``"config_change"``, ``"prompt_change"``,
            ``"modify_function"``, ``"add_feature"``).
        function_name: Name of the specific function, class, method, config key,
            or prompt file to modify.  For config changes this can be a dotted
            key path (e.g. ``"models.llm.temperature"``); for prompt changes
            it is the filename (e.g. ``"reflect.txt"``); for code changes it
            is the fully qualified Python symbol.
        new_code: The actual new source code, config content, or prompt text
            that should replace or augment the existing artefact.  For large
            additions this may be a diff fragment or a complete new method body.
        description: Human-readable explanation of what this plan proposes and
            *why* the change is beneficial.  Should reference the external
            insight that motivated the plan.
        risk_level: Risk assessment — one of ``"low"``, ``"medium"``, or
            ``"high"``.  Derived from ``change_type`` by default but can be
            overridden.
        prerequisite_changes: List of plan IDs that must be applied *before*
            this plan can be executed safely.  Empty tuple means no prerequisites.
            Used to model change dependencies (e.g. a new feature may depend on
            a prior config change).
        source_concepts: The external concept(s) from the knowledge record that
            triggered this plan.  Useful for traceability back to the original
            insight.
        confidence: Optional confidence score (0.0 – 1.0) indicating how well
            this plan maps to the source knowledge.  Higher values = stronger
            match.  Set during generation.
    """

    id: str = ""
    target_module: str = ""
    change_type: str = CONFIG_CHANGE
    function_name: str = ""
    new_code: str = ""
    description: str = ""
    risk_level: str = RISK_LOW
    prerequisite_changes: tuple[str, ...] = field(default_factory=tuple)
    source_concepts: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0

    def __post_init__(self) -> None:
        """Validate fields after initialisation and auto-generate an ID if missing."""
        if not self.id:
            self.id = _next_plan_id()
        _validate_change_type(self.change_type)
        _validate_risk_level(self.risk_level)
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict[str, Any]:
        """Serialize this plan to a JSON-compatible dictionary.

        Returns:
            A dict with all fields, suitable for serialization to JSON,
            storage in evolution_db, or transmission to ArchitectureImprover.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImprovementPlan:
        """Deserialize a plan from a dictionary (inverse of :meth:`to_dict`).

        Args:
            data: Dictionary with keys matching the dataclass fields.

        Returns:
            A new ``ImprovementPlan`` instance.
        """
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _next_plan_id() -> str:
    """Return the next auto-incrementing plan ID (e.g. ``\"plan_001\"``)."""
    global _PLAN_ID_COUNTER
    n = _PLAN_ID_COUNTER["next"]
    _PLAN_ID_COUNTER["next"] = n + 1
    return f"plan_{n:03d}"


def _reset_plan_id_counter(start: int = 1) -> None:
    """Reset the plan ID counter (useful in tests and for reproducible IDs)."""
    global _PLAN_ID_COUNTER
    _PLAN_ID_COUNTER["next"] = start


def _validate_change_type(change_type: str) -> None:
    """Raise ``ValueError`` if *change_type* is not a recognised change type."""
    if change_type not in VALID_CHANGE_TYPES:
        raise ValueError(
            f"Invalid change_type {change_type!r}. "
            f"Must be one of {VALID_CHANGE_TYPES}."
        )


def _validate_risk_level(risk_level: str) -> None:
    """Raise ``ValueError`` if *risk_level* is not a recognised risk level."""
    if risk_level not in VALID_RISK_LEVELS:
        raise ValueError(
            f"Invalid risk_level {risk_level!r}. "
            f"Must be one of {VALID_RISK_LEVELS}."
        )


def _infer_risk_level(change_type: str) -> str:
    """Return the default risk level for a given change type.

    Args:
        change_type: One of ``\"config_change\"``, ``\"prompt_change\"``,
            ``\"modify_function\"``, ``\"add_feature\"``.

    Returns:
        ``\"low\"``, ``\"medium\"``, or ``\"high\"``.
    """
    return _CHANGE_TYPE_RISK.get(change_type, RISK_HIGH)


def _guess_change_type_from_description(description: str, insight: str) -> str:
    """Heuristically pick a change type based on description/insight text.

    Scans the text for keywords suggesting config, prompt, or code changes.
    Falls back to ``modify_function`` if no clear signal is found.

    Args:
        description: The plan description text.
        insight: The original knowledge insight text.

    Returns:
        One of the four valid change type strings.
    """
    combined = (description + " " + insight).lower()

    # Config-related keywords
    if any(kw in combined for kw in (
        "config", "setting", "parameter", "threshold", "option",
        "yaml", "json", "toml", "ini", "env var",
    )):
        return CONFIG_CHANGE

    # Prompt-related keywords
    if any(kw in combined for kw in (
        "prompt", "template", "instruction", "system message",
        "user message", "few-shot", "example",
    )):
        return PROMPT_CHANGE

    # New-feature keywords
    if any(kw in combined for kw in (
        "add", "new feature", "new function", "new class",
        "new module", "new endpoint", "new command",
    )):
        return ADD_FEATURE

    # Default: existing code modification
    return MODIFY_FUNCTION


def _guess_target_module(
    insight: str,
    *,
    concept: str = "",
) -> str:
    """Map an insight string to the most likely Partner module.

    Scans the insight text against :data:`CONCEPT_TO_MODULE` keys.  If a
    keyword match is found, returns the corresponding module.  As a fallback,
    checks the *concept* parameter directly.

    Args:
        insight: The knowledge insight text to scan.
        concept: An optional explicit concept name to check if text scanning
            yields no match.

    Returns:
        The name of a Partner module (e.g. ``\"planner\"``, ``\"llm\"``,
        ``\"memory\"``, ``\"evolution\"``).
    """
    lower = insight.lower()

    # Score each concept for keyword overlap
    best_score = 0
    best_module = "core"

    for c_key, mod in CONCEPT_TO_MODULE.items():
        c_lower = c_key.lower()
        # Token overlap heuristic — count how many words from the concept
        # appear in the insight text
        tokens = c_lower.replace("_", " ").split()
        # Also handle single-token concepts (most common)
        score = 0
        for token in tokens:
            if token in lower:
                score += 1
        # Exact substring match gets a bonus
        if c_lower in lower:
            score += 2

        if score > best_score:
            best_score = score
            best_module = mod

    # If we had no textual match and an explicit concept was provided, map it
    if best_score == 0 and concept:
        mapped = CONCEPT_TO_MODULE.get(concept.lower())
        if mapped:
            return mapped

    return best_module


# ═══════════════════════════════════════════════════════════════════════════════
# Plan Generation
# ═══════════════════════════════════════════════════════════════════════════════


def _build_plan_from_insight(
    insight: str,
    source: str,
    *,
    change_type: str = "",
    target_module: str = "",
    concept: str = "",
    confidence: float = 0.5,
) -> ImprovementPlan:
    """Build a single ``ImprovementPlan`` from one knowledge insight string.

    This is the core mapping function.  It:

    1. Infers the target Partner module from the insight text.
    2. Infers or accepts the change type.
    3. Generates a human-readable description that explains the proposed change.
    4. Produces stub ``new_code`` content where applicable (for config and
       prompt changes this is a concrete template; for code changes it is a
       representative signature or comment placeholder).

    Args:
        insight: One key insight string from a knowledge record.
        source: The source URI of the knowledge (for traceability in the
            description).
        change_type: Override the change type.  If empty, inferred heuristically.
        target_module: Override the target module.  If empty, inferred.
        concept: An optional concept label for module mapping disambiguation.
        confidence: How confidently this insight maps to the plan (0.0–1.0).

    Returns:
        A populated ``ImprovementPlan`` instance.
    """
    # 1. Resolve target module
    if not target_module:
        target_module = _guess_target_module(insight, concept=concept)

    # 2. Resolve change type
    if not change_type:
        change_type = _guess_change_type_from_description(insight, insight)

    # 3. Build description
    description = _build_description(insight, source, target_module, change_type)

    # 4. Generate stub new_code based on change type
    new_code = _generate_stub_code(insight, target_module, change_type)

    # 5. Derive function name (config key path, filename, or symbol name)
    function_name = _derive_function_name(target_module, change_type, insight)

    # 6. Assign risk level
    risk_level = _infer_risk_level(change_type)

    return ImprovementPlan(
        target_module=target_module,
        change_type=change_type,
        function_name=function_name,
        new_code=new_code,
        description=description,
        risk_level=risk_level,
        source_concepts=(concept,) if concept else (),
        confidence=confidence,
    )


def _build_description(
    insight: str,
    source: str,
    target_module: str,
    change_type: str,
) -> str:
    """Build a human-readable description for an improvement plan.

    Args:
        insight: The original knowledge insight.
        source: Source URI of the knowledge.
        target_module: Mapped Partner module name.
        change_type: The type of change being proposed.

    Returns:
        A concise, informative description string.
    """
    type_labels = {
        CONFIG_CHANGE: "Configuration adjustment",
        PROMPT_CHANGE: "Prompt template update",
        MODIFY_FUNCTION: "Code modification",
        ADD_FEATURE: "New feature addition",
    }
    label = type_labels.get(change_type, "Improvement")
    # Truncate insight for readability
    truncated = (insight[:120] + "...") if len(insight) > 120 else insight
    return (
        f"[{target_module}] {label}: {truncated} "
        f"(inspired by {source})"
    )


def _generate_stub_code(
    insight: str,
    target_module: str,
    change_type: str,
) -> str:
    """Generate representative stub code based on change type and insight.

    For config changes, returns a YAML/INI-style config fragment.
    For prompt changes, returns a prompt template with the insight embedded.
    For code changes, returns a Python function stub with a docstring.

    Args:
        insight: The knowledge insight driving the change.
        target_module: The target Partner module name.
        change_type: The type of change.

    Returns:
        A string containing the stub code, config, or template text.
    """
    safe_insight = insight.replace("{", "{{").replace("}", "}}")

    if change_type == CONFIG_CHANGE:
        # YAML-style config key derived from the insight
        key = _insight_to_config_key(insight)
        return (
            f"# {target_module} configuration — inspired by external insight\n"
            f"{key}:\n"
            f"  enabled: true\n"
            f"  # {safe_insight}\n"
            f"  value: null\n"
        )

    if change_type == PROMPT_CHANGE:
        return (
            f"# Prompt template for {target_module}\n"
            f"# Source insight: {safe_insight}\n"
            f"# TODO: replace this placeholder with actual prompt content\n\n"
            f"You are a {target_module} module. {safe_insight}\n\n"
            f"Consider the following when responding:\n"
            f"- [key principle from insight]\n"
            f"- Apply the learned pattern appropriately\n"
        )

    if change_type == ADD_FEATURE:
        # Derive a class/function name from the insight
        class_name = _insight_to_pascal_name(insight)
        return (
            f"# New feature for {target_module} module\n"
            f"# Based on: {safe_insight}\n"
            f"from __future__ import annotations\n\n\n"
            f"class {class_name}:\n"
            f'    """{safe_insight}\n\n'
            f"    Added as part of the self-evolution plan formation cycle.\n"
            f'    """\n\n'
            f"    def __init__(self) -> None:\n"
            f"        ...\n\n"
            f"    def execute(self) -> dict:\n"
            f'        """Execute the {class_name} logic."""\n'
            f"        return {{'status': 'pending_implementation'}}\n"
        )

    # modify_function — return a function stub
    func_name = _insight_to_snake_name(insight)
    return (
        f"# Modification for {target_module} module\n"
        f"# Based on: {safe_insight}\n"
        f"from __future__ import annotations\n\n\n"
        f"def {func_name}(*args, **kwargs) -> dict:\n"
        f'    """{safe_insight}\n\n'
        f"    This is a stub generated by PlanFormation. Replace with actual\n"
        f"    implementation based on the external insight.\n\n"
        f"    Returns:\n"
        f"        Result dictionary.\n"
        f'    """\n'
        f"    ...\n"
    )


def _insight_to_config_key(insight: str) -> str:
    """Convert an insight string into a dotted config key.

    Example: ``\"Enable parallel step execution for performance\"`` →
    ``\"execution.parallel_step.enable\"``
    """
    # Extract the first few meaningful words
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", insight.lower())
    # Take up to 4 content words, avoiding very common words
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "and", "or", "but", "not", "that", "this", "these", "those",
        "it", "its", "as", "we", "can", "may", "will", "would",
        "should", "could", "has", "have", "had", "do", "does", "did",
    }
    filtered = [w for w in words if w not in stop]
    if not filtered:
        return "improvement.unknown"
    key_parts = filtered[:4]
    return ".".join(key_parts)


def _insight_to_pascal_name(insight: str) -> str:
    """Convert an insight string into a PascalCase class name.

    Example: ``\"Add a feedback loop for continuous learning\"`` →
    ``\"FeedbackLoopContinuousLearning\"``
    """
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", insight)
    stop = {
        "the", "a", "an", "to", "of", "in", "for", "on", "with",
        "at", "by", "from", "and", "or", "but", "not", "as", "we",
    }
    meaningful = [w for w in words if w.lower() not in stop]
    if not meaningful:
        return "NewFeature"
    return "".join(w.capitalize() for w in meaningful[:6])


def _insight_to_snake_name(insight: str) -> str:
    """Convert an insight string into a snake_case function name.

    Example: ``\"Optimize memory retrieval with embeddings\"`` →
    ``\"optimize_memory_retrieval_with_embeddings\"``
    """
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", insight)
    stop = {
        "the", "a", "an", "to", "of", "in", "for", "on", "with",
        "at", "by", "from", "and", "or", "but", "not", "as", "we",
        "is", "are", "was", "were",
    }
    meaningful = [w for w in words if w.lower() not in stop]
    if not meaningful:
        return "improvement_stub"
    return "_".join(w.lower() for w in meaningful[:8])


def _derive_function_name(
    target_module: str,
    change_type: str,
    insight: str,
) -> str:
    """Derive a concrete ``function_name`` value based on plan attributes.

    Returns different kinds of identifiers depending on change type:

    - **config_change**: A dotted config key path.
    - **prompt_change**: A filename like ``\"prompts/{target}_optimization.txt\"``.
    - **modify_function**: A dotted Python path, e.g. ``\"module.function_name\"``.
    - **add_feature**: A fully qualified class name, e.g. ``\"module.ClassName\"``.

    Args:
        target_module: The Partner module name.
        change_type: The type of change.
        insight: The knowledge insight text (used to derive meaningful names).

    Returns:
        A string suitable for the plan's ``function_name`` field.
    """
    if change_type == CONFIG_CHANGE:
        return f"{target_module}.{_insight_to_config_key(insight)}"

    if change_type == PROMPT_CHANGE:
        key = _insight_to_snake_name(insight)
        return f"prompts/{target_module}_{key}.txt"

    if change_type == ADD_FEATURE:
        class_name = _insight_to_pascal_name(insight)
        return f"{target_module}.{class_name}"

    # modify_function
    func_name = _insight_to_snake_name(insight)
    return f"{target_module}.{func_name}"


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════


class PlanFormation:
    """Maps external knowledge to structured, actionable improvement plans.

    ``PlanFormation`` is the central class of this module.  It maintains the
    concept-to-module mapping table and exposes static methods for generating
    :class:`ImprovementPlan` instances from :class:`Knowledge` records (or from
    raw insight strings).

    Typical workflow::

        from partner.evolution.knowledge_acquisition import KnowledgeAcquirer
        from partner.evolution.plan_formation import PlanFormation

        acquirer = KnowledgeAcquirer()
        knowledge = await acquirer.fetch_from_github(repo_url="...")

        plans = PlanFormation.from_knowledge(knowledge)
        for plan in plans:
            print(f"{plan.id}: [{plan.risk_level}] {plan.target_module}")

    PlanFormation also provides batch generation from multiple knowledge records
    and integration helpers for downstream consumers (gap_discovery, etc.).
    """

    @staticmethod
    def from_knowledge(
        knowledge: Any,
        *,
        max_plans_per_insight: int = 1,
        confidence_threshold: float = 0.0,
    ) -> list[ImprovementPlan]:
        """Generate improvement plans from a ``Knowledge`` acquisition record.

        Each key insight in the knowledge record is evaluated against the
        concept-to-module mapping and converted into zero or more improvement
        plans.

        Args:
            knowledge: A ``Knowledge`` dataclass instance (from
                ``partner.evolution.knowledge_acquisition``).  Must have
                ``key_insights``, ``source``, and optionally
                ``raw_metadata``.
            max_plans_per_insight: Maximum number of plans to generate per
                insight (default 1).  Usually 1 is sufficient because each
                insight maps to a single module/change.
            confidence_threshold: Minimum confidence score for a plan to be
                included (0.0–1.0).  Default 0.0 = include everything.

        Returns:
            A list of :class:`ImprovementPlan` instances.  May be empty if
            no insights could be mapped.
        """
        if not hasattr(knowledge, "key_insights"):
            logger.warning("[PLAN_FORM] 'knowledge' object has no key_insights attribute")
            return []

        insights = getattr(knowledge, "key_insights", ())
        source = getattr(knowledge, "source", "unknown")
        metadata = getattr(knowledge, "raw_metadata", {}) or {}

        if not insights:
            logger.debug("[PLAN_FORM] no key_insights in knowledge from %s", source)
            return []

        # Check metadata for concept hints
        concepts_hint = metadata.get("concepts", []) or metadata.get("focus_areas", [])

        plans: list[ImprovementPlan] = []
        for insight in insights:
            if not insight or not insight.strip():
                continue

            # If metadata provides explicit concepts, try each
            if concepts_hint:
                for concept in concepts_hint:
                    plan = _build_plan_from_insight(
                        insight, source, concept=concept,
                    )
                    if plan.confidence >= confidence_threshold:
                        plans.append(plan)
                        if len(plans) >= max_plans_per_insight * len(insights):
                            break
            else:
                plan = _build_plan_from_insight(insight, source)
                if plan.confidence >= confidence_threshold:
                    plans.append(plan)

            # Limit plans per insight
            if max_plans_per_insight < len([p for p in plans if any(
                ins in p.description for ins in [insight]
            )]):
                continue  # already added enough for this insight

        logger.info(
            "[PLAN_FORM] generated %d plans from %d insights (source=%s)",
            len(plans), len(insights), source,
        )
        return plans

    @staticmethod
    def from_insights(
        insights: list[str],
        source: str = "manual",
        *,
        concepts: list[str] | None = None,
    ) -> list[ImprovementPlan]:
        """Generate improvement plans from a list of raw insight strings.

        Useful when plans should be created from inline observations rather
        than a full ``Knowledge`` record — e.g. from gap_discovery findings,
        user feedback, or LLM-generated suggestions.

        Args:
            insights: List of insight strings describing potential improvements.
            source: Source label for traceability (default ``\"manual\"``).
            concepts: Optional list of concept hints to improve module mapping.

        Returns:
            A list of :class:`ImprovementPlan` instances.
        """
        if not insights:
            return []

        plans: list[ImprovementPlan] = []
        for i, insight in enumerate(insights):
            if not insight or not insight.strip():
                continue
            concept = concepts[i] if concepts and i < len(concepts) else ""
            plan = _build_plan_from_insight(insight, source, concept=concept)
            plans.append(plan)

        logger.info("[PLAN_FORM] generated %d plans from %d raw insights", len(plans), len(insights))
        return plans

    @staticmethod
    def from_multiple_knowledge(
        knowledge_records: list[Any],
        *,
        max_plans_per_record: int = 10,
    ) -> list[ImprovementPlan]:
        """Generate plans from multiple ``Knowledge`` records in a single batch.

        Deduplicates plans that target the same module + function_name to
        avoid redundant proposals.

        Args:
            knowledge_records: List of ``Knowledge`` instances.
            max_plans_per_record: Max plans to generate from each record.

        Returns:
            A deduplicated list of :class:`ImprovementPlan` instances.
        """
        seen: set[tuple[str, str]] = set()
        all_plans: list[ImprovementPlan] = []

        for record in knowledge_records:
            plans = PlanFormation.from_knowledge(record)
            for plan in plans[:max_plans_per_record]:
                dedup_key = (plan.target_module, plan.function_name)
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    all_plans.append(plan)

        logger.info(
            "[PLAN_FORM] batch generated %d unique plans from %d records",
            len(all_plans), len(knowledge_records),
        )
        return all_plans

    @staticmethod
    def get_modules_for_concept(concept: str) -> list[str]:
        """Look up which Partner module(s) map to a given external concept.

        Args:
            concept: An external concept name (e.g. ``\"rag_pipeline\"``,
                ``\"event_parallelism\"``, ``\"guardrails\"``).

        Returns:
            List of Partner module names that relate to this concept.
        """
        key = concept.lower().replace(" ", "_")
        direct = CONCEPT_TO_MODULE.get(key)
        if direct:
            return [direct]

        # Fuzzy match — find concepts containing the key
        results: list[str] = []
        for c_key, mod in CONCEPT_TO_MODULE.items():
            if key in c_key or c_key in key:
                results.append(mod)
        return list(set(results)) if results else ["core"]

    @staticmethod
    def get_concepts_for_module(module: str) -> list[str]:
        """Look up which external concepts are associated with a Partner module.

        Args:
            module: A Partner module name (e.g. ``\"planner\"``, ``\"memory\"``,
                ``\"events\"``).

        Returns:
            List of concept strings that map to this module.
        """
        return _MODULE_TO_CONCEPTS.get(module, [])

    @staticmethod
    def list_all_modules() -> list[str]:
        """Return a sorted list of all known Partner modules in the mapping table.

        Returns:
            Sorted list of unique module names.
        """
        return sorted(set(CONCEPT_TO_MODULE.values()))

    @staticmethod
    def list_all_concepts() -> list[str]:
        """Return a sorted list of all external concepts in the mapping table.

        Returns:
            Sorted list of unique concept names.
        """
        return sorted(CONCEPT_TO_MODULE.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience function (standalone entry point)
# ═══════════════════════════════════════════════════════════════════════════════


def generate_plans(
    insights: list[str],
    source: str = "auto_detect",
    *,
    concepts: list[str] | None = None,
    as_dicts: bool = False,
) -> list[ImprovementPlan] | list[dict[str, Any]]:
    """Standalone function to generate improvement plans from insight strings.

    This is a convenience wrapper around ``PlanFormation.from_insights()``
    intended for use by other evolution modules that want a simple function
    call rather than a class invocation.

    Args:
        insights: List of insight strings describing potential improvements.
        source: Source label.  Default ``\"auto_detect\"``.
        concepts: Optional concept hints for module mapping.
        as_dicts: If ``True``, return plain dictionaries instead of
            ``ImprovementPlan`` objects (useful for JSON serialisation).

    Returns:
        List of :class:`ImprovementPlan` instances (or dicts if
        ``as_dicts=True``).
    """
    plans = PlanFormation.from_insights(insights, source, concepts=concepts)
    if as_dicts:
        return [p.to_dict() for p in plans]
    return plans


# ═══════════════════════════════════════════════════════════════════════════════
# LLM-assisted plan generation (optional — requires LLM adapter)
# ═══════════════════════════════════════════════════════════════════════════════


async def generate_plans_with_llm(
    insights: list[str],
    source: str = "llm_assisted",
    *,
    llm_adapter: Any = None,
    system_prompt: str = "",
) -> list[ImprovementPlan]:
    """Generate improvement plans using an LLM for higher-quality mapping.

    When an LLM adapter is available, this function sends the insights along
    with the concept-to-module mapping to an LLM for more nuanced plan
    generation.  Falls back to the rule-based :func:`generate_plans` if
    no adapter is provided.

    Args:
        insights: List of insight strings.
        source: Source label.
        llm_adapter: An object with a ``async generate(prompt, system)``
            method that returns a JSON string or parsed dict.
        system_prompt: Optional system prompt override.  If empty, a default
            prompt is constructed.

    Returns:
        List of :class:`ImprovementPlan` instances.

    Raises:
        ValueError: If the LLM returns unparseable output.
    """
    if llm_adapter is None:
        logger.info("[PLAN_FORM] no LLM adapter provided; falling back to rule-based generation")
        return generate_plans(insights, source)  # type: ignore[return-value]

    # Build context with the concept mapping table
    module_list = "\n".join(
        f"  - {mod}: {', '.join(concepts[:3])}"
        for mod, concepts in list(_MODULE_TO_CONCEPTS.items())[:30]
    )

    prompt = (
        "You are an AI architect mapping external knowledge insights to specific "
        "improvement plans for the Partner AI agent system.\n\n"
        "Partner has these modules:\n"
        f"{module_list}\n\n"
        "For each insight, return a JSON array of plan objects with these fields:\n"
        "- target_module (str): the Partner module to modify\n"
        "- change_type (str): config_change | prompt_change | modify_function | add_feature\n"
        "- function_name (str): specific function, config key, or file name\n"
        "- new_code (str): stub code, config YAML, or prompt template text\n"
        "- description (str): what to change and why\n"
        "- risk_level (str): low | medium | high\n"
        "- confidence (float): 0.0–1.0 how confidently this maps\n\n"
        "Insights:\n"
    )
    for i, ins in enumerate(insights, 1):
        prompt += f"{i}. {ins}\n"

    prompt += "\nRespond ONLY with a valid JSON array. No markdown, no code fences."

    system = system_prompt or (
        "You are an experienced software architect specialising in AI agent "
        "systems. You map external knowledge into concrete, actionable "
        "improvement plans."
    )

    try:
        if hasattr(llm_adapter, "generate"):
            result = await llm_adapter.generate(prompt, system=system)
        else:
            result = await llm_adapter(prompt, system=system)

        # Parse the LLM output
        if isinstance(result, str):
            import json as _json
            # Clean up potential markdown fences
            cleaned = re.sub(r"^```(?:json)?\s*", "", result.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
            data = _json.loads(cleaned)
        elif isinstance(result, dict) and "choices" in result:
            content = result["choices"][0]["message"]["content"]
            import json as _json
            cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
            data = _json.loads(cleaned)
        elif isinstance(result, list):
            data = result
        else:
            logger.warning("[PLAN_FORM] unexpected LLM result type: %s", type(result))
            return generate_plans(insights, source)  # type: ignore[return-value]

        if not isinstance(data, list):
            data = [data]

        plans = []
        for item in data:
            if not isinstance(item, dict):
                continue
            plan = ImprovementPlan(
                target_module=item.get("target_module", "core"),
                change_type=item.get("change_type", MODIFY_FUNCTION),
                function_name=item.get("function_name", ""),
                new_code=item.get("new_code", ""),
                description=item.get("description", ""),
                risk_level=item.get("risk_level", RISK_HIGH),
                confidence=float(item.get("confidence", 0.5)),
            )
            plans.append(plan)

        logger.info("[PLAN_FORM] LLM generated %d plans", len(plans))
        return plans

    except Exception as exc:
        logger.warning("[PLAN_FORM] LLM generation failed: %s; falling back to rule-based", exc)
        return generate_plans(insights, source)  # type: ignore[return-value]
