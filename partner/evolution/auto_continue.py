
"""Auto-continue: inject next task after STOP_PROJECT (Sprint 7).
DISABLED 2026-08-09: The blind inject() → desktop_inbox → 
interaction_orchestrator → BATCH_PLAN loop caused infinite re-injection.
TASKS table cleared; inject() now checks for existing pending tasks
before injecting to prevent the death loop.
"""
import os, json, time, uuid, logging

logger = logging.getLogger(__name__)

# Hardcoded tasks per instance — CLEARED to prevent blind re-injection.
# Each entry was causing an infinite loop: OODA trigger → inject → 
# desktop_inbox → new USER_MESSAGE → BATCH_PLAN → OODA trigger → ...
TASKS = {}

_LAST_INJECTED = {}  # {instance_id: timestamp} — prevent rapid re-injection

def inject(workspace: str, instance_id: str = "", cooldown_sec: int = 300) -> bool:
    """Inject next task into desktop_inbox. With dedup protection.

    Returns False (no-op) if:
    - No task configured for this instance
    - Last injection was within cooldown_sec seconds
    - There are already unprocessed entries in the inbox
    """
    task = TASKS.get(instance_id, TASKS.get("02", ""))
    if not task:
        logger.debug("[AUTO-CONTINUE] No task configured for instance %s", instance_id)
        return False

    # Cooldown check: prevent rapid re-injection
    now = time.time()
    last = _LAST_INJECTED.get(instance_id, 0)
    if now - last < cooldown_sec:
        logger.debug("[AUTO-CONTINUE] Cooldown active for %s (%.0fs ago)", instance_id, now - last)
        return False

    inbox = os.path.join(workspace, "state", "desktop_inbox.jsonl")
    os.makedirs(os.path.dirname(inbox), exist_ok=True)

    # Check for existing unprocessed entries — don't pile on
    try:
        if os.path.exists(inbox):
            with open(inbox) as f:
                pending = [l for l in f if l.strip()]
            if len(pending) >= 1:
                logger.debug("[AUTO-CONTINUE] Skipping: %d entries already in inbox", len(pending))
                return False
    except Exception:
        pass

    try:
        with open(inbox, "a") as f:
            f.write(json.dumps({
                "id": str(uuid.uuid4()),
                "message_id": f"auto_{int(time.time())}",
                "source": "tui",
                "text": task,
                "ts": time.time(),
                "sender_name": "Auto-Continue",
            }, ensure_ascii=False) + "\n")
        _LAST_INJECTED[instance_id] = now
        logger.info("[AUTO-CONTINUE] Task injected for %s", instance_id)
        return True
    except Exception as e:
        logger.debug("[AUTO-CONTINUE] Failed: %s", e)
        return False
