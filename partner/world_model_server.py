"""World Model Server — AETHER-backed hybrid architecture.

Three-tier fallback chain:
  1. AETHER  — GPU visual world model (remote)
  2. LLM     — text-based language model API
  3. Heuristic — rule-based fallback (always available)

REST endpoints:
  POST /simulate   — simulate plan execution
  POST /optimize   — suggest plan optimizations
  GET  /health     — server status and backend availability

Run: python world_model_server.py
"""
import asyncio
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration (from env vars with sensible defaults)
# ---------------------------------------------------------------------------
CONFIG: Dict[str, Any] = {
    # Server
    "HOST": os.environ.get("WORLD_MODEL_HOST", "0.0.0.0"),
    "PORT": int(os.environ.get("WORLD_MODEL_PORT", "8100")),
    "LOG_DIR": os.environ.get("WORLD_MODEL_LOG_DIR", "/tmp/workspace_world_model"),
    "LOG_FILE": "world_model.log",
    # Mode
    "MODE": os.environ.get("WORLD_MODEL_MODE", "hybrid"),  # hybrid | aether_only | llm_only | heuristic_only
    # AETHER backend
    "AETHER_ENDPOINT": os.environ.get("AETHER_ENDPOINT", "http://localhost:8080"),
    "AETHER_TIMEOUT": int(os.environ.get("AETHER_TIMEOUT", "90")),
    # LLM backend (uses HermesAdapter — no separate API key needed)
    "LLM_MODEL": os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
    "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "deepseek"),
    "LLM_TIMEOUT": int(os.environ.get("LLM_TIMEOUT", "30")),
    "LLM_MAX_RETRIES": int(os.environ.get("LLM_MAX_RETRIES", "2")),
    # Simulation
    "MAX_SIMULATION_STEPS": int(os.environ.get("MAX_SIMULATION_STEPS", "10")),
    "FALLBACK_TO_LLM": os.environ.get("FALLBACK_TO_LLM", "true").lower() == "true",
}

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
os.makedirs(CONFIG["LOG_DIR"], exist_ok=True)
_log_path = os.path.join(CONFIG["LOG_DIR"], CONFIG["LOG_FILE"])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(_log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("world_model")

_start_time: float = time.time()


# ===================================================================
# TIER 1: AETHER backend (visual world model on GPU)
# ===================================================================

async def _call_aether(
    plan: List[Dict[str, Any]], state: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Call the AETHER API server. Returns parsed response or None on failure."""
    endpoint = CONFIG["AETHER_ENDPOINT"].rstrip("/") + "/simulate"
    payload = {
        "plan": plan,
        "state": state,
        "max_steps": CONFIG["MAX_SIMULATION_STEPS"],
    }

    try:
        async with httpx.AsyncClient(timeout=CONFIG["AETHER_TIMEOUT"]) as client:
            resp = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "AETHER simulation succeeded: %s steps, risk=%.2f",
                len(plan),
                data.get("total_risk_score", 0),
            )
            data["_backend"] = "aether"
            return data
    except httpx.TimeoutException:
        logger.warning("AETHER backend timed out after %ds", CONFIG["AETHER_TIMEOUT"])
    except httpx.HTTPStatusError as exc:
        logger.warning("AETHER backend returned HTTP %s: %s", exc.response.status_code, exc.response.text[:200])
    except httpx.RequestError as exc:
        logger.warning("AETHER backend unreachable: %s", exc)
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("AETHER backend returned malformed response: %s", exc)

    return None


async def _check_aether_health() -> Optional[Dict[str, Any]]:
    """Check if AETHER backend is healthy. Returns health dict or None."""
    endpoint = CONFIG["AETHER_ENDPOINT"].rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(endpoint)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.debug("AETHER health check failed: %s", exc)
        return None


# ===================================================================
# TIER 2: LLM backend (language model API)
# ===================================================================

async def _call_llm(
    messages: List[Dict[str, str]],
    system_prompt: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> Optional[str]:
    """Call the LLM API via HermesAdapter. Returns text content or None on failure."""
    try:
        from partner.adapter import HermesAdapter
    except ImportError:
        logger.warning("LLM backend: HermesAdapter not available (run from partner_world_model directory)")
        return None

    # Build full prompt from messages
    workspace = CONFIG.get("PARTNER_WORKSPACE",
                          os.environ.get("PARTNER_WORKSPACE",
                                         os.path.join(os.path.dirname(__file__), "..", "instances", "05")))
    try:
        # Build full prompt from messages
        full_text = ""
        if system_prompt:
            full_text = system_prompt + "\n\n"
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            full_text += f"{role}: {content}\n\n"

        adapter = HermesAdapter(
            workspace_path=workspace,
            model=CONFIG.get("LLM_MODEL", "deepseek-v4-flash"),
            provider=CONFIG.get("LLM_PROVIDER", "deepseek"),
        )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: adapter.chat(full_text, purpose="world_model"),
        )
        if result:
            logger.debug("LLM call succeeded via HermesAdapter: %d chars", len(result))
            return result.strip()
        logger.warning("LLM call returned empty result")
        return None
    except Exception as exc:
        logger.warning("LLM call failed via HermesAdapter: %s", exc)
        return None


async def _llm_simulate(plan: List[Dict], state: Dict) -> Optional[Dict]:
    """Use LLM to simulate plan execution. Returns parsed dict or None."""
    llm_text = await _call_llm(
        messages=[
            {
                "role": "user",
                "content": (
                    "You are a world model simulator. Given the following plan and current state, "
                    "simulate the execution and predict outcomes.\n\n"
                    f"Plan: {json.dumps(plan, indent=2)}\n\n"
                    f"Current State: {json.dumps(state, indent=2)}\n\n"
                    "Return a JSON object with:\n"
                    "- 'status': 'simulated' or 'error'\n"
                    "- 'plan_length': int\n"
                    "- 'total_risk_score': float (0-10)\n"
                    "- 'total_estimated_duration_minutes': float\n"
                    "- 'overall_assessment': string\n"
                    "- 'steps': list of dicts with 'step_index', 'action', 'target', 'risk_score', "
                    "'estimated_duration_minutes', 'likely_outcome'\n"
                    "- 'warnings': list of strings\n"
                    "- 'state_after': dict of predicted final state\n\n"
                    "Respond with ONLY valid JSON."
                ),
            }
        ],
        system_prompt="You are a precise world model simulator. Return ONLY valid JSON.",
    )

    if llm_text is None:
        return None

    try:
        # Strip markdown code fences if present
        cleaned = llm_text.strip()
        if cleaned.startswith("```"):
            # Remove markdown code fences (```json ... ``` or just ``` ... ```)
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
            cleaned = cleaned.strip()
        result = json.loads(cleaned)
        result["source"] = "llm"
        result["_backend"] = "llm"
        logger.info("LLM simulation result parsed successfully")
        return result
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON response, discarding")
        return None


async def _check_llm_health() -> Dict[str, Any]:
    """Check if LLM backend is available via HermesAdapter."""
    try:
        probe = await asyncio.wait_for(
            _call_llm(
                messages=[{"role": "user", "content": "Respond with just the word 'ok'."}],
                max_tokens=10,
            ),
            timeout=15.0,
        )
    except (asyncio.TimeoutError, Exception):
        probe = None
    return {
        "available": probe is not None,
        "model": CONFIG.get("LLM_MODEL", "deepseek-v4-flash"),
        "adapter": "HermesAdapter",
    }


# ===================================================================
# TIER 3: Heuristic fallback (always available)
# ===================================================================

def _heuristic_simulate(plan: List[Dict], state: Dict) -> Dict:
    """Heuristic simulation when AETHER and LLM are unavailable."""
    results: List[Dict] = []
    total_risk = 0.0
    total_duration = 0.0
    warnings: List[str] = []

    for i, step in enumerate(plan):
        action = step.get("action", "unknown")
        target = step.get("target", "")
        params = step.get("parameters", {})

        risk = 0.1  # base risk per step
        duration = 2.0  # base minutes per step

        if action in ("delete", "remove", "drop", "rm"):
            risk += 0.5
            duration += 1.0
            warnings.append(f"Step {i}: Destructive action '{action}' has elevated risk")
        elif action in ("write", "create", "add", "insert"):
            risk += 0.15
            duration += 0.5
        elif action in ("read", "get", "list", "search"):
            risk += 0.05
            duration += 0.3
        elif action in ("modify", "update", "edit", "patch"):
            risk += 0.3
            duration += 1.0
        elif action in ("execute", "run", "deploy"):
            risk += 0.6
            duration += 3.0
            warnings.append(f"Step {i}: Execution action '{action}' may have side effects")
        elif action == "test":
            risk += 0.2
            duration += 1.0

        results.append({
            "step_index": i,
            "action": action,
            "target": target,
            "risk_score": round(risk, 2),
            "estimated_duration_minutes": round(duration, 1),
            "likely_outcome": "success" if risk < 0.4 else "needs_review",
        })
        total_risk += risk
        total_duration += duration

    return {
        "status": "simulated",
        "source": "heuristic",
        "_backend": "heuristic",
        "plan_length": len(plan),
        "total_risk_score": round(total_risk, 2),
        "total_estimated_duration_minutes": round(total_duration, 1),
        "overall_assessment": "low_risk" if total_risk < len(plan) * 0.3 else "needs_review",
        "steps": results,
        "warnings": warnings,
        "state_after": {**state, "_simulated": True},
    }


def _heuristic_optimize(plan: List[Dict], state: Dict) -> Dict:
    """Heuristic plan optimization suggestions."""
    suggestions: List[str] = []
    reordered = list(plan)

    # Check for destructive actions
    has_destructive = any(
        s.get("action") in ("delete", "remove", "drop", "rm") for s in plan
    )

    # Check for read-before-write
    has_read_before_write = False
    for i, step in enumerate(plan):
        action = step.get("action", "")
        if action in ("read", "get", "list"):
            has_read_before_write = True
        elif action in ("write", "create") and not has_read_before_write and i > 0:
            suggestions.append(
                f"Step {i}: Consider adding a 'read' step before '{action}' to verify state"
            )

    if has_destructive:
        suggestions.append(
            "Destructive actions detected: consider adding confirmation steps or backups"
        )

    # Check for duplicate actions on same target
    seen_targets: Dict[str, int] = {}
    for i, step in enumerate(plan):
        target = step.get("target", "")
        action = step.get("action", "")
        key = f"{action}:{target}"
        if key in seen_targets:
            suggestions.append(
                f"Duplicate '{action}' on '{target}' at steps {seen_targets[key]} and {i}"
            )
        else:
            seen_targets[key] = i

    # Suggest parallelization for independent read steps
    read_steps = [
        (i, s) for i, s in enumerate(plan) if s.get("action") in ("read", "get", "list")
    ]
    if len(read_steps) > 2:
        suggestions.append(
            f"Steps {[s[0] for s in read_steps]} are independent 'read' actions "
            "and can be parallelized"
        )

    return {
        "status": "optimized",
        "source": "heuristic",
        "_backend": "heuristic",
        "suggestions": suggestions,
        "reordered_plan": reordered,
        "estimated_improvement": f"{len(suggestions)} optimizations identified",
    }


def _enrich_simulation_result(result: dict, plan: list) -> dict:
    """Post-process any simulation result to ensure it has suggestions,
    per_step_risk, and parallel_recommendation in the format batch_planner expects.

    This makes every backend (AETHER, LLM, Heuristic) produce the same structured output.
    """
    result = dict(result)  # shallow copy
    plan = list(plan or [])

    # --- Ensure per_step_risk ---
    if "per_step_risk" not in result or not result["per_step_risk"]:
        per_step_risk = []
        for step in plan:
            action = step.get("action", step.get("event_type", "unknown"))
            risk = 0.3  # default moderate risk
            if action in ("delete", "remove", "drop", "rm"):
                risk = 0.7
            elif action in ("execute", "run", "deploy"):
                risk = 0.6
            elif action in ("write", "create", "add", "insert"):
                risk = 0.35
            elif action in ("read", "get", "list", "search", "atomic_http_get"):
                risk = 0.2
            elif action in ("modify", "update", "edit", "patch"):
                risk = 0.4
            elif "convert" in action.lower():
                risk = 0.2
            elif "write" in action.lower() or "artifact" in action.lower():
                risk = 0.2
            elif "llm" in action.lower() or "structured" in action.lower():
                risk = 0.3
            per_step_risk.append({"action": action, "risk": risk})
        result["per_step_risk"] = per_step_risk

    # --- Generate suggestions from per_step_risk ---
    if "suggestions" not in result or not result["suggestions"]:
        suggestions = []
        per_step = result.get("per_step_risk", [])

        # 1. High-risk steps get modify_parameter (timeout + max_retries)
        for item in per_step:
            action = item.get("action", "")
            risk = item.get("risk", 0.3)
            if risk > 0.5:
                suggestions.append({
                    "type": "modify_parameter",
                    "target": action,
                    "param": "timeout",
                    "value": max(30, int(risk * 60)),
                    "reason": f"高风险步骤 ({risk:.2f})，自动增加超时保护",
                })
                suggestions.append({
                    "type": "modify_parameter",
                    "target": action,
                    "param": "max_retries",
                    "value": 2,
                    "reason": f"高风险步骤 ({risk:.2f})，自动增加重试保护",
                })

        # 2. Parallel recommendation for independent read/search steps
        read_actions = {"atomic_http_get", "http_get", "search", "read", "get", "list"}
        read_steps = []
        step_actions = [s.get("action", s.get("event_type", "")) for s in plan]
        for i, a in enumerate(step_actions):
            if a in read_actions:
                read_steps.append((i, a))
        if len(read_steps) >= 3:
            result["parallel_recommendation"] = "parallel"
            if not any(s.get("type") == "reorder" for s in suggestions):
                suggestions.append({
                    "type": "reorder",
                    "strategy": "parallel_safe_first",
                    "reason": f"{len(read_steps)} 个独立读取步骤可并行执行",
                })

        # 3. If plan has report generation but no intermediate analysis, suggest add_step
        has_report = any("report" in a.lower() or "write" in a.lower() or "convert" in a.lower()
                         for a in step_actions)
        has_analysis = any("analysis" in a.lower() or "analyze" in a.lower() or "structured" in a.lower()
                           for a in step_actions)
        if has_report and not has_analysis and len(plan) < 6:
            suggestions.append({
                "type": "add_step",
                "event": "smart_llm_structured_action",
                "target": "before_report",
                "reason": "报告生成前缺少结构化数据分析步骤，建议添加",
            })

            suggestions.append({
                "type": "modify_parameter",
                "target": "smart_llm_structured_action",
                "param": "timeout",
                "value": 120,
                "reason": "LLM分析步骤需要更长的超时时间",
            })

        result["suggestions"] = suggestions

    # --- Ensure parallel_recommendation ---
    if "parallel_recommendation" not in result or not result.get("parallel_recommendation"):
        result["parallel_recommendation"] = "serial"

    # --- Ensure frames_generated for AETHER compatibility ---
    result.setdefault("frames_generated", 0)

    # --- Normalize status for batch_planner compatibility ---
    raw_status = str(result.get("status", "")).lower().strip()
    if raw_status in ("simulated", "ok"):
        result["status"] = "success"

    return result


# ===================================================================
# Multi-plan candidate generation (改进 2)
# ===================================================================

async def _generate_candidate_plans(
    original_plan: List[Dict[str, Any]],
    state: Dict[str, Any],
    num_candidates: int = 3,
) -> List[List[Dict[str, Any]]]:
    """Generate multiple candidate plan variants using the LLM backend.

    Uses the LLM to produce alternative plans based on the original,
    then returns all candidates for simulation-based selection.
    """

    steps_summary = "\n".join(
        f"  {i}. action={s.get('action', s.get('event_type', '?'))} "
        f"params={json.dumps(s.get('parameters', {}), ensure_ascii=False)[:80]}"
        for i, s in enumerate(original_plan)
    )

    prompt = (
        f"You are a plan optimization expert. Given the following original plan "
        f"and user request, generate {num_candidates} alternative plan variants.\n\n"
        f"Original plan ({len(original_plan)} steps):\n{steps_summary}\n\n"
        f"User request:\n{state.get('user_message', 'N/A')}\n\n"
        "For each variant, adjust the plan to improve it in a different way:\n"
        "- Variant 1: More parallel steps (reduce latency)\n"
        "- Variant 2: More safety checks (add validation, audit steps)\n"
        "- Variant 3: More thorough search (add extra search queries, cross-reference)\n\n"
        "Return ONLY a JSON array of plan variants, where each variant is a list of step dicts. "
        "Each step dict must have: 'action' (string), 'parameters' (dict), 'depends_on' (list of int indices).\n"
        "Steps that modify the same file should be serial (have depends_on). "
        "Independent read/search steps should have no dependencies.\n\n"
        "Example format:\n"
        '[\n'
        '  [{"action": "search", "parameters": {"query": "..."}, "depends_on": []}, ...],\n'
        '  [{"action": "search", "parameters": {"query": "..."}, "depends_on": []}, ...],\n'
        '  ...\n'
        ']\n\n'
        "Respond with ONLY the JSON array. No markdown, no explanation."
    )

    llm_text = await _call_llm(
        messages=[{"role": "user", "content": prompt}],
        system_prompt="You are a plan optimization expert. Return ONLY valid JSON arrays.",
        temperature=0.7,
        max_tokens=4096,
    )

    if not llm_text:
        logger.info("candidate plans: LLM unavailable, using original plan only")
        return [list(original_plan)]

    try:
        # Strip markdown code fences
        cleaned = llm_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
            cleaned = cleaned.strip()
        candidates = json.loads(cleaned)
        if not isinstance(candidates, list) or len(candidates) == 0:
            raise ValueError("not a non-empty array")
        # Validate each candidate is a list of step dicts
        valid = []
        for c in candidates:
            if isinstance(c, list) and len(c) > 0 and all(isinstance(s, dict) for s in c):
                # Normalize step format
                normalized = []
                for s in c:
                    normalized.append({
                        "action": str(s.get("action", s.get("event_type", "unknown"))),
                        "parameters": s.get("parameters", {}),
                        "depends_on": s.get("depends_on", []),
                    })
                valid.append(normalized)
        if len(valid) == 0:
            raise ValueError("no valid candidates")
        logger.info("candidate plans: generated %d variants (from %d raw)", len(valid), len(candidates))
        # Include the original plan as baseline
        result = [list(original_plan)] + valid
        return result[:num_candidates + 1]  # original + N candidates
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("candidate plans: LLM returned invalid JSON: %s", e)
        return [list(original_plan)]


async def _select_best_plan(
    candidates: List[List[Dict[str, Any]]],
    state: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Select the best plan from candidates by simulating each and comparing risk scores.

    Returns:
        Tuple of (best_plan, best_simulation_result)
    """
    if len(candidates) == 1:
        # Only one plan, just simulate and return
        sim = await simulate_plan(candidates[0], state)
        return candidates[0], sim

    results = []
    for i, plan in enumerate(candidates):
        try:
            sim = await simulate_plan(plan, state)
            risk = sim.get("total_risk_score", sim.get("risk", 10))
            step_count = len(plan)
            suggestions = sim.get("suggestions", [])
            has_parallel = sim.get("parallel_recommendation") == "parallel"

            # Score: lower risk + fewer steps + has parallel + has suggestions = better
            score = risk * 2 + step_count * 0.5
            if has_parallel:
                score -= 1.0
            if suggestions:
                score -= 0.3 * len(suggestions)

            results.append((score, i, plan, sim))
            logger.info("candidate %d: risk=%.2f, steps=%d, parallel=%s, score=%.2f",
                        i, risk, step_count, has_parallel, score)
        except Exception as exc:
            logger.warning("candidate %d simulation failed: %s", i, exc)
            results.append((999, i, plan, {"status": "error", "error": str(exc)}))

    # Sort by score ascending
    results.sort(key=lambda x: x[0])
    best_score, best_idx, best_plan, best_sim = results[0]
    logger.info("best candidate: index=%d, score=%.2f", best_idx, best_score)
    return best_plan, best_sim


# ===================================================================
# The three-tier simulate orchestrator
# ===================================================================

async def simulate_plan(
    plan: List[Dict[str, Any]], state: Dict[str, Any]
) -> Dict[str, Any]:
    """Simulate plan execution with AETHER → LLM → Heuristic fallback chain.

    Args:
        plan: List of step dicts, each with 'action', 'target', optional 'parameters'.
        state: Current environment state dict.

    Returns:
        Dict with simulation results, source info, and fallback indicators.
    """
    logger.info(
        "simulate_plan: %d steps, mode=%s, state keys=%s",
        len(plan),
        CONFIG["MODE"],
        list(state.keys()),
    )

    mode = CONFIG["MODE"]

    # ---- Tier 1: AETHER ----
    if mode in ("hybrid", "aether_only"):
        logger.info("Trying AETHER backend...")
        aether_result = await _call_aether(plan, state)
        if aether_result is not None:
            aether_result['fallback'] = False
            aether_result['fallback_chain'] = ['aether']
            return _enrich_simulation_result(aether_result, plan)
        logger.info("AETHER unavailable, fallback in chain")

        if mode == "aether_only":
            # No fallback allowed — return error with heuristic as annotation
            heuristic = _heuristic_simulate(plan, state)
            return {
                "status": "error",
                "source": "aether_only",
                "_backend": "none",
                "fallback": True,
                "fallback_chain": ["aether"],
                "error": "AETHER backend unavailable (mode=aether_only, no fallback)",
                "heuristic_hint": heuristic,
            }

    # ---- Tier 2: LLM ----
    if mode in ("hybrid", "llm_only") and CONFIG["FALLBACK_TO_LLM"]:
        logger.info("Trying LLM backend...")
        llm_result = await _llm_simulate(plan, state)
        if llm_result is not None:
            llm_result['fallback'] = mode == 'hybrid'
            llm_result['fallback_chain'] = ['aether', 'llm'] if mode == 'hybrid' else ['llm']
            return _enrich_simulation_result(llm_result, plan)
        logger.info("LLM unavailable, falling to heuristic")

        if mode == "llm_only":
            heuristic = _heuristic_simulate(plan, state)
            return {
                "status": "error",
                "source": "llm_only",
                "_backend": "none",
                "fallback": True,
                "fallback_chain": ["aether", "llm"],
                "error": "LLM backend unavailable (mode=llm_only, no fallback)",
                "heuristic_hint": heuristic,
            }

    # ---- Tier 3: Heuristic (always available) ----
    logger.info("Using heuristic fallback")
    result = _heuristic_simulate(plan, state)
    result["fallback"] = True
    result["fallback_chain"] = ["heuristic"]
    # Show which higher tiers were attempted
    attempted = []
    if mode in ("hybrid", "aether_only"):
        attempted.append("aether")
    if mode in ("hybrid", "llm_only") and CONFIG["FALLBACK_TO_LLM"]:
        attempted.append("llm")
    result['tiers_attempted'] = attempted
    return _enrich_simulation_result(result, plan)


async def optimize_plan(plan: List[Dict], state: Dict) -> Dict:
    """Optimize a plan. Uses LLM if available, else heuristic."""
    logger.info("optimize_plan: %d steps", len(plan))

    mode = CONFIG["MODE"]

    # Try LLM for optimize (AETHER doesn't do text optimization)
    if mode in ("hybrid", "llm_only") and CONFIG["FALLBACK_TO_LLM"]:
        llm_text = await _call_llm(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are a plan optimization expert. Given the following plan and current state, "
                        "suggest optimizations to improve efficiency, reduce risk, and avoid errors.\n\n"
                        f"Plan: {json.dumps(plan, indent=2)}\n\n"
                        f"Current State: {json.dumps(state, indent=2)}\n\n"
                        "Return a JSON object with:\n"
                        "- 'status': 'optimized' or 'error'\n"
                        "- 'source': 'llm' or 'heuristic'\n"
                        "- 'suggestions': list of strings, each describing an optimization\n"
                        "- 'reordered_plan': list of step dicts in suggested new order\n"
                        "- 'estimated_improvement': string describing expected improvement\n\n"
                        "Respond with ONLY valid JSON."
                    ),
                }
            ],
            system_prompt="You are a precise plan optimizer. Return ONLY valid JSON.",
        )

        if llm_text:
            try:
                result = json.loads(llm_text)
                result["source"] = "llm"
                result["_backend"] = "llm"
                logger.info("optimize_plan: LLM result obtained")
                return result
            except json.JSONDecodeError:
                logger.warning("optimize_plan: LLM returned non-JSON, falling back")

    logger.info("optimize_plan: using heuristic fallback")
    return _heuristic_optimize(plan, state)


