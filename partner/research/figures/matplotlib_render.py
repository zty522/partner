"""Matplotlib figure generation for benchmark reports (M3 / 6.4).

Real matplotlib code that consumes the figure JSON contracts emitted
by ``scripts/benchmark/report.py`` and renders PNG / SVG output.

State honesty: ``static_implemented``.  The module source is real
and will produce figures once matplotlib is installed; the rendering
itself is deferred to the verification pass.
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


def render_figure_2(figure_2_data: dict, output_path: "Path",
                    *, title: str = "Per-family metric table") -> bool:
    """Render Figure 2 — per-family metric bar with 95% CI whiskers.

    Returns True if a file was written; False if matplotlib is not
    installed (in which case the caller can fall back to the raw
    JSON payload).
    """
    if not _have_matplotlib():
        return False
    import matplotlib.pyplot as plt
    metrics = figure_2_data.get("metrics") or {}
    if not metrics:
        return False
    primary = next(iter(metrics.keys()), None)
    if primary is None:
        return False
    rows = metrics[primary]
    families = [r.get("fixture_family", "?") for r in rows]
    means = [r.get("mean") or 0.0 for r in rows]
    lowers = [r.get("ci_lower") or 0.0 for r in rows]
    uppers = [r.get("ci_upper") or 0.0 for r in rows]
    err_low = [max(0.0, m - l) for m, l in zip(means, lowers)]
    err_up = [max(0.0, u - m) for m, u in zip(means, uppers)]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(families, means, yerr=[err_low, err_up], capsize=4,
           color="#3a86ff", edgecolor="#222")
    ax.set_ylabel(primary)
    ax.set_title(title)
    ax.set_ylim(0.0, max(1.0, max(means + uppers + [0.05]) * 1.1))
    ax.tick_params(axis="x", labelrotation=15)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return True


def render_figure_3(figure_3_data: dict, output_path: "Path",
                    *, title: str = "Per-method paired-delta CI") -> bool:
    """Render Figure 3 — per-method paired-delta CI.

    Methods on x-axis; CI [lower, upper] on y-axis; CI crossing zero
    is highlighted with a red dashed line.
    """
    if not _have_matplotlib():
        return False
    import matplotlib.pyplot as plt
    methods = figure_3_data.get("methods") or {}
    if not methods:
        return False
    labels = sorted(methods.keys())
    ci_lows: list[float] = []
    ci_highs: list[float] = []
    crosses_zero: list[bool] = []
    for m in labels:
        # Aggregate across families for the method.
        lows = []
        highs = []
        for family, rows in methods[m].items():
            for r in rows:
                lo = r.get("ci_lower")
                hi = r.get("ci_upper")
                if lo is not None and hi is not None:
                    lows.append(float(lo))
                    highs.append(float(hi))
        if lows:
            ci_lows.append(min(lows))
            ci_highs.append(max(highs))
            crosses_zero.append(min(lows) <= 0.0 <= max(highs))
        else:
            ci_lows.append(0.0)
            ci_highs.append(0.0)
            crosses_zero.append(False)
    fig, ax = plt.subplots(figsize=(7, 4))
    xs = list(range(len(labels)))
    err_low = [max(0.0, -l) for l in ci_lows]
    err_up = [max(0.0, h) for h in ci_highs]
    ax.errorbar(xs, ci_lows, yerr=[err_low, err_up], fmt="o", capsize=4,
                color="#3a86ff")
    ax.axhline(0, color="#c44", linestyle="--")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=15)
    ax.set_ylabel("paired-delta CI")
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return True


__all__ = ["render_figure_2", "render_figure_3"]
