"""Figure 4 — ablation & reliability data contract + renderer.

Real matplotlib code that consumes the issues.jsonl aggregate and
produces a stacked bar of issue categories per method.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def _have_matplotlib() -> bool:
    try:
        import matplotlib  # type: ignore
        return True
    except Exception:
        return False


def render_figure_4(issues_by_method: dict, output_path: "Path",
                    *, title: str = "Issue categories per method") -> bool:
    if not _have_matplotlib():
        return False
    import matplotlib.pyplot as plt
    categories = ("infrastructure", "candidate", "data", "evaluator", "protocol")
    methods = sorted(issues_by_method.keys())
    counts = {c: [issues_by_method[m].get(c, 0) for m in methods] for c in categories}
    fig, ax = plt.subplots(figsize=(7, 4))
    bottom = [0] * len(methods)
    for cat in categories:
        ax.bar(methods, counts[cat], bottom=bottom, label=cat)
        bottom = [b + c for b, c in zip(bottom, counts[cat])]
    ax.set_ylabel("issue count")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return True


def aggregate_issues_by_method(runs_root: Path) -> dict:
    """Group ``Issue.category`` counts by method (read from manifest.model)."""
    out: dict = {}
    for run_json in sorted(runs_root.glob("*/run.json")):
        try:
            payload = json.loads(run_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        m = (payload.get("manifest") or {}).get("model") or "unknown"
        for issue in payload.get("issues", []) or []:
            cat = issue.get("category") or "unknown"
            out.setdefault(m, Counter())[cat] += 1
    # Convert to plain dict for JSON output.
    return {m: dict(c) for m, c in out.items()}


__all__ = ["render_figure_4", "aggregate_issues_by_method"]