async def health_check() -> Dict[str, Any]:
    """Comprehensive health check of all backends."""
    aether_health = await _check_aether_health()
    llm_health = await _check_llm_health()

    aether_available = aether_health is not None and aether_health.get("status") in ("ok", "degraded")
    llm_available = llm_health.get("available", False)

    mode = CONFIG["MODE"]

    return {
        "status": "healthy",
        "server": "World Model Server (AETHER Hybrid)",
        "version": "2.0.0",
        "mode": mode,
        "current_backend": _determine_current_backend(aether_available, llm_available, mode),
        "backends": {
            "aether": {
                "available": aether_available,
                "endpoint": CONFIG["AETHER_ENDPOINT"],
                "model_loaded": aether_health.get("model_loaded", False) if aether_health else False,
                "details": aether_health,
            },
            "llm": {
                "available": llm_available,
                "configured": True,
                "model": CONFIG["LLM_MODEL"],
                "endpoint": "HermesAdapter (no separate endpoint)",
            },
            "heuristic": {
                "available": True,
                "description": "Rule-based fallback, always available",
            },
        },
        "config": {
            "fallback_to_llm": CONFIG["FALLBACK_TO_LLM"],
            "max_simulation_steps": CONFIG["MAX_SIMULATION_STEPS"],
        },
        "uptime": time.time() - _start_time,
    }


