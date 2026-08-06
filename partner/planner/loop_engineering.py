"""Loop Engineering — Goal-driven iterative execution loop for Event pipelines."""

from __future__ import annotations
import logging, time
from typing import Any

logger = logging.getLogger(__name__)

class LoopEngine:
    """Iterative execution loop driven by goal satisfaction."""
    
    def __init__(self, max_iterations: int = 5, max_total_timeout: int = 1800):
        self.max_iterations = max_iterations
        self.max_total_timeout = max_total_timeout
        self._start_time = time.time()
    
    async def run(self, user_message: str, execute_plan_fn, **kwargs) -> dict:
        """Run the goal-driven loop until all subgoals are achieved or max iterations reached."""
        from .goal_parser import parse_goal, validate_subgoals, analyze_gap, generate_new_plan
        
        subgoals = parse_goal(user_message)
        logger.info("[LOOP_ENG] Parsed %d subgoals from: %s", len(subgoals), user_message[:60])
        
        current_plan = None
        iteration_results = []
        iteration = 0
        
        for iteration in range(self.max_iterations):
            # Check timeout
            if time.time() - self._start_time > self.max_total_timeout:
                logger.warning("[LOOP_ENG] Timeout after %ds", self.max_total_timeout)
                break
            
            logger.info("[LOOP_ENG] Iteration %d/%d", iteration + 1, self.max_iterations)
            
            # Execute current plan
            result = await execute_plan_fn(user_message=user_message, iteration=iteration, current_plan=current_plan, **kwargs)
            iteration_results.append(result)
            
            # Validate against subgoals
            artifacts = result if isinstance(result, dict) else {}
            statuses = validate_subgoals(artifacts, subgoals)
            
            achieved = sum(1 for s in statuses if s.status == "achieved")
            logger.info("[LOOP_ENG] Subgoals: %d/%d achieved", achieved, len(subgoals))
            
            # Check if all achieved
            if achieved == len(subgoals):
                logger.info("[LOOP_ENG] All subgoals achieved!")
                break
            
            # Analyze gaps and generate new plan
            hypotheses = analyze_gap(statuses)
            if not hypotheses:
                logger.info("[LOOP_ENG] No hypotheses generated, stopping")
                break
            
            current_plan = generate_new_plan(hypotheses, current_plan)
            logger.info("[LOOP_ENG] Generated %d new plan steps", len(current_plan))
        
        return {
            "iterations": iteration + 1,
            "subgoals_achieved": sum(1 for s in validate_subgoals({}, subgoals) if s.status == "achieved"),
            "subgoals_total": len(subgoals),
            "iteration_results": iteration_results,
        }
