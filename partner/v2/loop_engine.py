"""
loop_engine.py — Rolling planner, gap detector, pause/resume, outer learning

Four atomic handlers for the v2 loop:
  1. atomic_rolling_plan  — Generate next N steps
  2. atomic_gap_detect    — Detect gaps between goal and current state
  3. atomic_pause_resume  — Pause / resume / status via persistent JSON
  4. atomic_outer_learn   — Generate a learning plan for a gap
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


def atomic_goal_parse(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """解析用户目标为结构化的子目标列表。

    Params:
        goal (str): 用户输入的目标
        context (str, optional): 额外上下文

    Returns:
        dict with ok, goals (list of {id, title, description, priority, dependencies}),
        and estimated_complexity
    """
    goal = params.get("goal", "").strip()
    if not goal:
        return {"ok": False, "error": "goal parameter is required", "goals": []}

    context = params.get("context", "")

    import re as _re

    goals = []
    complexity = "simple"

    goal_lower = goal.lower()
    separators = _re.split(r'[。；;]|\s+和\s+|\s+并\s+|\s+同时\s+|\n\d+[.、]', goal)
    action_patterns = _re.findall(r'(?:分析|搜索|生成|创建|读取|下载|安装|配置|比较|测试|部署|训练|可视化|汇总|报告)(?:\s+[^，。；]+)', goal)

    if len(separators) > 1 or len(action_patterns) > 1:
        complexity = "complex"
        for i, part in enumerate(separators):
            part = part.strip()
            if part:
                action_type = "generic"
                for keyword, atype in [
                    ("分析", "analysis"), ("搜索", "search"), ("生成", "generation"),
                    ("创建", "creation"), ("读取", "read"), ("下载", "download"),
                    ("安装", "install"), ("配置", "config"), ("比较", "comparison"),
                    ("测试", "test"), ("部署", "deploy"), ("训练", "train"),
                    ("可视化", "visualization"), ("汇总", "summary"), ("报告", "report"),
                ]:
                    if keyword in part:
                        action_type = atype
                        break
                goals.append({
                    "id": f"subgoal_{i}",
                    "title": part[:60],
                    "description": part,
                    "action_type": action_type,
                    "priority": i + 1,
                    "dependencies": [f"subgoal_{j}" for j in range(i)] if i > 0 else [],
                })
    else:
        action_type = "generic"
        for keyword, atype in [
            ("分析", "analysis"), ("搜索", "search"), ("生成", "generation"),
            ("研究", "research"), ("学习", "learning"), ("测试", "test"),
            ("部署", "deploy"), ("配置", "config"), ("写", "writing"),
            ("修复", "fix"), ("优化", "optimization"),
        ]:
            if keyword in goal_lower:
                action_type = atype
                break
        goals.append({
            "id": "subgoal_0",
            "title": goal[:60],
            "description": goal,
            "action_type": action_type,
            "priority": 1,
            "dependencies": [],
        })

    if context:
        context_lower = context.lower()
        if "urgent" in context_lower or "紧急" in context_lower:
            for g in goals:
                g["priority"] = max(0, g["priority"] - 1)

    return {
        "ok": True,
        "goals": goals,
        "goal_count": len(goals),
        "estimated_complexity": complexity,
        "parsed_from": "goal_parser",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKSPACE = os.environ.get("WORKSPACE", "/mnt/e/work")
PAUSE_STATE_PATH = os.path.join(WORKSPACE, "workspace", "state", "plan_pause_state.json")


def _ensure_pause_dir() -> None:
    os.makedirs(os.path.dirname(PAUSE_STATE_PATH), exist_ok=True)


def _load_pause_state() -> dict[str, Any]:
    _ensure_pause_dir()
    if not os.path.isfile(PAUSE_STATE_PATH):
        return {"status": "running", "paused_at": None, "reason": None}
    try:
        with open(PAUSE_STATE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"status": "running", "paused_at": None, "reason": None}


def _save_pause_state(state: dict[str, Any]) -> None:
    _ensure_pause_dir()
    with open(PAUSE_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# 1. atomic_rolling_plan — Generate next 3-5 steps (rule-based)
# ---------------------------------------------------------------------------

def atomic_rolling_plan(
    ctx: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Generate the next N steps toward a goal.

    Params
    ------
    goal : str
        The high-level goal being pursued.
    context : str
        Current situation / state summary.
    completed_steps : list[dict]
        Each entry: {step, result, ok}.
    steps_per_plan : int, optional
        How many steps to produce (default 4).

    Returns
    -------
    dict with keys: ok, plan, reasoning, remaining_budget
    """
    goal = params.get("goal", "").strip()
    context = params.get("context", "").strip()
    completed_steps: list[dict] = params.get("completed_steps", [])
    steps_per_plan = params.get("steps_per_plan", 4)

    # --- Input validation ---
    if not goal:
        return {
            "ok": False,
            "plan": [],
            "reasoning": "No goal provided — cannot generate a plan.",
            "remaining_budget": 0,
        }

    # --- Analyse completed steps ---
    failed = [s for s in completed_steps if not s.get("ok")]
    succeeded = [s for s in completed_steps if s.get("ok")]

    # Build a simple reasoning trace
    reasoning_parts: list[str] = []

    if not completed_steps:
        reasoning_parts.append(
            "No prior steps completed. Starting fresh with initial "
            "investigation and setup actions."
        )
    else:
        reasoning_parts.append(
            f"Completed {len(succeeded)} step(s) successfully, "
            f"{len(failed)} step(s) failed."
        )
        if failed:
            failures_desc = "; ".join(
                f"'{s.get('step', '?')}' — {s.get('result', 'no result')}"
                for s in failed[:3]
            )
            reasoning_parts.append(
                f"Recent failures: {failures_desc}. Adjusting strategy to "
                "avoid repeated errors."
            )

    if context:
        reasoning_parts.append(f"Context summary: {context}")

    reasoning_parts.append(
        f"Planning next {steps_per_plan} steps toward goal: {goal[:120]}"
    )

    # --- Generate plan steps (rule-based templates) ---
    plan: list[dict[str, Any]] = []

    # Bias: offset the step type based on what has happened so far
    has_failures = len(failed) > 0
    has_progress = len(succeeded) > 0

    topics_seen: set[str] = set()
    for s in completed_steps:
        step_name = s.get("step", "")
        topics_seen.add(step_name.lower())

    step_idx = len(completed_steps) + 1

    for i in range(steps_per_plan):
        step_id = f"step_{step_idx + i:03d}"

        # Rule: if we've had failures, insert a diagnostic step early
        if i == 0 and has_failures:
            action = "diagnose"
            description = (
                f"Diagnose root cause of recent failures "
                f"({len(failed)} failed step(s)) before proceeding."
            )
            params_body = {
                "focus": "failure_analysis",
                "context": context or goal,
            }
            expected = "Root cause identified or mitigation plan outlined."
        # Rule: first step with no progress → investigate
        elif i == 0 and not has_progress:
            action = "investigate"
            description = f"Initial investigation into: {goal[:100]}"
            params_body = {
                "action": "gather_information",
                "target": goal,
            }
            expected = "Clear understanding of current state and next actions."
        # Rule: mid-plan step → execute
        elif i < steps_per_plan - 1 or has_progress:
            action = "execute"
            description = (
                f"Work on sub-goal related to: {goal[:80]} "
                f"(step {i + 1} of {steps_per_plan})"
            )
            params_body = {
                "action": "execute_step",
                "context": context or goal,
            }
            expected = "Progress made toward the goal."
        # Rule: last step → review / verify
        else:
            action = "verify"
            description = "Review progress and verify alignment with the goal."
            params_body = {
                "action": "verify_progress",
                "goal": goal,
            }
            expected = "Validation of completed work or identification of remaining gaps."

        plan.append({
            "step_id": step_id,
            "action": action,
            "params": params_body,
            "expected": expected,
        })

    # Simple remaining budget heuristic
    remaining_budget = max(3, 10 - len(completed_steps))

    return {
        "ok": True,
        "plan": plan,
        "reasoning": " ".join(reasoning_parts),
        "remaining_budget": remaining_budget,
    }