def _determine_current_backend(
    aether_avail: bool, llm_avail: bool, mode: str
) -> str:
    """Determine which backend would be tried first."""
    if mode == "aether_only":
        return "aether" if aether_avail else "none (degraded)"
    if mode == "llm_only":
        return "llm" if llm_avail else "none (degraded)"
    if mode == "heuristic_only":
        return "heuristic"
    # hybrid
    if aether_avail:
        return "aether (with llm + heuristic fallback)"
    if llm_avail:
        return "llm (with heuristic fallback)"
    return "heuristic"


# ===================================================================
# MCP / FastAPI server (conditional on imports available)
# ===================================================================

_mcp_server = None  # type: ignore[var-annotated]
_fastapi_app = None  # type: ignore[var-annotated]

try:
    from mcp.server.fastmcp import FastMCP
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import JSONResponse as StarletteJSONResponse, Response as StarletteResponse

    _mcp_server = FastMCP(
        "World Model Server (AETHER Hybrid)",
        host=CONFIG["HOST"],
        port=CONFIG["PORT"],
        log_level="INFO",
        debug=False,
    )

    @_mcp_server.tool()
    async def simulate_plan_tool(plan: List[Dict], state: Dict) -> Dict:
        """Simulate executing a plan with AETHER → LLM → Heuristic fallback."""
        return await simulate_plan(plan, state)

    @_mcp_server.tool()
    async def health_tool() -> Dict:
        """Health check — shows backend availability and current mode."""
        return await health_check()

    @_mcp_server.tool()
    async def optimize_plan_tool(plan: List[Dict], state: Dict) -> Dict:
        """Suggest plan optimizations (LLM → Heuristic)."""
        return await optimize_plan(plan, state)

    @_mcp_server.custom_route("/simulate", methods=["POST"])
    async def _rest_simulate(request: StarletteRequest) -> StarletteResponse:
        try:
            body = await request.json()
            plan = body.get("plan", [])
            state = body.get("state", {})
            result = await simulate_plan(plan, state)
            return StarletteJSONResponse(result)
        except Exception as e:
            logger.error("REST /simulate error: %s", e)
            return StarletteJSONResponse({"status": "error", "error": str(e)}, status_code=500)

    @_mcp_server.custom_route("/optimize", methods=["POST"])
    async def _rest_optimize(request: StarletteRequest) -> StarletteResponse:
        try:
            body = await request.json()
            plan = body.get("plan", [])
            state = body.get("state", {})
            result = await optimize_plan(plan, state)
            return StarletteJSONResponse(result)
        except Exception as e:
            logger.error("REST /optimize error: %s", e)
            return StarletteJSONResponse({"status": "error", "error": str(e)}, status_code=500)

    @_mcp_server.custom_route("/candidate_plans", methods=["POST"])
    async def _rest_candidate_plans(request: StarletteRequest) -> StarletteResponse:
        try:
            body = await request.json()
            plan = body.get("plan", [])
            state = body.get("state", {})
            num_candidates = int(body.get("num_candidates", 3))
            candidates = await _generate_candidate_plans(plan, state, num_candidates)
            best_plan, best_sim = await _select_best_plan(candidates, state)
            all_sims = []
            for c in candidates:
                sim = await simulate_plan(c, state)
                all_sims.append({"plan": c, "simulation": sim})
            return StarletteJSONResponse({
                "status": "ok",
                "num_candidates": len(candidates),
                "candidates": all_sims,
                "best_plan": best_plan,
                "best_simulation": best_sim,
                "recommendation": "使用候选计划 0 为原始计划，候选计划 1-N 为 LLM 生成的优化变体。已自动选择最优计划。",
            })
        except Exception as e:
            logger.error("REST /candidate_plans error: %s", e)
            return StarletteJSONResponse({"status": "error", "error": str(e)}, status_code=500)

    @_mcp_server.custom_route("/health", methods=["GET"])
    async def _rest_health(request: StarletteRequest) -> StarletteResponse:
        try:
            result = await health_check()
            return StarletteJSONResponse(result)
        except Exception as e:
            logger.error("REST /health error: %s", e)
            return StarletteJSONResponse({"status": "error", "error": str(e)}, status_code=500)

    HAS_MCP = True
    logger.info("MCP imports available — using MCP transport")

