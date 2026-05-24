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
    
    def topic_coverage(self, topic: str) -> float:
        """Calculate knowledge coverage for a topic (0.0 = no coverage, 1.0 = full coverage).

        Algorithm:
        1. Split topic into keywords (lowercased)
        2. For each entry, check keyword presence in title/content/tags
        3. Coverage = entries_with_any_match / total_entries (capped at 1.0)
        4. Bonus weight for entries with high confidence
        """
        if not self.entries:
            return 0.0

        # Extract keywords: split topic, filter short words
        keywords = [kw.strip().lower() for kw in topic.split() if len(kw.strip()) >= 2]
        if not keywords:
            return 0.0

        matched = 0
        weighted_matched = 0.0
        total_weight = 0.0

        confidence_weight = {"high": 1.5, "medium": 1.0, "low": 0.5}

        for entry in self.entries:
            w = confidence_weight.get(entry.confidence, 1.0)
            total_weight += w

            # Check keyword presence
            text = f"{entry.title} {entry.content}".lower()
            tag_text = " ".join(entry.tags).lower()

            if any(kw in text or kw in tag_text for kw in keywords):
                matched += 1
                weighted_matched += w

        if total_weight == 0:
            return 0.0

        return min(1.0, weighted_matched / total_weight)

    def knowledge_distribution(self) -> dict:
        """Analyze knowledge distribution across topics/categories/tags.

        Returns a dict with:
        - by_category: {category: count}
        - by_tag: {tag: count}
        - coverage_summary: list of (tag, count, coverage_level) sorted by count desc
        """
        cats = {}
        tags = {}
        for e in self.entries:
            cats[e.category] = cats.get(e.category, 0) + 1
            for t in e.tags:
                tags[t] = tags.get(t, 0) + 1

        # Classify coverage levels
        coverage_summary = []
        for tag, count in sorted(tags.items(), key=lambda x: -x[1]):
            if count >= 5:
                level = "well-covered"
            elif count >= 2:
                level = "moderate"
            else:
                level = "gap"
            coverage_summary.append({"tag": tag, "count": count, "level": level})

        return {
            "total_entries": len(self.entries),
            "by_category": cats,
            "by_tag": tags,
            "coverage_summary": coverage_summary,
        }

    def find_gaps(self, min_gap_count: int = 1) -> list:
        """Find knowledge gaps - tags/categories with low coverage.

        Returns list of dicts: [{"topic": str, "coverage": float, "suggested_priority": int}]
        """
        dist = self.knowledge_distribution()
        gaps = []

        for item in dist["coverage_summary"]:
            if item["level"] == "gap":
                # Calculate coverage via topic_coverage
                cov = self.topic_coverage(item["tag"])
                gaps.append({
                    "topic": item["tag"],
                    "entry_count": item["count"],
                    "coverage": round(cov, 3),
                    "suggested_priority": max(1, int((1 - cov) * 10)),
                })

        # Also check categories with very few entries
        for cat, count in dist["by_category"].items():
            if count <= 1:
                existing = any(g["topic"] == cat for g in gaps)
                if not existing:
                    gaps.append({
                        "topic": cat,
                        "entry_count": count,
                        "coverage": round(self.topic_coverage(cat), 3),
                        "suggested_priority": 8,
                    })

        gaps.sort(key=lambda x: -x["suggested_priority"])
        return gaps[:min_gap_count * 3]  # Return top candidates

    def stats(self) -> dict:
        cats = {}
        for e in self.entries:
            cats[e.category] = cats.get(e.category, 0) + 1
        return {"total": len(self.entries), "by_category": cats}
