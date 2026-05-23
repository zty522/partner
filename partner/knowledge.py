"""Knowledge Base - stores and retrieves research findings."""

import json
import uuid
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class KnowledgeEntry:
    id: str = field(default_factory=lambda: f"k_{uuid.uuid4().hex[:8]}")
    category: str = "findings"  # methods, findings, tools, concepts, pitfalls
    title: str = ""
    content: str = ""
    source: str = ""
    related_projects: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    confidence: str = "medium"  # high, medium, low
    tags: List[str] = field(default_factory=list)


class KnowledgeBase:
    """Stores research findings with search capability."""
    
    def __init__(self, path: str):
        self.path = path
        self.entries: List[KnowledgeEntry] = []
        self._load()
    
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            entries_data = data.get("entries", []) if isinstance(data, dict) else data
            valid_fields = {f.name for f in KnowledgeEntry.__dataclass_fields__.values()}
            self.entries = []
            for e in entries_data:
                filtered = {k: v for k, v in e.items() if k in valid_fields}
                self.entries.append(KnowledgeEntry(**filtered))
        except (FileNotFoundError, json.JSONDecodeError):
            self.entries = []
    
    def save(self):
        data = {
            "meta": {
                "last_updated": datetime.now().isoformat(),
                "total_entries": len(self.entries),
            },
            "entries": [asdict(e) for e in self.entries]
        }
        with open(self.path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def add(self, entry: KnowledgeEntry):
        self.entries.append(entry)
        self.save()
    
    def search(self, query: str, top_k: int = 5) -> List[KnowledgeEntry]:
        """Simple keyword search (can be upgraded to semantic later)."""
        query_lower = query.lower()
        scored = []
        for e in self.entries:
            score = 0
            if query_lower in e.title.lower():
                score += 3
            if query_lower in e.content.lower():
                score += 2
            if any(query_lower in t.lower() for t in e.tags):
                score += 2
            if any(query_lower in p.lower() for p in e.related_projects):
                score += 1
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:top_k]]
    
    def get_by_category(self, category: str) -> List[KnowledgeEntry]:
        return [e for e in self.entries if e.category == category]
    
    def get_recent(self, n: int = 10) -> List[KnowledgeEntry]:
        sorted_entries = sorted(self.entries, key=lambda e: e.created_at, reverse=True)
        return sorted_entries[:n]
    
    def stats(self) -> dict:
        cats = {}
        for e in self.entries:
            cats[e.category] = cats.get(e.category, 0) + 1
        return {"total": len(self.entries), "by_category": cats}
