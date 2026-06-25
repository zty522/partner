from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any


def stable_cache_key(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class TTLFileCache:
    def __init__(self, root: str, ttl_seconds: int, enabled: bool = True) -> None:
        self.root = root
        self.ttl_seconds = max(0, int(ttl_seconds or 0))
        self.enabled = bool(enabled)
        os.makedirs(self.root, exist_ok=True)

    def path_for(self, key: str) -> str:
        return os.path.join(self.root, f"{key}.json")

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        path = self.path_for(key)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                row = json.load(f)
            created = float(row.get("created_at") or 0)
            if self.ttl_seconds > 0 and time.time() - created > self.ttl_seconds:
                return None
            return row.get("value")
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        path = self.path_for(key)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"created_at": time.time(), "value": value}, f, ensure_ascii=False, indent=2)
