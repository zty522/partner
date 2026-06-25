"""Skill Store — download and register skills from remote sources.

The SkillStore fetches skill definitions from remote URLs (e.g. GitHub raw JSON)
and registers them into the local SkillRegistry. This enables Partner to pull
community-curated skill packs without manual config updates.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

from .base_skill import Skill

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


class SkillStore:
    """Fetch skill definitions from remote URLs and register them locally.

    Typical usage:
        store = SkillStore()
        skills = store.fetch_remote_skills("https://...skills.json")
        store.register_remote_skills(skill_registry, skills)
    """

    def __init__(
        self,
        cache_dir: str | None = None,
        cache_ttl_sec: int = 3600,
    ) -> None:
        self.cache_dir = cache_dir
        self.cache_ttl_sec = cache_ttl_sec

    # ── Fetch ─────────────────────────────────────────────────────────────

    def fetch_remote_skills(self, url: str) -> list[JsonDict]:
        """Fetch skill definitions from a remote URL and parse JSON.

        Returns a list of skill definition dicts. Each dict should have at
        minimum a ``"name"`` key.  Additional keys (e.g. ``"description"``,
        ``"kind"``, ``"tags"``, ``"input_schema"``, ``"metadata"``) are
        forwarded to the Skill constructor.
        """
        raw = self._http_get(url)
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            skills = data.get("skills") or data.get("items") or []
            if isinstance(skills, list):
                return skills
        logger.warning("[SKILL_STORE] unexpected remote format from %s (expected list or {skills: [...]})", url)
        return []

    # ── Register ──────────────────────────────────────────────────────────

    def register_remote_skills(
        self,
        skill_registry: Any,
        skills: list[JsonDict],
    ) -> int:
        """Register downloaded skill definitions into a SkillRegistry.

        Args:
            skill_registry: A ``SkillRegistry`` instance (``.register()``
                method).
            skills: List of skill definition dicts as returned by
                :meth:`fetch_remote_skills`.

        Returns:
            Number of skills successfully registered.
        """
        count = 0
        for item in skills:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                logger.warning("[SKILL_STORE] skipping skill with no name: %s", item)
                continue
            try:
                skill = Skill(
                    name=name,
                    description=str(item.get("description") or "").strip(),
                    kind=str(item.get("kind") or "atomic").strip(),
                    external=bool(item.get("external", False)),
                    endpoint=str(item.get("endpoint") or "").strip(),
                    method=str(item.get("method") or "POST").upper(),
                    input_schema=dict(item.get("input_schema") or {}),
                    output_schema=dict(item.get("output_schema") or {}),
                    estimated_ms=int(item.get("estimated_ms") or 0),
                    tags=[str(t) for t in (item.get("tags") or [])],
                    dependencies=[str(d) for d in (item.get("dependencies") or [])],
                    metadata=dict(item.get("metadata") or {}),
                )
                skill_registry.register(skill, category="remote", level="community")
                count += 1
            except Exception as exc:
                logger.warning("[SKILL_STORE] failed to register skill %r: %s", name, exc)
        logger.info("[SKILL_STORE] registered %d/%d remote skills", count, len(skills))
        return count

    # ── Internal helpers ──────────────────────────────────────────────────

    def _http_get(self, url: str, timeout: int = 30) -> str:
        """Raw HTTP GET returning the response body as text."""
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "PartnerSkillStore/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")


# ── Standalone CLI helper ─────────────────────────────────────────────────


def configure_from_config(
    skill_registry: Any,
    config: JsonDict,
) -> int:
    """Convenience: read ``skill_store`` section from skills.yaml config and
    fetch/register all configured sources.

    Expected config shape::

        skill_store:
          enabled: true
          sources:
            - url: "https://raw.githubusercontent.com/.../skills.json"
              name: "hermes_community"
              auto_update: true

    Returns:
        Total number of skills registered from all sources.
    """
    store_cfg = config.get("skill_store") or {}
    if not store_cfg.get("enabled", True):
        logger.info("[SKILL_STORE] disabled by config")
        return 0

    sources = store_cfg.get("sources") or []
    if not isinstance(sources, list):
        logger.warning("[SKILL_STORE] 'sources' is not a list in config")
        return 0

    store = SkillStore()
    total = 0
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        name = str(source.get("name") or url).strip()
        if not url:
            logger.warning("[SKILL_STORE] source %r has no URL, skipping", name)
            continue
        try:
            skills = store.fetch_remote_skills(url)
            registered = store.register_remote_skills(skill_registry, skills)
            total += registered
            logger.info("[SKILL_STORE] source=%r url=%s registered=%d", name, url, registered)
        except Exception as exc:
            logger.warning("[SKILL_STORE] failed to load source %r from %s: %s", name, url, exc)
    return total
