"""Multi-layer Rule Loader.

Loads rules from three layers in priority order (highest first):
  1. Personal  (~/.partner/rules/ or workspace state/user/rules/)
  2. Project   (workspace shared_projects/<project>/rules/)
  3. Global    (workspace config/rules/ or partner/rules/defaults/)

Personal rules override Project, Project overrides Global.
Within each layer, higher priority (lower number) wins on conflict.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import yaml

from . import Rule, RuleCategory, RuleScope

logger = logging.getLogger(__name__)

# Default rule files bundled with Partner
_BUILTIN_RULES_DIR = os.path.join(os.path.dirname(__file__), "defaults")


class RuleLoader:
    """Load rules from multiple layers with priority resolution."""

    def __init__(self, workspace_root: str = "", project_name: str = ""):
        self._workspace_root = workspace_root
        self._project_name = project_name
        self._cache: dict[str, list[Rule]] = {}
        self._cache_valid = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self, invalidate_cache: bool = False) -> list[Rule]:
        """Load all rules across all layers, resolved by priority."""
        if not invalidate_cache and self._cache_valid and "all" in self._cache:
            return self._cache["all"]

        rules: dict[str, Rule] = {}

        # Layer 3: Global (lowest priority)
        for rule in self._load_global():
            rules[rule.id] = rule

        # Layer 2: Project
        for rule in self._load_project():
            rules[rule.id] = rule  # Overrides global

        # Layer 1: Personal (highest priority)
        for rule in self._load_personal():
            rules[rule.id] = rule  # Overrides project and global

        # Sort by priority (lower number = higher priority)
        result = sorted(rules.values(), key=lambda r: r.priority)
        # Filter disabled
        result = [r for r in result if r.enabled]

        self._cache["all"] = result
        self._cache_valid = True
        return result

    def load_by_scope(self, scope: RuleScope) -> list[Rule]:
        """Load rules for a specific scope only."""
        return [r for r in self.load_all() if r.scope == scope]

    def load_by_category(self, category: RuleCategory) -> list[Rule]:
        """Load rules for a specific category."""
        return [r for r in self.load_all() if r.category == category]

    def format_for_prompt(self, max_rules: int = 20) -> str:
        """Format all active rules as a compact prompt injection block."""
        rules = self.load_all()[:max_rules]
        if not rules:
            return ""

        lines = ["## Active Rules"]
        for r in rules:
            lines.append(f"- {r.to_injection()}")
        return "\n".join(lines)

    def format_by_category(self) -> dict[str, list[str]]:
        """Group rules by category for structured injection."""
        grouped: dict[str, list[str]] = {}
        for r in self.load_all():
            cat = r.category.value
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append(r.statement)
        return grouped

    def count(self) -> dict[str, Any]:
        """Count rules by scope and category."""
        rules = self.load_all()
        by_scope: dict[str, int] = {}
        by_cat: dict[str, int] = {}
        for r in rules:
            by_scope[r.scope.value] = by_scope.get(r.scope.value, 0) + 1
            by_cat[r.category.value] = by_cat.get(r.category.value, 0) + 1
        return {"by_scope": by_scope, "by_category": by_cat, "total": len(rules)}

    # ------------------------------------------------------------------
    # Layer loaders
    # ------------------------------------------------------------------

    def _load_global(self) -> list[Rule]:
        """Load global rules from config/rules/ and built-in defaults."""
        rules: list[Rule] = []

        # Built-in defaults (shipped with Partner)
        if os.path.isdir(_BUILTIN_RULES_DIR):
            rules.extend(self._load_dir(_BUILTIN_RULES_DIR, RuleScope.GLOBAL, "builtin"))

        # Workspace config/rules/
        if self._workspace_root:
            cfg_rules = os.path.join(self._workspace_root, "config", "rules")
            if os.path.isdir(cfg_rules):
                rules.extend(self._load_dir(cfg_rules, RuleScope.GLOBAL, "workspace-config"))

        return rules

    def _load_project(self) -> list[Rule]:
        """Load project-specific rules."""
        if not self._workspace_root or not self._project_name:
            return []
        proj_rules = os.path.join(
            self._workspace_root, "shared_projects", self._project_name, "rules"
        )
        if os.path.isdir(proj_rules):
            return self._load_dir(proj_rules, RuleScope.PROJECT, f"project:{self._project_name}")
        return []

    def _load_personal(self) -> list[Rule]:
        """Load personal/user rules."""
        rules: list[Rule] = []

        # ~/.partner/rules/
        home_rules = os.path.expanduser("~/.partner/rules")
        if os.path.isdir(home_rules):
            rules.extend(self._load_dir(home_rules, RuleScope.PERSONAL, "home"))

        # workspace state/user/rules/
        if self._workspace_root:
            user_rules = os.path.join(self._workspace_root, "state", "user", "rules")
            if os.path.isdir(user_rules):
                rules.extend(self._load_dir(user_rules, RuleScope.PERSONAL, "workspace-user"))

        return rules

    # ------------------------------------------------------------------
    # File parsing
    # ------------------------------------------------------------------

    def _load_dir(self, dirpath: str, scope: RuleScope, source: str) -> list[Rule]:
        """Load all rule files from a directory."""
        rules: list[Rule] = []
        for fname in sorted(os.listdir(dirpath)):
            fpath = os.path.join(dirpath, fname)
            if fname.endswith((".yaml", ".yml")):
                rules.extend(self._parse_yaml(fpath, scope, source))
            elif fname.endswith(".json"):
                rules.extend(self._parse_json(fpath, scope, source))
            elif fname.endswith(".md"):
                rules.extend(self._parse_markdown(fpath, scope, source))
        return rules

    def _parse_yaml(self, fpath: str, scope: RuleScope, source: str) -> list[Rule]:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return self._data_to_rules(data, scope, source)
        except Exception as e:
            logger.warning("Failed to parse YAML rules from %s: %s", fpath, e)
            return []

    def _parse_json(self, fpath: str, scope: RuleScope, source: str) -> list[Rule]:
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
            return self._data_to_rules(data, scope, source)
        except Exception as e:
            logger.warning("Failed to parse JSON rules from %s: %s", fpath, e)
            return []

    def _parse_markdown(self, fpath: str, scope: RuleScope, source: str) -> list[Rule]:
        """Extract rules from a markdown file.

        Format: lines starting with '- [MUST]', '- [SHOULD]', '- [NEVER]',
        or '- [BY DEFAULT]' are treated as rules.
        """
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning("Failed to read markdown rules from %s: %s", fpath, e)
            return []

        rules: list[Rule] = []
        for line in content.splitlines():
            line = line.strip()
            if not line.startswith("- ["):
                continue

            cat = None
            if line.startswith("- [MUST]"):
                cat = RuleCategory.CONSTRAINT
                statement = line[len("- [MUST] "):].strip()
            elif line.startswith("- [SHOULD]"):
                cat = RuleCategory.PREFERENCE
                statement = line[len("- [SHOULD] "):].strip()
            elif line.startswith("- [NEVER]"):
                cat = RuleCategory.PROHIBITION
                statement = line[len("- [NEVER] "):].strip()
            elif line.startswith("- [BY DEFAULT]"):
                cat = RuleCategory.CONVENTION
                statement = line[len("- [BY DEFAULT] "):].strip()
            else:
                continue

            if statement:
                rules.append(Rule(
                    id=f"md:{os.path.basename(fpath)}:{hash(statement) & 0xFFFF:04x}",
                    category=cat,
                    scope=scope,
                    statement=statement,
                    source=source,
                ))
        return rules

    def _data_to_rules(
        self, data: Any, scope: RuleScope, source: str
    ) -> list[Rule]:
        """Convert parsed data to Rule objects."""
        rules: list[Rule] = []
        if isinstance(data, dict) and "rules" in data:
            items = data["rules"]
        elif isinstance(data, list):
            items = data
        else:
            return []

        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            try:
                cat_str = item.get("category", "convention")
                rules.append(Rule(
                    id=item.get("id", f"{source}:{i}"),
                    category=RuleCategory(cat_str),
                    scope=scope,
                    statement=item.get("statement", item.get("rule", "")),
                    applies_to=item.get("applies_to", []),
                    priority=item.get("priority", 5),
                    enabled=item.get("enabled", True),
                    source=source,
                    metadata=item.get("metadata", {}),
                ))
            except (ValueError, KeyError) as e:
                logger.warning("Skipping invalid rule entry %d: %s", i, e)
        return rules
