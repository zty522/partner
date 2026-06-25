from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import yaml


JsonDict = dict[str, Any]


DEFAULT_MEMORY_CONFIG: JsonDict = {
    "long_term": {
        "enabled": True,
        "path": "~/.partner/long_term_memory.json",
        "max_entries": 200,
        "compression": "lru",
        "llm_model": "gpt-4o-mini",
    },
    "short_term": {
        "keep_last_n_tasks": 10,
        "max_workspace_size_mb": 1000,
    },
}


def _deep_merge(base: JsonDict, patch: JsonDict) -> JsonDict:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_config(workspace: str) -> JsonDict:
    cfg = dict(DEFAULT_MEMORY_CONFIG)
    for path in (
        os.path.join(workspace, "config", "memory.yaml"),
        os.path.join(workspace, "memory.yaml"),
    ):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                cfg = _deep_merge(cfg, loaded)
                cfg["_config_path"] = path
                break
        except Exception:
            continue
    return cfg


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{1,4}", str(text or "").lower()))


@dataclass
class MemoryManager:
    workspace: str
    config: JsonDict = field(default_factory=dict)

    @classmethod
    def from_workspace(cls, workspace: str) -> "MemoryManager":
        return cls(workspace=workspace, config=_load_config(workspace))

    @property
    def enabled(self) -> bool:
        return bool((self.config.get("long_term") or {}).get("enabled", True))

    @property
    def path(self) -> str:
        raw = str((self.config.get("long_term") or {}).get("path") or "~/.partner/long_term_memory.json")
        return os.path.abspath(os.path.expanduser(raw))

    def _load(self) -> JsonDict:
        if not os.path.exists(self.path):
            return {"memories": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("memories"), list):
                return data
        except Exception:
            pass
        return {"memories": []}

    def _save(self, data: JsonDict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def store(self, summary: str, tags: list[str] | None = None) -> str:
        if not self.enabled or not str(summary or "").strip():
            return ""
        data = self._load()
        row = {
            "id": str(uuid.uuid4()),
            "summary": str(summary or "").strip()[:2000],
            "tags": [str(x) for x in (tags or []) if str(x).strip()],
            "access_count": 0,
            "last_access": time.time(),
        }
        data["memories"].append(row)
        self._compress_if_needed(data)
        self._save(data)
        return row["id"]

    def retrieve(self, query: str, top_k: int = 3) -> list[JsonDict]:
        if not self.enabled:
            return []
        data = self._load()
        q = _tokens(query)
        scored: list[tuple[float, JsonDict]] = []
        for row in data.get("memories") or []:
            text = " ".join([str(row.get("summary") or ""), " ".join(str(x) for x in row.get("tags") or [])])
            score = len(q & _tokens(text))
            if score > 0:
                scored.append((float(score), row))
        picked = [row for _, row in sorted(scored, key=lambda item: item[0], reverse=True)[: max(0, int(top_k or 3))]]
        now = time.time()
        changed = False
        picked_ids = {row.get("id") for row in picked}
        for row in data.get("memories") or []:
            if row.get("id") in picked_ids:
                row["access_count"] = int(row.get("access_count") or 0) + 1
                row["last_access"] = now
                changed = True
        if changed:
            self._save(data)
        return picked

    def _compress_if_needed(self, data: JsonDict) -> None:
        cfg = self.config.get("long_term") or {}
        max_entries = max(1, int(cfg.get("max_entries") or 200))
        memories = data.get("memories") or []
        if len(memories) <= max_entries:
            return
        if str(cfg.get("compression") or "lru") != "lru":
            # LLM merge is deliberately not implicit here; callers can add it later.
            pass
        memories.sort(key=lambda row: (int(row.get("access_count") or 0), float(row.get("last_access") or 0)), reverse=True)
        data["memories"] = memories[:max_entries]
