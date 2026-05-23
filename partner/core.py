"""Core - the Partner orchestrator that ties everything together."""

import os
import json
from datetime import datetime
from typing import Optional

from .config import PartnerConfig
from .task_queue import TaskQueue, Task
from .knowledge import KnowledgeBase, KnowledgeEntry
from .journal import Journal, JournalEntry
from .state import StateManager
from .adapter import AgentAdapter, create_adapter
from .conversation import ConversationEngine


class Partner:
    """The main Partner class - an autonomous research companion.
    
    Partner works independently in the background and talks to you
    when you ask: "What have you been doing?"
    """
    
    def __init__(self, config: PartnerConfig):
        self.config = config
        self.workspace = config.workspace.path
        
        # Ensure workspace structure
        state_dir = os.path.join(self.workspace, "state")
        os.makedirs(state_dir, exist_ok=True)
        os.makedirs(os.path.join(self.workspace, "knowledge"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace, "ideas"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace, "logs"), exist_ok=True)
        
        # Initialize components
        self.task_queue = TaskQueue(os.path.join(state_dir, "task_queue.json"))
        self.knowledge = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
        self.journal = Journal(os.path.join(state_dir, "journal.jsonl"))
        self.state = StateManager(state_dir)
        self.adapter = create_adapter(config.agent.backend, self.workspace)
        self.conversation = ConversationEngine(
            self.journal, self.knowledge, self.task_queue, self.state
        )
    
    def start(self):
        """Start Partner as a background process."""
        print(f"🤝 Partner is starting...")
        print(f"   Workspace: {self.workspace}")
        print(f"   Backend: {self.config.agent.backend}")
        print(f"   Interval: {self.config.scheduler.interval_minutes} minutes")
        
        # Check for crash recovery
        if self.state.detect_crash():
            print("⚠️  Detected previous crash. Recovering...")
            self._recover()
        
        # Mark as alive
        self.state.heartbeat(status="idle")
        
        # Save config
        config_path = os.path.join(self.workspace, "partner_config.json")
        self.config.save(config_path)
        
        print("✅ Partner is running. Open Hermes and say 'partner 最近在研究什么？'")
    
    def run_cycle(self) -> Optional[str]:
        """Run one research cycle. Returns summary of what was done."""
        self.state.heartbeat(status="working")
        
        # Get next task
        task = self.task_queue.get_next()
        if not task:
            self.state.heartbeat(status="idle")
            return None
        
        # Create checkpoint before starting
        self.state.create_checkpoint(
            "before_task",
            self.task_queue.path,
            self.knowledge.path,
        )
        
        # Execute task
        result = None
        try:
            result = self._execute_task(task)
            self.task_queue.complete(task.id, result)
            
            # Log to journal
            self.journal.log(JournalEntry(
                task_id=task.id,
                task_type=task.type,
                task_title=task.title,
                result_summary=result[:500],
                new_tasks_generated=0,
                knowledge_entries_added=0,
            ))
            
            # Update stats
            stats = self.state.load_stats()
            stats["total_tasks_completed"] = stats.get("total_tasks_completed", 0) + 1
            self.state.update_stats(stats)
            
        except Exception as e:
            self.task_queue.fail(task.id, str(e))
            self.journal.log(JournalEntry(
                task_id=task.id,
                task_type=task.type,
                task_title=f"FAILED: {task.title}",
                result_summary=str(e),
            ))
        
        self.state.heartbeat(status="idle")
        return result if result is not None else str(e)
    
    def _execute_task(self, task: Task) -> str:
        """Execute a single task via the agent adapter."""
        prompt = f"""Execute this research task and return the results:

Task: {task.title}
Type: {task.type}
Description: {task.description}

Requirements:
- Be thorough and specific
- Include sources/references where possible
- If searching literature, extract key findings with paper titles and years
- If analyzing a project, note specific metrics and improvements
- Return results in a structured format

Respond in the same language as the task description."""
        
        return self.adapter.execute_task(prompt)
    
    def chat(self, message: str) -> str:
        """Talk to Partner."""
        return self.conversation.respond(message)
    
    def status(self) -> str:
        """Get Partner's current status."""
        return self.conversation._handle_status()
    
    def add_task(self, title: str, description: str, 
                 task_type: str = "deep_dive", priority: int = 5) -> str:
        """Add a new research task."""
        task = Task(
            type=task_type,
            title=title,
            description=description,
            priority=priority,
        )
        return self.task_queue.add_task(task)
    
    def _recover(self):
        """Recover from a crash."""
        latest_cp = self.state.get_latest_checkpoint()
        if latest_cp:
            success = self.state.restore_from_checkpoint(
                latest_cp, self.task_queue.path, self.knowledge.path
            )
            if success:
                # Reload components
                self.task_queue._load()
                self.knowledge._load()
                print(f"✅ Recovered from checkpoint: {latest_cp}")
            else:
                print(f"❌ Failed to recover from checkpoint: {latest_cp}")
        else:
            print("ℹ️  No checkpoint found. Starting fresh.")
