"""Skill Registry — simplified.

Partner no longer manages per-skill configurations, agent discovery,
or local literature search skills.  The registry only tracks harness
events and provides basic context-based filtering for prompt injection.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import yaml

from .base_skill import Skill

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


DEFAULT_SKILL_CONFIG: JsonDict = {
    "dynamic_loading": {
        "enabled": True,
        "max_active": 10,
        "evict_every_tasks": 20,
        "always_active": [],
    },
    "cache": {
        "enabled": True,
        "ttl_seconds": 3600,
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
    candidates = [
        os.path.join(workspace, "config", "skills.yaml"),
        os.path.join(workspace, "skills.yaml"),
    ]
    cfg = dict(DEFAULT_SKILL_CONFIG)
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                cfg = _deep_merge(cfg, loaded)
                cfg["_config_path"] = path
                break
        except Exception as exc:
            logger.debug("[SKILL] failed to load %s: %s", path, exc)
    return cfg


def _apply_hermes_cli_config(cfg: JsonDict) -> None:
    """Read hermes_cli section from skills.yaml and configure semaphore."""
    hermes_cfg = cfg.get("hermes_cli") if isinstance(cfg.get("hermes_cli"), dict) else {}
    if hermes_cfg:
        try:
            from .external_agent_skills import configure_hermes_cli_semaphore
            configure_hermes_cli_semaphore(
                max_concurrent=int(hermes_cfg.get("max_concurrent", 1)),
                timeout=int(hermes_cfg.get("timeout", 300)),
            )
            logger.info(
                "[HERMES_CLI] configured: max_concurrent=%s timeout=%ss",
                hermes_cfg.get("max_concurrent", 1),
                hermes_cfg.get("timeout", 300),
            )
        except Exception as exc:
            logger.warning("[HERMES_CLI] failed to configure semaphore: %s", exc)


def _tokens(text: str) -> set[str]:
    raw = str(text or "").lower()
    tokens: set[str] = set()
    for part in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", raw):
        if not part.strip():
            continue
        tokens.add(part)
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            for n in range(2, min(4, len(part)) + 1):
                for i in range(0, len(part) - n + 1):
                    tokens.add(part[i:i + n])
        else:
            tokens.update(x for x in part.split("_") if x)
    return tokens


def _score_skill(skill: Skill, context: str) -> float:
    ctx = _tokens(context)
    haystack = " ".join([skill.name, skill.description, " ".join(skill.tags)])
    sk = _tokens(haystack)
    overlap = len(ctx & sk) if ctx and sk else 0
    name_bonus = 1.0 if skill.name.lower() in str(context or "").lower() else 0.0
    return overlap + name_bonus


class SkillRegistry:
    def __init__(self, workspace: str, config: JsonDict | None = None) -> None:
        self.workspace = workspace
        self.config = config or _load_config(workspace)
        self._skills: dict[str, Skill] = {}
        self._active_lru: dict[str, float] = {}
        self._tasks_seen = 0
        _apply_hermes_cli_config(self.config)

    @classmethod
    def from_workspace(cls, workspace: str) -> "SkillRegistry":
        return cls(workspace)

    def register(self, skill: Skill, category: str = "", level: str = "basic") -> None:
        if not skill.name:
            return
        if category:
            skill.metadata["category"] = category
        skill.metadata["level"] = level
        self._skills[skill.name] = skill

    def register_harness_events(self, event_registry: Any, category: str = "", level: str = "basic") -> None:
        events = getattr(event_registry, "_events", {}) or {}
        for name, spec in events.items():
            self.register(Skill(
                name=str(name),
                description=str(getattr(spec, "description", "") or ""),
                kind=str(getattr(spec, "kind", "atomic") or "atomic"),
                external=bool(getattr(spec, "external_call", False)),
                tags=[str(getattr(spec, "kind", "")), "harness_event"],
            ), category=category, level=level)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(str(name or "").strip())

    def get_active_skills(self, context: str, max_skills: int | None = None) -> list[Skill]:
        dyn = self.config.get("dynamic_loading") or {}
        always_names = [str(x).strip() for x in (dyn.get("always_active") or []) if str(x).strip()]
        always = [self._skills[name] for name in always_names if name in self._skills]
        if not dyn.get("enabled", True):
            selected = list(self._skills.values())
        else:
            limit = max(1, int(max_skills or dyn.get("max_active") or 10))
            scored = sorted(
                ((skill, _score_skill(skill, context)) for skill in self._skills.values()),
                key=lambda item: (item[1], item[0].name),
                reverse=True,
            )
            picked: list[Skill] = []
            seen: set[str] = set()
            for skill in always:
                if skill.name not in seen:
                    seen.add(skill.name)
                    picked.append(skill)
            for skill, _ in scored:
                if len(picked) >= limit:
                    break
                if skill.name not in seen:
                    seen.add(skill.name)
                    picked.append(skill)
            selected = picked
        now = time.time()
        for skill in selected:
            self._active_lru[skill.name] = now
        self._tasks_seen += 1
        self._maybe_evict_lru()
        return selected

    def describe_active_for_prompt(self, context: str, max_skills: int | None = None) -> str:
        return "\n".join(skill.to_prompt_row() for skill in self.get_active_skills(context, max_skills=max_skills))

    def active_names(self, context: str, max_skills: int | None = None) -> set[str]:
        return {skill.name for skill in self.get_active_skills(context, max_skills=max_skills)}

    def _maybe_evict_lru(self) -> None:
        dyn = self.config.get("dynamic_loading") or {}
        every = max(1, int(dyn.get("evict_every_tasks") or 20))
        if self._tasks_seen % every != 0:
            return
        max_active = max(1, int(dyn.get("max_active") or 10))
        if len(self._active_lru) <= max_active:
            return
        keep = dict(sorted(self._active_lru.items(), key=lambda item: item[1], reverse=True)[:max_active])
        self._active_lru = keep

    def dump_metadata(self) -> JsonDict:
        return {"skills": [skill.to_dict() for skill in self._skills.values()]}
