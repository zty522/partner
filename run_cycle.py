#!/usr/bin/env python3
"""Partner + Hermes integration runner.

This script bridges Partner and Hermes. It:
1. Loads Partner's state from a workspace
2. Picks the next task
3. Executes it using hermes_tools (if available) or prints instructions
4. Updates Partner's state with the result

Usage:
    python run_cycle.py [--workspace PATH]

Designed to be called by Hermes cron or manually.
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Add partner to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from partner.task_queue import TaskQueue, Task
from partner.knowledge import KnowledgeBase, KnowledgeEntry
from partner.journal import Journal, JournalEntry
from partner.state import StateManager


def run_cycle(workspace: str) -> dict:
    """Run one Partner research cycle using Hermes tools."""
    state_dir = os.path.join(workspace, "state")
    
    # Initialize components
    tq = TaskQueue(os.path.join(state_dir, "task_queue.json"))
    kb = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
    journal = Journal(os.path.join(state_dir, "journal.jsonl"))
    sm = StateManager(state_dir)
    
    # Mark as working
    sm.heartbeat(status="working")
    
    # Get next task
    task = tq.get_next()
    if not task:
        sm.heartbeat(status="idle")
        return {"status": "no_tasks", "message": "No pending tasks."}
    
    print(f"📋 Task: {task.title}")
    print(f"   Type: {task.type}")
    print(f"   Priority: {task.priority}")
    print(f"   Description: {task.description[:200]}")
    print()
    
    # Create checkpoint
    sm.create_checkpoint("before_task", tq.path, kb.path)
    
    # Execute task
    result = execute_task(task, workspace)
    
    # Update state
    tq.complete(task.id, result.get("summary", ""))
    
    # Add knowledge entries
    for k in result.get("knowledge", []):
        kb.add(KnowledgeEntry(
            title=k.get("title", ""),
            content=k.get("content", ""),
            source=k.get("source", task.title),
            category=k.get("category", "findings"),
            confidence=k.get("confidence", "medium"),
            tags=k.get("tags", [task.type]),
        ))
    
    # Log to journal
    journal.log(JournalEntry(
        task_id=task.id,
        task_type=task.type,
        task_title=task.title,
        result_summary=result.get("summary", "")[:500],
        new_tasks_generated=result.get("new_tasks", 0),
        knowledge_entries_added=len(result.get("knowledge", [])),
    ))
    
    # Generate new tasks
    for new_task in result.get("follow_up_tasks", []):
        tq.add_task(Task(
            title=new_task.get("title", ""),
            description=new_task.get("description", ""),
            type=new_task.get("type", task.type),
            priority=new_task.get("priority", task.priority - 1),
        ))
    
    # Update stats
    stats = sm.load_stats()
    stats["total_tasks_completed"] = stats.get("total_tasks_completed", 0) + 1
    stats["total_cycles"] = stats.get("total_cycles", 0) + 1
    stats["last_run"] = datetime.now().isoformat()
    sm.update_stats(stats)
    
    # Mark idle
    sm.heartbeat(status="idle")
    
    return {
        "status": "completed",
        "task": task.title,
        "summary": result.get("summary", ""),
        "knowledge_added": len(result.get("knowledge", [])),
        "new_tasks": len(result.get("follow_up_tasks", [])),
    }


def execute_task(task: Task, workspace: str) -> dict:
    """Execute a research task using available tools."""
    
    # Try to use hermes_tools
    try:
        from hermes_tools import terminal, web_search, read_file
        return execute_with_hermes(task, workspace, terminal, web_search, read_file)
    except ImportError:
        pass
    
    # Fallback: print instructions for manual execution
    return {
        "summary": f"Task '{task.title}' queued. hermes_tools not available. "
                   f"Run this inside a Hermes session to execute.",
        "knowledge": [],
        "follow_up_tasks": [],
    }


def execute_with_hermes(task, workspace, terminal, web_search, read_file) -> dict:
    """Execute task using hermes_tools."""
    result = {
        "summary": "",
        "knowledge": [],
        "follow_up_tasks": [],
    }
    
    task_type = task.type
    
    if task_type == "literature_search":
        result = do_literature_search(task, web_search)
    elif task_type == "project_scan":
        result = do_project_scan(task, read_file, terminal)
    elif task_type == "knowledge_synthesis":
        result = do_knowledge_synthesis(task, workspace)
    elif task_type == "idea_generation":
        result = do_idea_generation(task, workspace)
    elif task_type == "skill_learning":
        result = do_skill_learning(task, web_search)
    elif task_type == "deep_dive":
        result = do_deep_dive(task, web_search, read_file, workspace)
    else:
        result = do_general_task(task, web_search, terminal)
    
    return result


def do_literature_search(task, web_search) -> dict:
    """Search for literature on a topic."""
    query = task.description or task.title
    
    print(f"🔍 Searching: {query}")
    search_result = web_search(query)
    
    # Parse results
    results_text = str(search_result)
    
    summary = f"Literature search completed for: {task.title}\n\nResults:\n{results_text[:2000]}"
    
    knowledge = [{
        "title": f"文献搜索: {task.title}",
        "content": results_text[:3000],
        "source": "Web search",
        "category": "findings",
        "confidence": "medium",
        "tags": [task.type] + task.tags,
    }]
    
    # Generate follow-up tasks based on findings
    follow_ups = [{
        "title": f"深入分析搜索结果: {task.title}",
        "description": f"基于搜索结果，深入分析最相关的论文和方法",
        "type": "deep_dive",
        "priority": task.priority - 1,
    }]
    
    return {"summary": summary, "knowledge": knowledge, "follow_up_tasks": follow_ups}


def do_project_scan(task, read_file, terminal) -> dict:
    """Scan a project directory."""
    # Extract project path from description
    desc = task.description
    
    print(f"📂 Scanning project...")
    
    # List files
    try:
        ls_result = terminal("ls -la")
        files = str(ls_result)
    except:
        files = "Could not list files"
    
    summary = f"Project scan completed: {task.title}\n\nFiles found:\n{files[:1000]}"
    
    return {
        "summary": summary,
        "knowledge": [{
            "title": f"项目扫描: {task.title}",
            "content": summary,
            "source": "Project scan",
            "category": "findings",
            "confidence": "high",
        }],
        "follow_up_tasks": [],
    }


def do_knowledge_synthesis(task, workspace) -> dict:
    """Synthesize existing knowledge."""
    kb_path = os.path.join(workspace, "state", "knowledge.json")
    try:
        with open(kb_path) as f:
            kb_data = json.load(f)
        entries = kb_data.get("entries", [])
        summary = f"Knowledge synthesis: {len(entries)} entries available"
    except:
        summary = "No knowledge base found"
    
    return {"summary": summary, "knowledge": [], "follow_up_tasks": []}


def do_idea_generation(task, workspace) -> dict:
    """Generate research ideas."""
    return {
        "summary": f"Idea generation task: {task.title}\nDescription: {task.description}",
        "knowledge": [{
            "title": f"研究想法: {task.title}",
            "content": task.description,
            "source": "Idea generation",
            "category": "concepts",
            "confidence": "medium",
        }],
        "follow_up_tasks": [],
    }


def do_skill_learning(task, web_search) -> dict:
    """Learn a new skill or tool."""
    query = task.description or task.title
    print(f"📖 Learning: {query}")
    result = web_search(query)
    
    return {
        "summary": f"Skill learning: {task.title}\n\n{str(result)[:2000]}",
        "knowledge": [{
            "title": f"技能学习: {task.title}",
            "content": str(result)[:3000],
            "source": "Web search + documentation",
            "category": "tools",
            "confidence": "medium",
        }],
        "follow_up_tasks": [],
    }


def do_deep_dive(task, web_search, read_file, workspace) -> dict:
    """Deep dive into a specific topic."""
    query = task.description or task.title
    print(f"🔬 Deep dive: {query}")
    
    # Search
    search_result = web_search(query)
    results_text = str(search_result)[:3000]
    
    return {
        "summary": f"Deep dive completed: {task.title}\n\n{results_text[:1500]}",
        "knowledge": [{
            "title": f"深入研究: {task.title}",
            "content": results_text,
            "source": "Deep dive research",
            "category": "findings",
            "confidence": "medium",
            "tags": [task.type] + task.tags,
        }],
        "follow_up_tasks": [{
            "title": f"总结 {task.title} 的关键发现",
            "description": "整理深入研究的核心发现",
            "type": "knowledge_synthesis",
            "priority": task.priority - 1,
        }],
    }


def do_general_task(task, web_search, terminal) -> dict:
    """General task execution."""
    return {
        "summary": f"General task: {task.title}",
        "knowledge": [],
        "follow_up_tasks": [],
    }


def main():
    parser = argparse.ArgumentParser(description="Partner + Hermes cycle runner")
    parser.add_argument("--workspace", "-w", required=True, help="Partner workspace path")
    args = parser.parse_args()
    
    result = run_cycle(args.workspace)
    print(f"\n{'='*50}")
    print(f"Result: {result['status']}")
    if result.get("task"):
        print(f"Task: {result['task']}")
    if result.get("summary"):
        print(f"Summary: {result['summary'][:300]}")
    print(f"{'='*50}")
    
    # Output JSON for programmatic consumption
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
