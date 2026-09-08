from __future__ import annotations

import asyncio

from partner.mind import executor
from partner.mind.event_types import EventType, MindEvent


def test_stop_project_event_preserves_handoff_intent_provenance(monkeypatch):
    queued = []

    class Pool:
        async def put(self, event):
            queued.append(event)

    async def fake_pool():
        return Pool()

    monkeypatch.setattr(executor, "ensure_pool", fake_pool)
    source = MindEvent(type=EventType.BATCH_PLAN, payload={}, source="test")
    asyncio.run(executor._enqueue_stop_project_event(source, "project", "done", {
        "task_id": "task-1",
        "continue_from_project": "partner_framework_frontend",
        "previous_receipt_id": "receipt-1",
        "inbox_message_id": "message-1",
        "trigger_source": "inbox",
        "completion_inputs": ["source.py"],
        "expected_observation_completed": True,
    }))

    assert len(queued) == 1
    payload = queued[0].payload
    assert payload["continue_from_project"] == "partner_framework_frontend"
    assert payload["previous_receipt_id"] == "receipt-1"
    assert payload["inbox_message_id"] == "message-1"
    assert payload["trigger_source"] == "inbox"
    assert payload["completion_inputs"] == ["source.py"]
    assert payload["expected_observation_completed"] is True
