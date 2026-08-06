"""
Evolution Journal — track self-evolution changes.
Inspired by PaperFlow's closed-loop tracking.
"""
from __future__ import annotations
import json, os, time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class EvolutionEntry:
    id: str
    timestamp: str
    source: str
    insight: str
    action: str
    target_file: str = ""
    target_module: str = ""
    before_snippet: str = ""
    after_snippet: str = ""
    impact_rating: Optional[int] = None
    verified: bool = False

class EvolutionJournal:
    def __init__(self, workspace: str):
        self.path = os.path.join(workspace, "state", "evolution_journal.jsonl")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def record(self, source, insight, action, target_file="", target_module="",
               before="", after=""):
        entry = EvolutionEntry(
            id=f"evo_{int(time.time() * 1000)}",
            timestamp=datetime.now().isoformat(),
            source=source, insight=insight, action=action,
            target_file=target_file, target_module=target_module,
            before_snippet=before[:200], after_snippet=after[:200])
        with open(self.path, 'a') as f:
            f.write(json.dumps(entry.__dict__, ensure_ascii=False) + '\n')
        return entry

    def list_recent(self, n=20):
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, 'r') as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except Exception:
                    pass
        return sorted(entries, key=lambda e: e.get('timestamp', ''), reverse=True)[:n]

    def stats(self):
        entries = self.list_recent(1000)
        if not entries:
            return {'total': 0}
        sources, modules = {}, {}
        for e in entries:
            src = e.get('source', 'unknown')
            sources[src] = sources.get(src, 0) + 1
            mod = e.get('target_module', 'unknown')
            modules[mod] = modules.get(mod, 0) + 1
        return {'total': len(entries),
                'by_source': dict(sorted(sources.items(), key=lambda x: -x[1])[:5]),
                'by_module': dict(sorted(modules.items(), key=lambda x: -x[1])[:5]),
                'latest': entries[0]['timestamp'] if entries else None}
