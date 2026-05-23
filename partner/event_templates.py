"""Event Template Registry - loads and manages Event templates from YAML files."""

import os
import glob
import yaml
from typing import Dict, List, Optional
from .event import EventTemplate, EventPhase


class TemplateRegistry:
    """Manages Event templates from built-in and user directories."""

    def __init__(self, builtin_dir: str = None, user_dir: str = None):
        # Default paths
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.builtin_dir = builtin_dir or os.path.join(base, "templates", "events")
        self.user_dir = user_dir  # Optional user custom templates
        self.templates: Dict[str, EventTemplate] = {}
        self._load_all()

    def _load_all(self):
        """Load all templates from builtin and user directories."""
        self.templates.clear()

        # Load built-in templates
        if os.path.exists(self.builtin_dir):
            for fpath in glob.glob(os.path.join(self.builtin_dir, "*.yaml")):
                try:
                    template = self._load_template(fpath)
                    self.templates[template.name] = template
                except Exception as e:
                    print(f"Warning: failed to load template {fpath}: {e}")

        # Load user templates (override built-in if same name)
        if self.user_dir and os.path.exists(self.user_dir):
            for fpath in glob.glob(os.path.join(self.user_dir, "*.yaml")):
                try:
                    template = self._load_template(fpath)
                    self.templates[template.name] = template
                except Exception as e:
                    print(f"Warning: failed to load user template {fpath}: {e}")

    def _load_template(self, fpath: str) -> EventTemplate:
        """Load a single template from a YAML file."""
        with open(fpath, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError(f"Empty template file: {fpath}")

        # Parse phases
        phases = []
        for phase_data in data.get("phases", []):
            phase = EventPhase(
                name=phase_data.get("name", ""),
                type=phase_data.get("type", ""),
                description=phase_data.get("description", ""),
                prompt=phase_data.get("prompt", ""),
                config={k: v for k, v in phase_data.items()
                        if k not in ("name", "type", "description", "prompt")},
            )
            phases.append(phase)

        return EventTemplate(
            name=data.get("name", os.path.basename(fpath).replace(".yaml", "")),
            description=data.get("description", ""),
            phases=phases,
            priority_base=data.get("priority_base", 7),
            ttl_hours=data.get("ttl_hours", 48),
            estimated_minutes=data.get("estimated_minutes", 10),
            tags=data.get("tags", []),
            triggers=data.get("triggers", {}),
            inputs=data.get("inputs", {}),
        )

    def get(self, name: str) -> Optional[EventTemplate]:
        """Get a template by name."""
        return self.templates.get(name)

    def list_all(self) -> List[EventTemplate]:
        """List all available templates."""
        return list(self.templates.values())

    def list_names(self) -> List[str]:
        """List all template names."""
        return list(self.templates.keys())

    def install(self, source_path: str) -> str:
        """Install a template from a file to the user directory."""
        import shutil
        template = self._load_template(source_path)
        if not self.user_dir:
            raise ValueError("No user directory configured for template installation")
        os.makedirs(self.user_dir, exist_ok=True)
        dest = os.path.join(self.user_dir, f"{template.name}.yaml")
        shutil.copy2(source_path, dest)
        self.templates[template.name] = template
        return template.name

    def reload(self):
        """Reload all templates from disk."""
        self._load_all()
