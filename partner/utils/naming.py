"""Dynamic file naming based on user intent and config rules."""
from __future__ import annotations
import os, re, logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

def load_naming_config(workspace: str = "") -> dict:
    import yaml
    candidates = [
        os.path.join(workspace, "config", "naming.yaml"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                return yaml.safe_load(f) or {}
    return {"rules": [], "default_template": "result_{timestamp}.{ext}"}

def generate_filename(user_message: str, extension: str, workspace: str = "") -> str:
    """Generate a file name based on user intent and naming rules."""
    cfg = load_naming_config(workspace)
    rules = cfg.get("rules", [])
    max_len = int(cfg.get("max_filename_length", 64))
    ts = datetime.now().strftime(cfg.get("timestamp_format", "%Y%m%d_%H%M%S"))
    ext = extension.lstrip(".")

    for rule in rules:
        pattern = rule.get("pattern", "")
        if not pattern:
            continue
        try:
            if re.search(pattern, user_message, re.I):
                template = rule.get("template", cfg.get("default_template", "result_{timestamp}.{ext}"))
                extractors = rule.get("extractors", {})
                # Fill extractors
                kwargs = {"ext": ext, "timestamp": ts}
                for key, extract_pattern in extractors.items():
                    m = re.search(extract_pattern, user_message)
                    kwargs[key] = m.group(1) if m else "unknown"
                name = template.format(**kwargs)
                if len(name) > max_len:
                    base, e = os.path.splitext(name)
                    name = base[:max_len - len(e) - 1] + e
                return name
        except re.error:
            continue

    # Default fallback
    template = cfg.get("default_template", "result_{timestamp}.{ext}")
    name = template.format(ext=ext, timestamp=ts, goal_keywords="result")
    if len(name) > max_len:
        base, e = os.path.splitext(name)
        name = base[:max_len - len(e) - 1] + e
    return name
