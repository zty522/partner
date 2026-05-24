"""EventEngine runtime integration test.

Verifies the full execution flow:
load events -> select highest priority -> execute phases -> mark complete -> generate follow-up event.
Uses mock data and temp directories to avoid real network requests and state pollution.
"""

import os
import sys
import json
import copy
import shutil
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from partner.event import Event, EventPhase, EventTemplate, EventStatus, PhaseType
from partner.event_engine import EventEngine
from partner.event_templates import TemplateRegistry
from partner.task_queue import TaskQueue, Task
from partner.knowledge import KnowledgeBase, KnowledgeEntry
from partner.journal import Journal, JournalEntry
from partner.state import StateManager


def make_temp_workspace():
    """Create a temp workspace with all required state files."""
    workspace = tempfile.mkdtemp(prefix="event_engine_test_")
    state_dir = os.path.join(workspace, "state")
    os.makedirs(state_dir, exist_ok=True)
    os.makedirs(os.path.join(state_dir, "checkpoints"), exist_ok=True)

    # Initialize empty state files
    with open(os.path.join(state_dir, "task_queue.json"), "w") as f:
        json.dump([], f)
    with open(os.path.join(state_dir, "knowledge.json"), "w") as f:
        json.dump({"meta": {"total_entries": 0, "last_updated": ""}, "entries": []}, f)
    with open(os.path.join(state_dir, "journal.jsonl"), "w") as f:
        pass
    with open(os.path.join(state_dir, "heartbeat.json"), "w") as f:
        json.dump({"status": "idle", "last_heartbeat": "", "last_task_id": ""}, f)
    with open(os.path.join(state_dir, "stats.json"), "w") as f:
        json.dump({"total_cycles": 0, "total_tasks_completed": 0}, f)
    with open(os.path.join(state_dir, "events.json"), "w") as f:
        json.dump({"meta": {"version": "1.0.0"}, "events": []}, f)

    return workspace, state_dir


def create_engine(state_dir):
    """Create an EventEngine with real dependencies but temp paths."""
    tq = TaskQueue(os.path.join(state_dir, "task_queue.json"))
    kb = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
    journal = Journal(os.path.join(state_dir, "journal.jsonl"))
    sm = StateManager(state_dir)
    engine = EventEngine(state_dir, tq, kb, journal, sm)
    return engine, tq, kb, journal, sm


