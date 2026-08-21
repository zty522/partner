"""
Self-heal hook for executor.py (Sprint 7).

Instead of patching executor.py directly (which gets wiped by git checkout),
this module provides the hook function that executor imports.
No modification to executor.py needed — just add one import line.

Usage in executor.py:
    from partner.evolution.self_heal_hook import try_self_heal_on_failure

Then in the core_step_failed block:
    healed = try_self_heal_on_failure(
        workspace=_workspace, task_description=root_request or title,
        step_results=step_list, llm_check_feedback=check_result,
        adapter=_adapter, working_dir=getattr(task, 'working_dir', '') or '',
    )
    if healed:
        core_step_failed_across_iterations = False
"""

from __future__ import annotations
import logging, os, json
from typing import Any

logger = logging.getLogger(__name__)


def try_self_heal_on_failure(
    workspace: str = "",
    task_description: str = "",
    step_results: list = None,
    llm_check_feedback: Any = "",
    adapter: Any = None,
    working_dir: str = "",
) -> bool:
    """
    Try self-heal + tree_search on a failed core step.
    
    Returns True if a fix was applied and the task should retry.
    Returns False if no fix could be applied.
    
    Safe to call from executor.py with a single line:
        healed = try_self_heal_on_failure(...)
    """
    
    step_list = step_results or []
    
    # 1. Try self_heal
    try:
        from .self_heal import auto_heal
        hr = auto_heal(
            workspace=workspace,
            task_description=task_description,
            step_results=step_list,
            llm_check_feedback=llm_check_feedback,
            adapter=adapter,
            working_dir=working_dir,
        )
        if isinstance(hr, dict) and hr.get("should_retry") and hr.get("fix_result", {}).get("applied"):
            logger.info("[SELFHEAL-HOOK] Applied: %s", hr.get("root_cause", "")[:120])
            return True
    except ImportError:
        logger.debug("[SELFHEAL-HOOK] self_heal module not available")
    except Exception as e:
        logger.debug("[SELFHEAL-HOOK] self_heal failed: %s", e)
    
    # 2. Try tree_search
    try:
        from .tree_search import tree_search_heal
        tr = tree_search_heal(
            workspace=workspace,
            task_description=task_description,
            step_results=step_list,
            llm_feedback=llm_check_feedback,
            adapter=adapter,
            working_dir=working_dir,
        )
        if tr:
            logger.info("[SELFHEAL-HOOK] Tree search found fix")
            return True
    except ImportError:
        logger.debug("[SELFHEAL-HOOK] tree_search module not available")
    except Exception as e:
        logger.debug("[SELFHEAL-HOOK] tree_search failed: %s", e)
    
    return False


def apply_self_heal_to_executor(executor_path: str) -> bool:
    """
    One-time tool to inject the self-heal hook import into executor.py.
    Only needs to be run once — subsequent git checkouts can re-run this.
    """
    if not os.path.exists(executor_path):
        return False
    
    with open(executor_path) as f:
        content = f.read()
    
    # Check if already patched
    if 'from partner.evolution.self_heal_hook import' in content:
        return True  # Already done
    
    # Add import after existing evolution imports
    hook_import = '\nfrom partner.evolution.self_heal_hook import try_self_heal_on_failure'
    
    # Find the old self-heal block and replace with hook call
    old_pattern = 'if core_step_failed_across_iterations:\n                logger.info("[ITERATION] core agent step failed; trying self-heal+tree'
    
    if old_pattern in content:
        block_start = content.find(old_pattern)
        # Find the `break` that ends this block
        break_pos = content.find('\n                break', block_start)
        if break_pos < 0:
            break_pos = content.find('\n                    break', block_start)
        
        if break_pos > 0:
            old_block = content[block_start:break_pos]
            
            new_block = '''if core_step_failed_across_iterations:
                logger.info("[ITERATION] core step failed; trying self-heal task_id=%s", task.task_id)
                healed = try_self_heal_on_failure(
                    workspace=_workspace, task_description=root_request or title,
                    step_results=step_list, llm_check_feedback=check_result,
                    adapter=_adapter, working_dir=getattr(task, "working_dir", "") or "",
                )
                if healed:
                    core_step_failed_across_iterations = False
                else:'''
            
            content = content.replace(old_block, new_block)
            
            # Add import
            if 'from partner.evolution' in content:
                content = content.replace(
                    'from partner.evolution',
                    hook_import + '\nfrom partner.evolution',
                    1
                )
            else:
                # Add near other imports
                content = content.replace(
                    'import logging\n',
                    'import logging\n' + hook_import + '\n',
                    1
                )
            
            with open(executor_path, 'w') as f:
                f.write(content)
            
            return True
    
    return False
