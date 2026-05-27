"""Journal System - chronological log of all activities."""

import json
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple


@dataclass
class JournalEntry:
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    task_id: str = ""
    task_type: str = ""
    task_title: str = ""
    result_summary: str = ""
    new_tasks_generated: int = 0
    knowledge_entries_added: int = 0


class Journal:
    """Append-only journal for all Partner activities."""
    
    def __init__(self, path: str):
        self.path = path
        self.entries: List[JournalEntry] = []
        self._load()
    
    def _load(self):
        self.entries = []
        try:
            with open(self.path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        # Handle lines with multiple JSON objects concatenated
                        decoder = json.JSONDecoder()
                        pos = 0
                        while pos < len(line):
                            try:
                                data, end = decoder.raw_decode(line, pos)
                                valid_fields = {f.name for f in JournalEntry.__dataclass_fields__.values()}
                                filtered = {k: v for k, v in data.items() if k in valid_fields}
                                self.entries.append(JournalEntry(**filtered))
                                pos = end
                            except json.JSONDecodeError:
                                break
        except FileNotFoundError:
            pass
    
    def log(self, entry: JournalEntry):
        self.entries.append(entry)
        with open(self.path, 'a') as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + '\n')
    
    def get_recent(self, n: int = 10) -> List[JournalEntry]:
        return self.entries[-n:]
    
    def query(self,
              task_type: Optional[str] = None,
              since: Optional[str] = None,
              limit: int = 20) -> List[JournalEntry]:
        results = self.entries
        if task_type:
            results = [e for e in results if e.task_type == task_type]
        if since:
            results = [e for e in results if e.timestamp >= since]
        return results[-limit:]
    
    def summary(self, last_n: int = 10) -> str:
        """Generate a human-readable summary of recent activity."""
        recent = self.get_recent(last_n)
        if not recent:
            return "还没有任何活动记录。"
        
        lines = [f"最近 {len(recent)} 个活动：\n"]
        for i, e in enumerate(recent, 1):
            lines.append(f"{i}. [{e.timestamp[:16]}] {e.task_title}")
            if e.result_summary:
                # Truncate long summaries
                summary = e.result_summary[:200] + "..." if len(e.result_summary) > 200 else e.result_summary
                lines.append(f"   → {summary}")
            lines.append("")
        return "\n".join(lines)
    
    def stats(self) -> dict:
        total = len(self.entries)
        by_type = {}
        for e in self.entries:
            by_type[e.task_type] = by_type.get(e.task_type, 0) + 1
        return {"total": total, "by_type": by_type}
