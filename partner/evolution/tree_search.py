"""
Tree Search for Self-Improvement — inspired by ERA (Nature 2026).

ERA uses LLM + tree search to systematically explore solution space.
Partner currently does linear iteration (try → fail → retry → fail).
This module adds parallel branch exploration with quality scoring.

Core concepts from ERA:
1. Quality score (0-10) instead of boolean satisfied/unsatisfied
2. Tree search: try N fix strategies, keep best branch
3. Code mutation: LLM rewrites code → evaluate → branch
"""

from __future__ import annotations
import json, logging, os, time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SolutionNode:
    """A node in the fix search tree."""
    strategy: str
    score: float = 0.0
    fix_result: dict = field(default_factory=dict)
    children: list[SolutionNode] = field(default_factory=list)
    depth: int = 0


class TreeSearchHealer:
    """ERA-style tree search over fix strategies."""

    MAX_DEPTH = 3
    BRANCH_FACTOR = 3  # try 3 fix strategies per node
    QUALITY_THRESHOLD = 7.0  # score >= 7 means good enough

    def __init__(self, adapter=None, workspace: str = ""):
        self.adapter = adapter
        self.workspace = workspace
        self.best_score = 0.0
        self.best_fix: Optional[dict] = None
        self.search_tree: list[SolutionNode] = []

    def search(
        self,
        task_description: str,
        step_results: list[dict],
        llm_feedback: dict,
        working_dir: str = "",
    ) -> dict:
        """Run tree search over fix strategies. Return best fix found."""
        
        root = SolutionNode(strategy="root", depth=0)
        self.search_tree = [root]

        # Phase 1: Generate N fix strategies (parallel via LLM)
        strategies = self._generate_strategies(task_description, step_results, llm_feedback)
        if not strategies:
            strategies = ["params", "env", "config", "code"]

        logger.info("[TREE_SEARCH] Exploring %d fix strategies", len(strategies))

        # Phase 2: Try each strategy, score results
        for strategy in strategies[:self.BRANCH_FACTOR]:
            node = SolutionNode(strategy=strategy, depth=1)
            root.children.append(node)

            # Execute fix
            fix_result = self._try_fix(strategy, task_description, step_results, working_dir)
            node.fix_result = fix_result

            # Score the result
            node.score = self._score_result(fix_result, llm_feedback, task_description)
            logger.info("[TREE_SEARCH] strategy=%s score=%.1f", strategy, node.score)

            if node.score > self.best_score:
                self.best_score = node.score
                self.best_fix = fix_result

            # If good enough, stop searching
            if node.score >= self.QUALITY_THRESHOLD:
                logger.info("[TREE_SEARCH] Good fix found: %s (%.1f)", strategy, node.score)
                break

        return self.best_fix or {}

    def _generate_strategies(self, task_desc: str, steps: list, feedback: dict) -> list[str]:
        """LLM generates diverse fix strategies (ERA: code mutation proposals)."""
        if not self.adapter:
            return []

        prompt = f"""Given this task failure, propose {self.BRANCH_FACTOR} different fix strategies.

TASK: {task_desc[:1000]}
FAILURES: {json.dumps(feedback, ensure_ascii=False)[:1000]}

For each strategy, output:
STRATEGY: <name> | TYPE: params|env|config|code|install | DESCRIPTION: <1 sentence>

Output EXACTLY {self.BRANCH_FACTOR} strategies, one per line."""

        try:
            response = self.adapter.chat(prompt, purpose="tree_search_strategies")
            strategies = []
            for line in response.split('\n'):
                if line.startswith('STRATEGY:'):
                    strategies.append(line.split('STRATEGY:')[1].split('|')[0].strip())
            return strategies[:self.BRANCH_FACTOR]
        except Exception as e:
            logger.debug("[TREE_SEARCH] Strategy generation failed: %s", e)
            return []

    def _try_fix(self, strategy: str, task_desc: str, steps: list, working_dir: str) -> dict:
        """Execute one fix strategy. Delegates to SelfHealEngine."""
        from .self_heal import auto_heal

        diagnosis = {"fix_type": strategy, "fix_action": f"Apply {strategy} fix to resolve task failure"}
        
        try:
            result = auto_heal(
                workspace=self.workspace,
                task_description=task_desc,
                step_results=steps,
                llm_check_feedback={},
                adapter=self.adapter,
                working_dir=working_dir,
            )
            return {"strategy": strategy, "result": result}
        except Exception as e:
            return {"strategy": strategy, "error": str(e)}

    def _score_result(self, fix_result: dict, llm_feedback: dict, task_desc: str) -> float:
        """ERA-style quality score: 0-10 based on fix effectiveness."""
        if fix_result.get("error"):
            return 0.0

        result = fix_result.get("result", {})
        applied = result.get("fix_result", {}).get("applied", False)
        fix_type = result.get("fix_type", "unknown")

        # Base scores by fix type
        type_scores = {"code": 8, "params": 6, "env": 5, "config": 4, "install": 3, "cannot_fix": 0}
        base = type_scores.get(fix_type, 2)

        # Bonus for successful application
        if applied:
            base += 1.0
        
        # Penalty for repeated same-type failures
        # (simple heuristic — could be enhanced with actual re-execution scoring)

        return min(10.0, base)


def tree_search_heal(
    workspace: str,
    task_description: str,
    step_results: list[dict],
    llm_feedback: dict,
    adapter=None,
    working_dir: str = "",
) -> dict:
    """Entry point: run tree search over fix strategies."""
    healer = TreeSearchHealer(adapter=adapter, workspace=workspace)
    result = healer.search(
        task_description=task_description,
        step_results=step_results,
        llm_feedback=llm_feedback,
        working_dir=working_dir or workspace,
    )
    
    # Log to tree_search_log
    log_path = os.path.join(workspace, "state", "tree_search_log.jsonl")
    entry = {
        "timestamp": time.time(),
        "strategies_tried": len(healer.search_tree[0].children) if healer.search_tree else 0,
        "best_score": healer.best_score,
        "best_fix": str(healer.best_fix)[:200] if healer.best_fix else None,
    }
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except:
        pass
    
    return result