except ImportError:
    HAS_MCP = False
    logger.info("MCP imports not available — using FastAPI standalone server")
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    _fastapi_app = FastAPI(
        title="World Model Server (AETHER Hybrid)",
        version="2.0.0",
    )

    class _SimulateRequestBody(BaseModel):
        plan: List[Dict[str, Any]]
        state: Dict[str, Any]

    class _OptimizeRequestBody(BaseModel):
        plan: List[Dict[str, Any]]
        state: Dict[str, Any]

    @_fastapi_app.post("/simulate")
    async def _fastapi_simulate(body: _SimulateRequestBody):
        return await simulate_plan(body.plan, body.state)

    @_fastapi_app.post("/optimize")
    async def _fastapi_optimize(body: _OptimizeRequestBody):
        return await optimize_plan(body.plan, body.state)

    class _CandidatePlansRequestBody(BaseModel):
        plan: List[Dict[str, Any]]
        state: Dict[str, Any]
        num_candidates: int = 3

    @_fastapi_app.post("/candidate_plans")
    async def _fastapi_candidate_plans(body: _CandidatePlansRequestBody):
        candidates = await _generate_candidate_plans(body.plan, body.state, body.num_candidates)
        best_plan, best_sim = await _select_best_plan(candidates, body.state)
        all_sims = []
        for c in candidates:
            sim = await simulate_plan(c, body.state)
            all_sims.append({"plan": c, "simulation": sim})
        return {
            "status": "ok",
            "num_candidates": len(candidates),
            "candidates": all_sims,
            "best_plan": best_plan,
            "best_simulation": best_sim,
            "recommendation": "使用候选计划 0 为原始计划，候选计划 1-N 为 LLM 生成的优化变体。已自动选择最优计划。",
        }

    @_fastapi_app.get("/health")
    async def _fastapi_health():
        return await health_check()


