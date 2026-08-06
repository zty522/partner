"""
ProcMEM: Procedural Memory for Partner Agents.
Inspired by ICML 2026 ProcMEM paper.
"""
from __future__ import annotations
import hashlib, json, os, time
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class ProcMemEntry:
    task_type: str
    task_signature: str
    action_sequence: list
    success_score: float = 1.0
    use_count: int = 0
    last_used: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

class ProceduralMemory:
    def __init__(self, workspace: str):
        self.db_path = os.path.join(workspace, "state", "procedural_memory.jsonl")
        self._cache = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.db_path):
            return
        with open(self.db_path, 'r') as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                    entry = ProcMemEntry(**d)
                    self._cache.setdefault(entry.task_type, []).append(entry)
                except Exception:
                    pass

    def _save(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, 'w') as f:
            for entries in self._cache.values():
                for e in entries:
                    f.write(json.dumps(e.__dict__, ensure_ascii=False) + '\n')

    @staticmethod
    def make_signature(task_type: str, params: dict) -> str:
        key_params = {k: v for k, v in sorted(params.items())
                      if k not in ('text', 'content', 'description')}
        raw = f"{task_type}|{json.dumps(key_params, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def store(self, task_type, params, action_sequence, success=True):
        sig = self.make_signature(task_type, params)
        entry = ProcMemEntry(task_type=task_type, task_signature=sig,
                             action_sequence=action_sequence,
                             success_score=1.0 if success else 0.3)
        self._cache.setdefault(task_type, []).append(entry)
        self._save()
        return entry

    def retrieve(self, task_type, params, top_k=3):
        sig = self.make_signature(task_type, params)
        candidates = self._cache.get(task_type, [])
        if not candidates:
            return []
        scored = []
        for entry in candidates:
            score = entry.success_score * (1.0 if entry.task_signature == sig else 0.3)
            scored.append((score, entry))
        scored.sort(key=lambda x: -x[0])
        results = []
        for score, entry in scored[:top_k]:
            entry.use_count += 1
            entry.last_used = datetime.now().isoformat()
            results.append({'task_signature': entry.task_signature,
                            'action_sequence': entry.action_sequence,
                            'score': score, 'use_count': entry.use_count})
        self._save()
        return results

    def format_for_prompt(self, memories):
        if not memories:
            return ""
        lines = ["## Procedural Memory", ""]
        for i, mem in enumerate(memories[:3], 1):
            lines.append(f"### Memory {i} (uses: {mem['use_count']}, match: {mem['score']:.2f})")
            for j, step in enumerate(mem['action_sequence'][:5], 1):
                action = step.get('action', step.get('event_type', '?'))
                desc = step.get('description', step.get('summary', ''))
                lines.append(f"  {j}. {action}: {desc}")
            lines.append("")
        return '\n'.join(lines)

    def stats(self):
        total = sum(len(v) for v in self._cache.values())
        types = {k: len(v) for k, v in self._cache.items()}
        return {'total_entries': total, 'by_type': types}
