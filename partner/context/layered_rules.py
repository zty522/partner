"""
Layered Context Rules for Partner.
Inspired by "Give AI Rules" article.
"""
from __future__ import annotations
import os, yaml
from dataclasses import dataclass
from typing import Optional

@dataclass
class Rule:
    name: str
    content: str
    tier: str
    priority: int = 0
    category: str = "general"
    enabled: bool = True

class LayeredRules:
    TIER_DIRS = {'personal': 'rules/personal', 'team': 'rules/team', 'project': 'rules/project'}
    TIER_PRIORITY = {'personal': 300, 'team': 200, 'project': 100}

    def __init__(self, workspace: str):
        self.workspace = workspace
        self._rules = []
        self._load_all()

    def _load_all(self):
        self._rules = []
        for tier, dir_name in self.TIER_DIRS.items():
            tier_dir = os.path.join(self.workspace, dir_name)
            if not os.path.isdir(tier_dir):
                continue
            for fname in sorted(os.listdir(tier_dir)):
                if fname.endswith(('.md', '.txt')):
                    self._load_file(tier, os.path.join(tier_dir, fname))

    def _load_file(self, tier, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            meta, body = {}, content
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    try:
                        meta = yaml.safe_load(parts[1]) or {}
                    except Exception:
                        pass
                    body = parts[2].strip()
            name = meta.get('name', os.path.basename(path).rsplit('.', 1)[0])
            category = meta.get('category', 'general')
            priority = meta.get('priority', 0) + self.TIER_PRIORITY[tier]
            self._rules.append(Rule(name=name, content=body, tier=tier,
                                    priority=priority, category=category,
                                    enabled=meta.get('enabled', True)))
        except Exception:
            pass

    def get_active_rules(self, category=None):
        rules = [r for r in self._rules if r.enabled]
        if category:
            rules = [r for r in rules if r.category == category]
        rules.sort(key=lambda r: -r.priority)
        return rules

    def format_for_prompt(self, max_rules=10, category=None):
        rules = self.get_active_rules(category)[:max_rules]
        if not rules:
            return ""
        lines = ["## Layered Context Rules", ""]
        current_tier = None
        for rule in rules:
            if rule.tier != current_tier:
                tier_name = {'personal': 'Personal', 'team': 'Team', 'project': 'Project'}
                lines.append(f"### {tier_name.get(rule.tier, rule.tier)}")
                current_tier = rule.tier
            content = rule.content[:300]
            if len(rule.content) > 300:
                content += "..."
            lines.append(f"- **{rule.name}**: {content}")
        lines.append("")
        return '\n'.join(lines)

    def reload(self):
        self._rules = []
        self._load_all()
        return len(self._rules)