# ===================================================================
# Main entrypoint
# ===================================================================

def main() -> None:
    """Start the world model server."""
    logger.info("=" * 60)
    logger.info("World Model Server (AETHER Hybrid) v2.0.0")
    logger.info("=" * 60)
    logger.info("Host: %s:%s", CONFIG["HOST"], CONFIG["PORT"])
    logger.info("Mode: %s", CONFIG["MODE"])
    logger.info("AETHER endpoint: %s", CONFIG["AETHER_ENDPOINT"])
    logger.info("LLM API configured: %s (via HermesAdapter)", True)
    logger.info("LLM model: %s", CONFIG.get("LLM_MODEL"))
    logger.info("Heuristic fallback: always available")
    logger.info("Logging to: %s", _log_path)
    logger.info("REST endpoints: POST /simulate, POST /optimize, POST /candidate_plans, GET /health")

    if HAS_MCP and _mcp_server is not None:
        logger.info("Transport: streamable-http (MCP)")
        _mcp_server.run(transport="streamable-http")
    elif _fastapi_app is not None:
        import uvicorn
        logger.info("Transport: FastAPI (uvicorn)")
        uvicorn.run(_fastapi_app, host=CONFIG["HOST"], port=CONFIG["PORT"])
    else:
        logger.error("No server backend available — cannot start")


if __name__ == "__main__":
    main()
