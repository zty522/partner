"""Event Engine - executes Events and manages their lifecycle.

The EventEngine is the core runtime that:
1. Selects the next Event to execute
2. Runs each phase sequentially
3. Handles failures and recovery
4. Spawns follow-up Events
"""

import json
import os
from datetime import datetime
from typing import List, Optional, Dict, Any

from .event import Event, EventPhase, EventStatus, PhaseType
from .event_templates import TemplateRegistry
from .task_queue import TaskQueue
from .knowledge import KnowledgeBase
from .journal import Journal, JournalEntry
from .state import StateManager


class EventEngine:
    """Core engine for executing Events."""

    def __init__(self, state_dir: str, task_queue: TaskQueue,
                 knowledge: KnowledgeBase, journal: Journal,
                 state: StateManager, templates_dir: str = None):
        self.state_dir = state_dir
        self.events_path = os.path.join(state_dir, "events.json")
        self.tq = task_queue
        self.kb = knowledge
        self.journal = journal
        self.state = state
        self.registry = TemplateRegistry(user_dir=templates_dir)
        self.events: List[Event] = []
        self._load_events()

    def _load_events(self):
        """Load events from events.json.

        Events with empty phases get their phases resolved from the template.
        """
        try:
            with open(self.events_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            events_data = data.get("events", []) if isinstance(data, dict) else data
            self.events = []
            for e_data in events_data:
                event = Event.from_dict(e_data)
                # Resolve phases from template if empty
                if not event.phases and event.template:
                    template = self.registry.get(event.template)
                    if template:
                        import copy
                        event.phases = [copy.deepcopy(p) for p in template.phases]
                self.events.append(event)
        except (FileNotFoundError, json.JSONDecodeError):
            self.events = []

    def _save_events(self):
        """Save events to events.json."""
        data = {
            "meta": {
                "version": "1.0.0",
                "total_events_created": len(self.events),
                "last_updated": datetime.now().isoformat(),
            },
            "events": [e.to_dict() for e in self.events],
        }
        os.makedirs(os.path.dirname(self.events_path), exist_ok=True)
        with open(self.events_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_event(self, event: Event) -> str:
        """Add a new event to the queue."""
        self.events.append(event)
        self._save_events()
        return event.id

    def has_pending_events(self) -> bool:
        """Check if there are pending events."""
        return any(e.status == EventStatus.PENDING.value for e in self.events)

    def select_next(self) -> Optional[Event]:
        """Select the highest priority pending event."""
        now = datetime.now()
        pending = [e for e in self.events if e.status == EventStatus.PENDING.value]

        # Filter out expired events
        valid = []
        for e in pending:
            if e.is_expired:
                e.status = EventStatus.EXPIRED.value
            else:
                valid.append(e)

        if not valid:
            return None

        # Score events: priority + freshness + chain bonus + knowledge gap bonus
        def score(e: Event) -> float:
            s = e.priority * 10
            # Freshness bonus: newer events get a small boost
            age_hours = (now - datetime.fromisoformat(e.created_at)).total_seconds() / 3600
            s += max(0, 5 - age_hours * 0.5)
            # Chain bonus: events spawned by other events get continuity bonus
            if e.meta.get("parent_event_id"):
                s += 3
            # Knowledge gap bonus: events targeting low-coverage topics get priority boost
            topic = e.inputs.get("topic", "")
            if topic and self.kb:
                coverage = self.kb.topic_coverage(topic)
                s += (1 - coverage) * 5  # Coverage越低，加分越高，最高+5
            return s

        valid.sort(key=score, reverse=True)
        return valid[0]

    def run_event(self, event: Event) -> Dict[str, Any]:
        """Execute an event's phases sequentially. Returns result dict."""
        event.status = EventStatus.RUNNING.value
        event.started_at = datetime.now().isoformat()
        self._save_events()

        results = {
            "event_id": event.id,
            "template": event.template,
            "phases_completed": 0,
            "phases_failed": 0,
            "knowledge_entries": [],
            "new_events": [],
        }

        for phase in event.phases:
            phase.status = "running"
            phase.started_at = datetime.now().isoformat()

            try:
                phase_result = self._execute_phase(phase, event)
                phase.status = "completed"
                phase.completed_at = datetime.now().isoformat()
                phase.result = phase_result
                results["phases_completed"] += 1

                # Collect knowledge entries from extraction phases
                if phase.type == PhaseType.KNOWLEDGE_EXTRACTION.value:
                    entries = phase_result.get("knowledge_entries", [])
                    results["knowledge_entries"].extend(entries)

            except Exception as e:
                phase.status = "failed"
                phase.error = str(e)
                event.status = EventStatus.FAILED.value
                results["phases_failed"] += 1
                self._save_events()
                return results

        event.status = EventStatus.COMPLETED.value
        event.completed_at = datetime.now().isoformat()
        event.outputs = {
            "knowledge_entries": results["knowledge_entries"],
            "followup_events": results["new_events"],
        }
        self._save_events()

        return results

    def _execute_phase(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Execute a single phase. Returns phase result dict.

        This is the core dispatch logic. Each phase type has different behavior.
        The actual work (web search, file reading, etc.) is delegated to the
        Partner agent via task_queue or handled inline.
        """
        phase_type = phase.type

        if phase_type == PhaseType.LITERATURE_SEARCH.value:
            return self._phase_literature_search(phase, event)
        elif phase_type == PhaseType.KNOWLEDGE_EXTRACTION.value:
            return self._phase_knowledge_extraction(phase, event)
        elif phase_type == PhaseType.KNOWLEDGE_SYNTHESIS.value:
            return self._phase_knowledge_synthesis(phase, event)
        elif phase_type == PhaseType.IDEA_GENERATION.value:
            return self._phase_idea_generation(phase, event)
        elif phase_type == PhaseType.PROJECT_SCAN.value:
            return self._phase_project_scan(phase, event)
        elif phase_type == PhaseType.EVENT_GENERATION.value:
            return self._phase_event_generation(phase, event)
        elif phase_type == PhaseType.KNOWLEDGE_SCAN.value:
            return self._phase_knowledge_scan(phase, event)
        elif phase_type == PhaseType.GAP_ANALYSIS.value:
            return self._phase_gap_analysis(phase, event)
        elif phase_type == PhaseType.PLANNING.value:
            return self._phase_planning(phase, event)
        elif phase_type == PhaseType.EXPLORATION.value:
            return self._phase_exploration(phase, event)
        else:
            # Unknown phase type: create a task for the agent to handle
            return self._phase_to_task(phase, event)

    def _phase_to_task(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Convert a phase to a task in the task queue for agent execution."""
        from .task_queue import Task
        task = Task(
            type=phase.type,
            title=f"[Event {event.id}] {phase.name}: {phase.description}",
            description=phase.prompt or phase.description,
            priority=event.priority,
            tags=event.inputs.get("tags", []) + ["event_phase"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id}

    def _phase_literature_search(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Create a literature search task."""
        topic = event.inputs.get("topic", phase.config.get("topic", ""))
        max_queries = phase.config.get("max_queries", 3)
        from .task_queue import Task
        task = Task(
            type="literature_search",
            title=f"文献搜索: {topic}",
            description=phase.prompt.format(topic=topic) if "{topic}" in phase.prompt else f"搜索关于 {topic} 的最新文献，最多 {max_queries} 个查询",
            priority=event.priority + 1,
            tags=["event_phase", "literature"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id, "topic": topic}

    def _phase_knowledge_extraction(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Create a knowledge extraction task."""
        from .task_queue import Task
        task = Task(
            type="knowledge_synthesis",
            title=f"知识提取: {event.template}",
            description=phase.prompt or f"从搜索结果中提取关键发现，整合到知识库",
            priority=event.priority,
            tags=["event_phase", "extraction"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id, "knowledge_entries": []}

    def _phase_knowledge_synthesis(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Create a knowledge synthesis task."""
        from .task_queue import Task
        task = Task(
            type="knowledge_synthesis",
            title=f"知识整合: {event.template}",
            description=phase.prompt or "整合新发现到知识库，找到关联和空白",
            priority=event.priority - 1,
            tags=["event_phase", "synthesis"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id}

    def _phase_idea_generation(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Create an idea generation task."""
        from .task_queue import Task
        task = Task(
            type="idea_generation",
            title=f"创意生成: {event.template}",
            description=phase.prompt or "基于现有知识生成改进建议",
            priority=event.priority,
            tags=["event_phase", "idea"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id}

    def _phase_project_scan(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Create a project scan task."""
        project = event.inputs.get("project", phase.config.get("project", ""))
        from .task_queue import Task
        task = Task(
            type="project_scan",
            title=f"项目扫描: {project}",
            description=phase.prompt or f"扫描项目 {project} 的当前状态",
            priority=event.priority,
            tags=["event_phase", "scan"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id}

    def _phase_event_generation(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Generate follow-up events based on execution results."""
        max_events = phase.config.get("max_events", 2)
        new_events = self._spawn_events(event, max_events)
        return {"events_spawned": [e.id for e in new_events]}

    def _phase_knowledge_scan(self, phase: PhaseType, event: Event) -> Dict[str, Any]:
        """Scan knowledge base for outdated/low-confidence entries."""
        from .task_queue import Task
        task = Task(
            type="knowledge_synthesis",
            title="知识库扫描",
            description=phase.prompt or "扫描知识库所有条目，标记过时、低置信度、重复的条目",
            priority=event.priority,
            tags=["event_phase", "knowledge_scan"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id}

    def _phase_gap_analysis(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Analyze knowledge gaps."""
        from .task_queue import Task
        task = Task(
            type="knowledge_synthesis",
            title="知识空白分析",
            description=phase.prompt or "识别知识库中的空白领域",
            priority=event.priority,
            tags=["event_phase", "gap_analysis"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id}

    def _phase_planning(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Create a planning task."""
        from .task_queue import Task
        task = Task(
            type="idea_generation",
            title=f"规划: {event.template}",
            description=phase.prompt or "制定下一步计划",
            priority=event.priority,
            tags=["event_phase", "planning"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id}

    def _phase_exploration(self, phase: EventPhase, event: Event) -> Dict[str, Any]:
        """Create an exploration task."""
        from .task_queue import Task
        task = Task(
            type="deep_dive",
            title=f"探索: {event.template}",
            description=phase.prompt or "执行探索性任务",
            priority=event.priority,
            tags=["event_phase", "exploration"],
        )
        self.tq.add_task(task)
        return {"task_created": task.id}

    def _spawn_events(self, parent_event: Event, max_events: int = 2) -> List[Event]:
        """Generate follow-up events based on completed event."""
        new_events = []

        # Rule 1: If template has spawn phase, use its config
        spawn_phases = [p for p in parent_event.phases
                        if p.type == PhaseType.EVENT_GENERATION.value]

        # Rule 2: Auto-generate based on template type
        template_name = parent_event.template

        if template_name == "literature-deep-dive":
            # After literature search, suggest idea exploration
            topic = parent_event.inputs.get("topic", "")
            template = self.registry.get("idea-exploration")
            if template and len(new_events) < max_events:
                evt = template.create(
                    inputs={"context": f"基于 {topic} 的文献研究结果"},
                    priority=6,
                    triggered_by="event_chain",
                    parent_event_id=parent_event.id,
                )
                self.add_event(evt)
                new_events.append(evt)

        elif template_name == "project-health-check":
            # After project scan, suggest synthesis review
            template = self.registry.get("synthesis-review")
            if template and len(new_events) < max_events:
                evt = template.create(
                    priority=5,
                    triggered_by="event_chain",
                    parent_event_id=parent_event.id,
                )
                self.add_event(evt)
                new_events.append(evt)

        # Rule 3: Every 5 completed events, suggest synthesis review
        completed_count = sum(1 for e in self.events
                              if e.status == EventStatus.COMPLETED.value)
        if completed_count > 0 and completed_count % 5 == 0:
            template = self.registry.get("synthesis-review")
            if template and len(new_events) < max_events:
                # Avoid duplicate synthesis reviews
                recent_synthesis = any(
                    e.template == "synthesis-review" and e.status == EventStatus.PENDING.value
                    for e in self.events
                )
                if not recent_synthesis:
                    evt = template.create(priority=5, triggered_by="auto_rule")
                    self.add_event(evt)
                    new_events.append(evt)

        return new_events

    def recover(self, event_id: str = None):
        """Recover a stuck event. If no event_id, recover all stuck events."""
        now = datetime.now()
        for event in self.events:
            if event_id and event.id != event_id:
                continue
            if event.status == EventStatus.RUNNING.value:
                # Check if it's been running too long (2x estimated time)
                if event.started_at:
                    started = datetime.fromisoformat(event.started_at)
                    elapsed_hours = (now - started).total_seconds() / 3600
                    if elapsed_hours > 1:  # More than 1 hour = likely stuck
                        # Mark running phases as pending for retry
                        for phase in event.phases:
                            if phase.status == "running":
                                phase.status = "pending"
                        event.status = EventStatus.PENDING.value
                        event.started_at = None
        self._save_events()

    def stats(self) -> Dict[str, Any]:
        """Get event statistics."""
        total = len(self.events)
        by_status = {}
        by_template = {}
        for e in self.events:
            by_status[e.status] = by_status.get(e.status, 0) + 1
            by_template[e.template] = by_template.get(e.template, 0) + 1

        return {
            "total_events": total,
            "by_status": by_status,
            "by_template": by_template,
            "templates_available": self.registry.list_names(),
        }

    def auto_generate(self):
        """Auto-generate events when queue is empty, prioritizing knowledge gaps."""
        # Generate a synthesis review if we have enough knowledge entries
        kb_stats = self.kb.stats() if hasattr(self.kb, 'stats') else {}
        total_entries = kb_stats.get("total", kb_stats.get("total_entries", 0))

        if total_entries > 10:
            template = self.registry.get("synthesis-review")
            if template:
                # Check if one is already pending
                existing = any(
                    e.template == "synthesis-review" and e.status == EventStatus.PENDING.value
                    for e in self.events
                )
                if not existing:
                    evt = template.create(priority=4, triggered_by="auto_generate")
                    self.add_event(evt)
                    return evt

        # Use knowledge gaps to find the best topic for a literature deep dive
        best_topic = None
        best_priority = 4

        if self.kb and hasattr(self.kb, 'find_gaps'):
            gaps = self.kb.find_gaps(min_gap_count=3)
            if gaps:
                # Pick the highest-priority gap
                best_gap = gaps[0]
                best_topic = best_gap["topic"]
                best_priority = min(8, best_gap.get("suggested_priority", 4))

        # Fallback: pick a random topic from knowledge tags with low coverage
        if not best_topic and self.kb and hasattr(self.kb, 'knowledge_distribution'):
            dist = self.kb.knowledge_distribution()
            gap_tags = [item["tag"] for item in dist.get("coverage_summary", [])
                        if item["level"] == "gap"]
            if gap_tags:
                best_topic = gap_tags[0]
                best_priority = 6

        # Final fallback: generic topic
        if not best_topic:
            best_topic = "recent advances in autonomous agents"

        template = self.registry.get("literature-deep-dive")
        if template:
            evt = template.create(
                inputs={"topic": best_topic},
                priority=best_priority,
                triggered_by="auto_generate",
            )
            self.add_event(evt)
            return evt

        return None
