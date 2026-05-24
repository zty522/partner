"""State Manager - checkpoint and crash recovery."""

import json
import os
import shutil
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any


@dataclass
class Heartbeat:
    """Heartbeat signal for liveness detection."""
    last_heartbeat: str = ""
    status: str = "idle"  # idle, working, recovering, crashed
    current_task_id: str = ""
    cycle_count: int = 0


@dataclass
class Checkpoint:
    id: str = ""
    timestamp: str = ""
    reason: str = ""  # "scheduled", "before_task", "crash_recovery"
    state: Dict[str, Any] = field(default_factory=dict)


class StateManager:
    """Manages global state, heartbeats, and checkpoints."""
    
    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.heartbeat_path = os.path.join(state_dir, "heartbeat.json")
        self.stats_path = os.path.join(state_dir, "stats.json")
        self.checkpoint_dir = os.path.join(state_dir, "checkpoints")
        os.makedirs(self.checkpoint_dir, exist_ok=True)
    
    # --- Heartbeat ---
    
    def heartbeat(self, status: str = "idle", task_id: str = ""):
        """Write heartbeat signal."""
        hb = Heartbeat(
            last_heartbeat=datetime.now().isoformat(),
            status=status,
            current_task_id=task_id,
            cycle_count=self.get_cycle_count() + (1 if status == "idle" else 0),
        )
        with open(self.heartbeat_path, 'w') as f:
            json.dump(asdict(hb), f, indent=2)
    
    def get_heartbeat(self) -> Optional[Heartbeat]:
        try:
            with open(self.heartbeat_path) as f:
                return Heartbeat(**json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
    
    def is_alive(self, timeout_minutes: int = 60) -> bool:
        """Check if Partner is alive based on heartbeat."""
        hb = self.get_heartbeat()
        if not hb:
            return False
        last = datetime.fromisoformat(hb.last_heartbeat)
        diff = (datetime.now() - last).total_seconds() / 60
        return diff < timeout_minutes
    
    def detect_crash(self) -> bool:
        """Detect if previous run crashed (was working but heartbeat expired)."""
        hb = self.get_heartbeat()
        if not hb:
            return False
        if hb.status == "working":
            return not self.is_alive(timeout_minutes=5)
        return False
    
    def get_cycle_count(self) -> int:
        hb = self.get_heartbeat()
        return hb.cycle_count if hb else 0
    
    # --- Stats ---
    
    def update_stats(self, updates: dict):
        stats = self.load_stats()
        stats.update(updates)
        stats["last_updated"] = datetime.now().isoformat()
        with open(self.stats_path, 'w') as f:
            json.dump(stats, f, indent=2)
    
    def load_stats(self) -> dict:
        try:
            with open(self.stats_path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"total_cycles": 0, "total_tasks_completed": 0}
    
    # --- Event Stats Helpers ---
    
    def record_event_completed(self, template_name: str = "", phases_done: int = 0, new_events_spawned: int = 0):
        """Record completion of an event: increment counters and track template usage."""
        stats = self.load_stats()
        stats["total_events_completed"] = stats.get("total_events_completed", 0) + 1
        stats["total_events_spawned"] = stats.get("total_events_spawned", 0) + new_events_spawned
        stats["total_phases_executed"] = stats.get("total_phases_executed", 0) + phases_done
        if template_name:
            templates_used = stats.get("event_templates_used", {})
            templates_used[template_name] = templates_used.get(template_name, 0) + 1
            stats["event_templates_used"] = templates_used
        stats["last_updated"] = datetime.now().isoformat()
        with open(self.stats_path, 'w') as f:
            json.dump(stats, f, indent=2)
    
    def increment_events_spawned(self, count: int = 1):
        """Increment total_events_spawned counter."""
        stats = self.load_stats()
        stats["total_events_spawned"] = stats.get("total_events_spawned", 0) + count
        stats["last_updated"] = datetime.now().isoformat()
        with open(self.stats_path, 'w') as f:
            json.dump(stats, f, indent=2)
    
    def increment_phases_executed(self, count: int = 1):
        """Increment total_phases_executed counter."""
        stats = self.load_stats()
        stats["total_phases_executed"] = stats.get("total_phases_executed", 0) + count
        stats["last_updated"] = datetime.now().isoformat()
        with open(self.stats_path, 'w') as f:
            json.dump(stats, f, indent=2)
    
    def get_event_stats(self) -> dict:
        """Return event-related stats subset."""
        stats = self.load_stats()
        return {
            "total_events_completed": stats.get("total_events_completed", 0),
            "total_events_spawned": stats.get("total_events_spawned", 0),
            "total_phases_executed": stats.get("total_phases_executed", 0),
            "event_templates_used": stats.get("event_templates_used", {}),
        }
    
    # --- Checkpoints ---
    
    def create_checkpoint(self, reason: str, task_queue_path: str, knowledge_path: str) -> str:
        """Create a checkpoint of current state."""
        cp_id = f"cp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        state = {}
        
        # Copy state files
        for name, src in [("task_queue", task_queue_path), ("knowledge", knowledge_path)]:
            try:
                with open(src) as f:
                    state[name] = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                state[name] = None
        
        cp = Checkpoint(
            id=cp_id,
            timestamp=datetime.now().isoformat(),
            reason=reason,
            state=state,
        )
        
        cp_path = os.path.join(self.checkpoint_dir, f"{cp_id}.json")
        with open(cp_path, 'w') as f:
            json.dump(asdict(cp), f, indent=2, ensure_ascii=False)
        
        # Keep only last 5 checkpoints
        self._cleanup_checkpoints(keep=5)
        return cp_id
    
    def restore_from_checkpoint(self, checkpoint_id: str, 
                                 task_queue_path: str, knowledge_path: str) -> bool:
        """Restore state from checkpoint."""
        cp_path = os.path.join(self.checkpoint_dir, f"{checkpoint_id}.json")
        try:
            with open(cp_path) as f:
                cp = json.load(f)
        except FileNotFoundError:
            return False
        
        state = cp.get("state", {})
        for name, dst in [("task_queue", task_queue_path), ("knowledge", knowledge_path)]:
            if state.get(name):
                with open(dst, 'w') as f:
                    json.dump(state[name], f, indent=2, ensure_ascii=False)
        
        return True
    
    def get_latest_checkpoint(self) -> Optional[str]:
        """Get the latest checkpoint ID."""
        files = sorted(os.listdir(self.checkpoint_dir), reverse=True)
        for f in files:
            if f.startswith("cp_") and f.endswith(".json"):
                return f[3:-5]  # Remove "cp_" prefix and ".json" suffix
        return None
    
    def _cleanup_checkpoints(self, keep: int = 5):
        files = sorted(os.listdir(self.checkpoint_dir))
        cp_files = [f for f in files if f.startswith("cp_")]
        if len(cp_files) > keep:
            for f in cp_files[:-keep]:
                os.remove(os.path.join(self.checkpoint_dir, f))
