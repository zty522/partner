"""Core - the Partner orchestrator that ties everything together."""

import os
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from .config import PartnerConfig
from .task_queue import TaskQueue, Task
from .knowledge import KnowledgeBase, KnowledgeEntry
from .journal import Journal, JournalEntry
from .state import StateManager
from .adapter import AgentAdapter, create_adapter
from .conversation import ConversationEngine
from .event_engine import EventEngine
from .event import Event, EventStatus
from .self_evolution import SelfEvolutionEngine


class Partner:
    """The main Partner class - an autonomous research companion.
    
    Partner works independently in the background and talks to you
    when you ask: "What have you been doing?"
    
    Execution priority:
    1. Events (structured multi-phase research cycles)
    2. Tasks (atomic operations)
    3. Auto-generate new Events when both are empty
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
            self.journal, self.knowledge, self.task_queue, self.state,
            workspace=self.workspace,
        )
        
        # Initialize Event Engine
        self.event_engine = EventEngine(
            state_dir=state_dir,
            task_queue=self.task_queue,
            knowledge=self.knowledge,
            journal=self.journal,
            state=self.state,
        )

        # Initialize Self-Evolution Engine
        self.self_evolution = SelfEvolutionEngine(state_dir=state_dir)
        self._cycle_count = 0
    
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
        """Run one research cycle. Returns summary of what was done.
        
        Priority: Event > Task > auto-generate Event
        
        After execution, checks for proactive notifications.
        """
        self.state.heartbeat(status="working")
        
        result = None
        
        # --- Priority 1: Execute pending Events ---
        if self.event_engine.has_pending_events():
            event = self.event_engine.select_next()
            if event:
                try:
                    result = self._execute_event(event)
                except Exception as e:
                    event.status = EventStatus.FAILED.value
                    self.event_engine._save_events()
                    self.journal.log(JournalEntry(
                        task_id=event.id,
                        task_type="event",
                        task_title=f"FAILED Event: {event.template}",
                        result_summary=str(e),
                    ))
                    # Fall through to try a task instead
        
        # --- Priority 2: Execute pending Tasks ---
        if result is None:
            task = self.task_queue.get_next()
            if task:
                # Create checkpoint before starting
                self.state.create_checkpoint(
                    "before_task",
                    self.task_queue.path,
                    self.knowledge.path,
                )
                
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
                    result = str(e)
        
        # --- Priority 3: Auto-generate new Events ---
        if result is None:
            new_event = self.event_engine.auto_generate()
            if new_event:
                result = f"Auto-generated event: {new_event.template} ({new_event.id})"
        
        # --- Phase 3: Self-evolution cycle ---
        try:
            self._cycle_count += 1
            evo_result = self.self_evolution.run_evolution_cycle()
            if evo_result:
                if result:
                    result += f"\n\n🧬 自我进化周期:\n{evo_result}"
                else:
                    result = f"🧬 自我进化周期:\n{evo_result}"
        except Exception as e:
            logger.warning(f"Self-evolution cycle failed: {e}")

        # --- Phase 4: Check proactive notifications ---
        try:
            notifications = self.conversation.check_proactive()
            if notifications:
                # Store for user to see on next interaction
                notif_file = os.path.join(self.workspace, "state", "pending_notifications.json")
                existing = []
                try:
                    with open(notif_file, "r", encoding="utf-8") as f:
                        existing = json.loads(f.read(), strict=False)
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
                existing.extend(notifications)
                with open(notif_file, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                if result:
                    result += f"\n\n📬 生成了 {len(notifications)} 条主动通知"
        except Exception as e:
            logger.warning(f"Proactive notification check failed: {e}")
        
        self.state.heartbeat(status="idle")
        return result
    
    def _execute_event(self, event: Event) -> str:
        """Execute an Event through the EventEngine.
        
        Returns a summary of what was done.
        """
        self.state.heartbeat(status="working", task_id=event.id)
        
        # Create checkpoint
        self.state.create_checkpoint(
            "before_event",
            self.event_engine.events_path,
            self.knowledge.path,
        )
        
        # Run the event
        result = self.event_engine.run_event(event)
        
        # Build summary
        phases_done = result.get("phases_completed", 0)
        phases_failed = result.get("phases_failed", 0)
        new_events_count = len(result.get("new_events", []))
        knowledge_count = len(result.get("knowledge_entries", []))
        
        summary = (
            f"Event [{event.template}] 完成: "
            f"{phases_done} 个阶段成功, {phases_failed} 个失败. "
            f"新增 {knowledge_count} 条知识, {new_events_count} 个后续事件."
        )
        
        # Log to journal
        self.journal.log(JournalEntry(
            task_id=event.id,
            task_type="event",
            task_title=f"Event: {event.template}",
            result_summary=summary[:500],
            new_tasks_generated=new_events_count,
            knowledge_entries_added=knowledge_count,
        ))
        
        # Update stats using StateManager helper
        self.state.record_event_completed(
            template_name=event.template,
            phases_done=phases_done,
            new_events_spawned=new_events_count,
        )
        
        self.state.heartbeat(status="idle")
        return summary
    
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
    
    def add_event(self, template_name: str, inputs: dict = None, 
                  priority: int = None) -> str:
        """Add a new Event from a template."""
        template = self.event_engine.registry.get(template_name)
        if not template:
            raise ValueError(f"Unknown event template: {template_name}")
        event = template.create(inputs=inputs, priority=priority)
        return self.event_engine.add_event(event)
    
    def _recover(self):
        """Recover from a crash."""
        # Recover stuck events first
        self.event_engine.recover()
        
        latest_cp = self.state.get_latest_checkpoint()
        if latest_cp:
            success = self.state.restore_from_checkpoint(
                latest_cp, self.task_queue.path, self.knowledge.path
            )
            if success:
                # Reload components
                self.task_queue._load()
                self.knowledge._load()
                self.event_engine._load_events()
                print(f"✅ Recovered from checkpoint: {latest_cp}")
            else:
                print(f"❌ Failed to recover from checkpoint: {latest_cp}")
        else:
            print("ℹ️  No checkpoint found. Starting fresh.")