def test_01_load_empty_events():
    """Engine initializes with no events."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, *_ = create_engine(state_dir)
        assert len(engine.events) == 0, f"Expected 0 events, got {len(engine.events)}"
        assert not engine.has_pending_events()
        assert engine.select_next() is None
        print("✅ test_01: Empty event load works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_02_add_and_select_event():
    """Add events and verify priority-based selection."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, tq, kb, journal, sm = create_engine(state_dir)

        # Create two events with different priorities
        template = engine.registry.get("literature-deep-dive")
        if not template:
            # Fallback: create a minimal template inline
            template = EventTemplate(
                name="test-template",
                description="Test template",
                phases=[
                    EventPhase(name="search", type="literature_search", description="Search phase"),
                    EventPhase(name="extract", type="knowledge_extraction", description="Extract phase"),
                ],
                priority_base=7,
            )

        low_evt = template.create(inputs={"topic": "low priority topic"}, priority=3)
        high_evt = template.create(inputs={"topic": "high priority topic"}, priority=10)

        engine.add_event(low_evt)
        engine.add_event(high_evt)

        assert len(engine.events) == 2
        assert engine.has_pending_events()

        # select_next should pick the higher priority event
        selected = engine.select_next()
        assert selected is not None
        assert selected.id == high_evt.id, f"Expected high priority event {high_evt.id}, got {selected.id}"
        print("✅ test_02: Priority-based selection works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_03_select_filters_expired():
    """Expired events should be filtered out."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, *_ = create_engine(state_dir)

        # Create an expired event (created 100 hours ago, ttl=48)
        evt = Event(
            id="evt_expired",
            template="test",
            priority=15,
            ttl_hours=48,
            created_at=(datetime.now() - timedelta(hours=100)).isoformat(),
            phases=[EventPhase(name="p1", type="literature_search")],
        )
        engine.add_event(evt)

        # Create a fresh high-priority event
        fresh = Event(
            id="evt_fresh",
            template="test",
            priority=5,
            ttl_hours=48,
            phases=[EventPhase(name="p1", type="literature_search")],
        )
        engine.add_event(fresh)

        selected = engine.select_next()
        assert selected is not None
        assert selected.id == "evt_fresh", f"Expected fresh event, got {selected.id}"

        # The expired event should now be marked as expired
        expired = next(e for e in engine.events if e.id == "evt_expired")
        assert expired.status == EventStatus.EXPIRED.value
        print("✅ test_03: Expired event filtering works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_04_run_event_full_flow():
    """Execute a full event: all phases run, tasks created, event marked complete."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, tq, kb, journal, sm = create_engine(state_dir)

        # Create an event with two phases that both create tasks
        evt = Event(
            id="evt_test_run",
            template="test-flow",
            priority=8,
            phases=[
                EventPhase(name="search", type="literature_search", description="Search papers",
                           prompt="Search for {topic}"),
                EventPhase(name="extract", type="knowledge_extraction", description="Extract findings",
                           prompt="Extract key findings"),
            ],
            inputs={"topic": "test topic"},
        )
        engine.add_event(evt)

        # Run the event
        results = engine.run_event(evt)

        # Verify results
        assert results["event_id"] == "evt_test_run"
        assert results["phases_completed"] == 2
        assert results["phases_failed"] == 0
        assert len(results["knowledge_entries"]) == 0  # extraction creates tasks, not entries directly

        # Verify event status
        reloaded = next(e for e in engine.events if e.id == "evt_test_run")
        assert reloaded.status == EventStatus.COMPLETED.value
        assert reloaded.started_at is not None
        assert reloaded.completed_at is not None

        # Verify phases are completed
        for phase in reloaded.phases:
            assert phase.status == "completed", f"Phase {phase.name} status: {phase.status}"
            assert phase.started_at is not None
            assert phase.completed_at is not None

        # Verify tasks were created in the task queue (one per phase)
        assert len(tq.tasks) >= 2, f"Expected >= 2 tasks, got {len(tq.tasks)}: {[t.title for t in tq.tasks]}"

        print("✅ test_04: Full event execution flow works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_05_phase_failure_stops_execution():
    """If a phase fails, the event should be marked as failed and remaining phases skipped."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, tq, kb, journal, sm = create_engine(state_dir)

        # Create an event where the second phase will raise
        class BadPhase(EventPhase):
            pass

        evt = Event(
            id="evt_fail_test",
            template="test-fail",
            priority=5,
            phases=[
                EventPhase(name="ok-phase", type="literature_search", description="This works"),
                EventPhase(name="bad-phase", type="unknown_type_xyz", description="This will fail"),
                EventPhase(name="never-run", type="literature_search", description="Should not execute"),
            ],
            inputs={},
        )
        engine.add_event(evt)

        # The unknown phase type should create a task (not fail), so let's
        # directly patch _execute_phase to raise for the bad phase
        original_execute = engine._execute_phase

        def patched_execute(phase, event):
            if phase.name == "bad-phase":
                raise RuntimeError("Simulated phase failure")
            return original_execute(phase, event)

        engine._execute_phase = patched_execute

        results = engine.run_event(evt)

        assert results["phases_completed"] == 1
        assert results["phases_failed"] == 1

        reloaded = next(e for e in engine.events if e.id == "evt_fail_test")
        assert reloaded.status == EventStatus.FAILED.value

        # The failed phase should have error info
        bad_phase = next(p for p in reloaded.phases if p.name == "bad-phase")
        assert bad_phase.status == "failed"
        assert bad_phase.error == "Simulated phase failure"

        # The third phase should still be pending (never executed)
        never_run = next(p for p in reloaded.phases if p.name == "never-run")
        assert never_run.status == "pending"

        print("✅ test_05: Phase failure stops execution correctly")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_06_spawn_followup_events():
    """After completing a literature-deep-dive, follow-up events should be spawned."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, tq, kb, journal, sm = create_engine(state_dir)

        # Ensure idea-exploration template exists
        idea_template = engine.registry.get("idea-exploration")
        if not idea_template:
            # Register a minimal template
            idea_template = EventTemplate(
                name="idea-exploration",
                description="Explore ideas",
                phases=[EventPhase(name="explore", type="idea_generation", description="Generate ideas")],
            )
            engine.registry.templates["idea-exploration"] = idea_template

        # Create a literature-deep-dive event
        lit_template = engine.registry.get("literature-deep-dive")
        if not lit_template:
            lit_template = EventTemplate(
                name="literature-deep-dive",
                description="Deep dive",
                phases=[EventPhase(name="search", type="literature_search", description="Search")],
            )

        evt = lit_template.create(inputs={"topic": "test spawn"}, priority=8)
        engine.add_event(evt)

        initial_count = len(engine.events)
        engine.run_event(evt)

        # Check if follow-up events were spawned
        new_events = [e for e in engine.events if e.id != evt.id]
        assert len(new_events) >= 1, "Expected at least 1 follow-up event to be spawned"

        # Verify the follow-up has parent_event_id
        followup = new_events[0]
        assert followup.meta.get("parent_event_id") == evt.id
        assert followup.status == EventStatus.PENDING.value

        print("✅ test_06: Follow-up event spawning works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_07_recover_stuck_events():
    """Stuck events (running > 1 hour) should be recoverable."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, *_ = create_engine(state_dir)

        # Create a stuck event (started 2 hours ago, still running)
        evt = Event(
            id="evt_stuck",
            template="test",
            priority=5,
            status=EventStatus.RUNNING.value,
            started_at=(datetime.now() - timedelta(hours=2)).isoformat(),
            phases=[
                EventPhase(name="p1", type="literature_search", status="running",
                           started_at=(datetime.now() - timedelta(hours=2)).isoformat()),
                EventPhase(name="p2", type="knowledge_extraction", status="pending"),
            ],
        )
        engine.add_event(evt)

        # Recover
        engine.recover()

        reloaded = next(e for e in engine.events if e.id == "evt_stuck")
        assert reloaded.status == EventStatus.PENDING.value
        assert reloaded.started_at is None
        assert reloaded.phases[0].status == "pending"  # running phase reset
        assert reloaded.phases[1].status == "pending"  # already pending, stays

        print("✅ test_07: Stuck event recovery works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_08_stats():
    """Stats should reflect event counts by status and template."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, *_ = create_engine(state_dir)

        # Add events with different statuses and templates
        for i, (status, template) in enumerate([
            ("pending", "literature-deep-dive"),
            ("pending", "literature-deep-dive"),
            ("completed", "synthesis-review"),
            ("failed", "literature-deep-dive"),
        ]):
            evt = Event(id=f"evt_stats_{i}", template=template, status=status, priority=5)
            engine.events.append(evt)
        engine._save_events()

        stats = engine.stats()
        assert stats["total_events"] == 4
        assert stats["by_status"]["pending"] == 2
        assert stats["by_status"]["completed"] == 1
        assert stats["by_status"]["failed"] == 1
        assert stats["by_template"]["literature-deep-dive"] == 3
        assert stats["by_template"]["synthesis-review"] == 1

        print("✅ test_08: Stats calculation works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_09_auto_generate_with_knowledge_gaps():
    """auto_generate should create events based on knowledge base gaps."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, tq, kb, journal, sm = create_engine(state_dir)

        # Add some knowledge entries so total > 10
        for i in range(12):
            kb.add(KnowledgeEntry(
                title=f"Entry {i}",
                content=f"Content about topic {i}",
                category="findings",
                tags=["test"],
            ))

        # auto_generate should create a synthesis-review or literature-deep-dive
        evt = engine.auto_generate()
        assert evt is not None, "auto_generate should create an event"
        assert evt.status == EventStatus.PENDING.value

        # The event should be in the engine's event list
        assert any(e.id == evt.id for e in engine.events)

        print("✅ test_09: Auto-generation with knowledge gaps works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_10_phase_dispatch_creates_correct_task_types():
    """Each phase type should create the correct task type in the queue."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, tq, kb, journal, sm = create_engine(state_dir)

        phase_types = [
            ("literature_search", "literature_search"),
            ("knowledge_extraction", "knowledge_synthesis"),
            ("knowledge_synthesis", "knowledge_synthesis"),
            ("idea_generation", "idea_generation"),
            ("project_scan", "project_scan"),
            ("knowledge_scan", "knowledge_synthesis"),
            ("gap_analysis", "knowledge_synthesis"),
            ("planning", "idea_generation"),
            ("exploration", "deep_dive"),
        ]

        for phase_type, expected_task_type in phase_types:
            # Clear tasks
            tq.tasks = []
            tq.save()

            evt = Event(
                id=f"evt_dispatch_{phase_type}",
                template="test",
                priority=5,
                phases=[EventPhase(name="p1", type=phase_type, description=f"Test {phase_type}")],
                inputs={"project": "test-project", "topic": "test-topic"},
            )
            engine.events = [evt]
            engine._save_events()

            engine.run_event(evt)

            assert len(tq.tasks) >= 1, f"No task created for phase type {phase_type}"
            task = tq.tasks[-1]
            assert task.type == expected_task_type, \
                f"Phase {phase_type}: expected task type '{expected_task_type}', got '{task.type}'"

        print("✅ test_10: Phase dispatch creates correct task types")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_11_event_serialization_roundtrip():
    """Events and phases should survive serialization/deserialization."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, tq, kb, journal, sm = create_engine(state_dir)

        original = Event(
            id="evt_roundtrip",
            template="literature-deep-dive",
            status=EventStatus.PENDING.value,
            priority=9,
            ttl_hours=72,
            inputs={"topic": "serialization test", "tags": ["test", "roundtrip"]},
            phases=[
                EventPhase(
                    name="search",
                    type="literature_search",
                    description="Search phase",
                    prompt="Search for {topic}",
                    config={"max_queries": 5},
                ),
                EventPhase(
                    name="extract",
                    type="knowledge_extraction",
                    description="Extract phase",
                ),
            ],
            meta={"triggered_by": "test", "parent_event_id": None},
        )

        engine.add_event(original)

        # Reload from disk
        engine2, *_ = create_engine(state_dir)
        assert len(engine2.events) == 1

        reloaded = engine2.events[0]
        assert reloaded.id == original.id
        assert reloaded.template == original.template
        assert reloaded.priority == original.priority
        assert reloaded.ttl_hours == original.ttl_hours
        assert reloaded.inputs == original.inputs
        assert len(reloaded.phases) == 2
        assert reloaded.phases[0].name == "search"
        assert reloaded.phases[0].config.get("max_queries") == 5
        assert reloaded.meta["triggered_by"] == "test"

        print("✅ test_11: Event serialization roundtrip works")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_12_end_to_end_flow():
    """Full end-to-end: load -> select -> run -> verify state changes -> spawn followup."""
    workspace, state_dir = make_temp_workspace()
    try:
        engine, tq, kb, journal, sm = create_engine(state_dir)

        # Seed knowledge base
        for i in range(5):
            kb.add(KnowledgeEntry(
                title=f"Seed entry {i}",
                content=f"Knowledge about autonomous agents part {i}",
                category="findings",
                tags=["agents", "autonomy"],
            ))

        # Create multiple events with different priorities
        events_data = [
            (3, "literature-deep-dive", {"topic": "low priority"}),
            (8, "literature-deep-dive", {"topic": "high priority target"}),
            (5, "synthesis-review", {}),
        ]

        for pri, tmpl_name, inputs in events_data:
            tmpl = engine.registry.get(tmpl_name)
            if tmpl:
                evt = tmpl.create(inputs=inputs, priority=pri)
            else:
                evt = Event(
                    id=f"evt_e2e_{pri}",
                    template=tmpl_name,
                    priority=pri,
                    inputs=inputs,
                    phases=[EventPhase(name="p1", type="literature_search", description="Search")],
                )
            engine.add_event(evt)

        initial_event_count = len(engine.events)

        # Step 1: Select next (should be priority 8)
        selected = engine.select_next()
        assert selected is not None
        assert selected.priority == 8, f"Expected priority 8, got {selected.priority}"

        # Step 2: Run the selected event
        results = engine.run_event(selected)
        assert results["phases_failed"] == 0

        # Step 3: Verify event is completed
        completed = next(e for e in engine.events if e.id == selected.id)
        assert completed.status == EventStatus.COMPLETED.value

        # Step 4: Verify tasks were created
        assert len(tq.tasks) > 0, "Expected tasks to be created from event phases"

        # Step 5: Verify journal was NOT written by the engine itself
        # (The engine creates tasks, the cron agent writes journal entries)
        # But events should be saved
        assert os.path.exists(os.path.join(state_dir, "events.json"))

        # Step 6: Select next event (should be priority 5 now, or a spawned event)
        next_evt = engine.select_next()
        assert next_evt is not None
        assert next_evt.id != selected.id  # Different event

        # Step 7: Verify stats
        stats = engine.stats()
        assert stats["by_status"].get("completed", 0) >= 1

        print("✅ test_12: End-to-end flow works correctly")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_all():
    """Run all tests."""
    tests = [
        test_01_load_empty_events,
        test_02_add_and_select_event,
        test_03_select_filters_expired,
        test_04_run_event_full_flow,
        test_05_phase_failure_stops_execution,
        test_06_spawn_followup_events,
        test_07_recover_stuck_events,
        test_08_stats,
        test_09_auto_generate_with_knowledge_gaps,
        test_10_phase_dispatch_creates_correct_task_types,
        test_11_event_serialization_roundtrip,
        test_12_end_to_end_flow,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    if failed == 0:
        print("🎉 All tests passed!")
    else:
        print("⚠️ Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    run_all()