# ---------------------------------------------------------------------------
# 2. atomic_gap_detect — Detect gaps between goal and current state
# ---------------------------------------------------------------------------

def atomic_gap_detect(
    ctx: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Compare goal against current state and surface gaps.

    Params
    ------
    goal : str
        The intended outcome.
    current_state : str
        Description of where things stand now.
    available_tools : list[str], optional
        Tools the agent has access to (used to identify missing capability gaps).

    Returns
    -------
    dict with keys: ok, gaps, has_gaps, gap_count
    """
    goal = params.get("goal", "").strip()
    current_state = params.get("current_state", "").strip()
    available_tools: list[str] = params.get("available_tools", [])

    if not goal:
        return {"ok": False, "gaps": [], "has_gaps": True, "gap_count": 1}

    gaps: list[dict[str, Any]] = []
    goal_lower = goal.lower()
    state_lower = current_state.lower()

    # --- Gap 1: Knowledge / information missing ---
    # Look for question-like patterns in the goal, or obvious unknowns
    question_words = ["how", "what", "why", "where", "when", "which", "who"]
    for qw in question_words:
        if goal_lower.startswith(qw) or f" {qw} " in goal_lower:
            if not current_state or goal_lower not in state_lower:
                gaps.append({
                    "type": "knowledge",
                    "description": (
                        f"The goal appears to ask '{qw} ...' but the current "
                        f"state does not clearly provide that answer."
                    ),
                    "severity": "medium",
                    "suggested_action": "Search for information or consult documentation.",
                })
                break

    # --- Gap 2: State mismatch — goal key-phrases absent from current_state ---
    goal_tokens = set(goal_lower.split())
    state_tokens = set(state_lower.split())
    missing_tokens = goal_tokens - state_tokens
    # Only flag substantial words (len > 3) to avoid noise
    substantial_missing = {t for t in missing_tokens if len(t) > 3}

    if substantial_missing:
        gaps.append({
            "type": "state",
            "description": (
                f"The current state does not mention several key concepts "
                f"from the goal: {', '.join(sorted(substantial_missing)[:6])}."
            ),
            "severity": "high",
            "suggested_action": (
                "Gather more information about the missing concepts or "
                "update the state representation."
            ),
        })

    # --- Gap 3: Tool capability gap ---
    if available_tools:
        tool_keywords = set()
        for tool in available_tools:
            tool_keywords.update(tool.lower().replace("_", " ").replace("-", " ").split())

        # If goal mentions actions that no tool seems to cover
        action_keywords = {"search", "read", "write", "execute", "analyze", "compute",
                           "plot", "scan", "deploy", "build", "compile", "test"}
        needed_actions = action_keywords & goal_tokens
        # Check if any tool matches these needed actions
        covered = needed_actions & tool_keywords
        uncovered = needed_actions - covered
        if uncovered:
            gaps.append({
                "type": "tool",
                "description": (
                    f"The goal implies actions ({', '.join(sorted(uncovered))}) "
                    f"that no available tool explicitly supports."
                ),
                "severity": "medium",
                "suggested_action": (
                    "Consider installing or enabling additional tools, or "
                    "adapting the approach to use existing tools differently."
                ),
            })

    # --- Gap 4: Empty / no state ---
    if not current_state:
        gaps.append({
            "type": "knowledge",
            "description": (
                "No current state provided — unable to assess progress "
                "toward the goal."
            ),
            "severity": "high",
            "suggested_action": "Gather information to establish a baseline state.",
        })

    # --- Gap 5: Contradiction detection (basic) ---
    negation_markers = ["not ", "no ", "without ", "lack ", "missing ", "unable "]
    for marker in negation_markers:
        if marker in state_lower:
            # The state admits a deficiency — check if goal expects the opposite
            # Simple heuristic: if goal exists, flag as potential gap
            if goal:
                gaps.append({
                    "type": "data",
                    "description": (
                        f"Current state contains a negation ('{marker.strip()}') "
                        f"suggesting a deficiency that may block the goal."
                    ),
                    "severity": "low",
                    "suggested_action": "Investigate the deficiency and plan remediation.",
                })
                break

    # Deduplicate by type+description
    seen: set[str] = set()
    unique_gaps: list[dict[str, Any]] = []
    for g in gaps:
        key = f"{g['type']}|{g['description']}"
        if key not in seen:
            seen.add(key)
            unique_gaps.append(g)

    return {
        "ok": True,
        "gaps": unique_gaps,
        "has_gaps": len(unique_gaps) > 0,
        "gap_count": len(unique_gaps),
    }


# ---------------------------------------------------------------------------
# 3. atomic_pause_resume — Pause / resume / status via persistent JSON
# ---------------------------------------------------------------------------

def atomic_pause_resume(
    ctx: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Pause, resume, or check status of the execution loop.

    Params
    ------
    action : str
        One of 'pause', 'resume', or 'status'.
    reason : str, optional
        Human-readable reason (required for pause).

    Returns
    -------
    dict with keys: ok, status, paused_at, reason
    """
    action = params.get("action", "status").strip().lower()
    reason = params.get("reason", "").strip()

    state = _load_pause_state()

    if action == "pause":
        if state.get("status") == "paused":
            return {
                "ok": True,
                "status": "paused",
                "paused_at": state.get("paused_at"),
                "reason": state.get("reason"),
            }
        state["status"] = "paused"
        state["paused_at"] = time.time()
        state["reason"] = reason or "Paused by user or system request."
        _save_pause_state(state)
        return {
            "ok": True,
            "status": "paused",
            "paused_at": state["paused_at"],
            "reason": state["reason"],
        }

    elif action == "resume":
        if state.get("status") != "paused":
            return {
                "ok": True,
                "status": "running",
                "paused_at": None,
                "reason": None,
            }
        state["status"] = "running"
        state["paused_at"] = None
        state["reason"] = None
        _save_pause_state(state)
        return {
            "ok": True,
            "status": "running",
            "paused_at": None,
            "reason": None,
        }

    elif action == "status":
        return {
            "ok": True,
            "status": state.get("status", "running"),
            "paused_at": state.get("paused_at"),
            "reason": state.get("reason"),
        }

    else:
        return {
            "ok": False,
            "status": state.get("status", "unknown"),
            "paused_at": state.get("paused_at"),
            "reason": f"Unknown action '{action}'. Use 'pause', 'resume', or 'status'.",
        }


# ---------------------------------------------------------------------------
# 4. atomic_outer_learn — Generate learning plan for a gap
# ---------------------------------------------------------------------------

_LEARNING_SOURCES: dict[str, list[str]] = {
    "knowledge": [
        "Official documentation / API reference",
        "Community forums (Stack Overflow, Reddit)",
        "Academic papers / arXiv",
        "Tutorials and blog posts",
    ],
    "tool": [
        "Tool repository README and wiki",
        "Package manager (pip, npm, cargo, etc.)",
        "GitHub releases and changelog",
        "Tool-specific documentation site",
    ],
    "data": [
        "Datasets and benchmarks (Kaggle, Hugging Face, UCI)",
        "Public APIs and data portals",
        "Web scraping or crawling",
        "Domain-specific data repositories",
    ],
    "skill": [
        "Online courses (Coursera, edX, Fast.ai)",
        "Official tutorials and cookbooks",
        "Practice projects and examples",
        "Mentorship / pair programming",
    ],
    "dependency": [
        "Package dependency tree audit",
        "Version compatibility matrices",
        "Migration guides and changelogs",
        "Issue trackers and pull requests",
    ],
}


def atomic_outer_learn(
    ctx: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Generate a structured learning plan to close a gap.

    Params
    ------
    gap_type : str
        One of 'knowledge', 'tool', 'data', 'skill', 'dependency'.
    gap_description : str
        What is missing or needs to be learned.
    target : str
        The specific subject / technology / skill to learn about.

    Returns
    -------
    dict with keys: ok, learning_plan, sources
    """
    gap_type = params.get("gap_type", "").strip().lower()
    gap_description = params.get("gap_description", "").strip()
    target = params.get("target", "").strip()

    valid_types = {"knowledge", "tool", "data", "skill", "dependency"}

    if gap_type not in valid_types:
        return {
            "ok": False,
            "learning_plan": {
                "search_queries": [],
                "approach": "",
                "integration_steps": [],
            },
            "sources": [],
        }

    if not target:
        target = gap_description or gap_type

    # --- Search queries ---
    search_queries: list[str] = []
    base = target[:200]
    search_queries.append(f"{base} tutorial guide")
    search_queries.append(f"{base} best practices")

    if gap_type == "tool":
        search_queries.append(f"{base} installation setup")
        search_queries.append(f"{base} API documentation")
    elif gap_type == "knowledge":
        search_queries.append(f"{base} overview introduction")
        search_queries.append(f"{base} explained")
    elif gap_type == "data":
        search_queries.append(f"{base} dataset")
        search_queries.append(f"{base} data source API")
    elif gap_type == "skill":
        search_queries.append(f"{base} learn practice exercise")
        search_queries.append(f"{base} curriculum")
    elif gap_type == "dependency":
        search_queries.append(f"{base} version compatibility")
        search_queries.append(f"{base} migration guide")

    # --- Approach description ---
    approach_templates = {
        "knowledge": (
            "Research the topic using curated search queries, "
            "then synthesize findings into a concise summary. "
            "Validate understanding by explaining the concept in "
            "simple terms."
        ),
        "tool": (
            "Install and configure the tool in a sandbox environment, "
            "follow the official 'getting started' guide, then "
            "implement a minimal working example that exercises the "
            "core functionality needed."
        ),
        "data": (
            "Identify and evaluate candidate data sources, assess "
            "licensing and suitability, then fetch a sample to "
            "validate structure and quality before full ingestion."
        ),
        "skill": (
            "Follow a structured curriculum (tutorial → practice "
            "exercise → small project), collecting feedback at each "
            "stage. Focus on applied learning with concrete outputs."
        ),
        "dependency": (
            "Audit the dependency tree, check version compatibility "
            "against the current environment, and create a migration "
            "plan with rollback checkpoints."
        ),
    }
    approach = approach_templates.get(
        gap_type,
        f"Investigate and learn about: {target}",
    )

    # --- Integration steps ---
    integration_steps: list[str] = [
        f"Complete initial research on '{target}'",
        "Document findings and key takeaways",
        "Implement a proof-of-concept using the new knowledge",
        "Test and validate the implementation against the original goal",
        "Update any relevant configuration or dependencies",
    ]

    sources = _LEARNING_SOURCES.get(gap_type, _LEARNING_SOURCES["knowledge"])

    return {
        "ok": True,
        "learning_plan": {
            "search_queries": search_queries,
            "approach": approach,
            "integration_steps": integration_steps,
        },
        "sources": sources,
    }
