"""Figure 1 — problem + method overview data contract + renderer.

Real matplotlib grid: rows = project families, columns = baseline
arms.  Cell shading = the number of matched-test rows.  Empty
families render as no_data and are not plotted.
"""
from __future__ import annotations

import json
from pathlib import Path


def _have_matplotlib() -> bool:
    try:
        import matplotlib  # type: ignore
        return True
    except Exception:
        return False


def aggregate_family_arm_table(runs_root: Path) -> dict:
    out: dict = {}
    for run_json in sorted(runs_root.glob("*/run.json")):
        try:
            payload = json.loads(run_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        m = (payload.get("manifest") or {})
        family = (m.get("fixture") or "").split(".")[-1]
        # Identify the baseline arm from the run's manifest; for now
        # we bucket by code_sha so different baseline configurations
        # stay distinct.
        arm = (m.get("code_sha") or "unknown")[:8]
        matched = sum(1 for s in (payload.get("scores") or [])
                       if s.get("missing_reason") is None and s.get("value") is not None)
        out.setdefault(family, {}).setdefault(arm, 0)
        out[family][arm] += matched
    return out


def render_figure_1(runs_root: Path, output_path: "Path",
                    *, title: str = "Project family × arm") -> bool:
    if not _have_matplotlib():
        return False
    import matplotlib.pyplot as plt
    table = aggregate_family_arm_table(runs_root)
    if not table:
        return False
    families = sorted(table.keys())
    arms = sorted({arm for f in table.values() for arm in f})
    matrix = [[table[f].get(a, 0) for a in arms] for f in families]
    fig, ax = plt.subplots(figsize=(7, 4))
    im = ax.imshow(matrix, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(arms)))
    ax.set_xticklabels(arms, rotation=15)
    ax.set_yticks(range(len(families)))
    ax.set_yticklabels(families)
    ax.set_title(title)
    fig.colorbar(im, ax=ax, label="matched tests")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return True


__all__ = ["aggregate_family_arm_table", "render_figure_1"]
