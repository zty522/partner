"""Figure 1—5 data contracts + matplotlib rendering (M0/M3)."""
from .matplotlib_render import render_figure_2, render_figure_3
from .figure_4 import render_figure_4, aggregate_issues_by_method
from .figure_1 import render_figure_1, aggregate_family_arm_table
__all__ = [
    "render_figure_1", "aggregate_family_arm_table",
    "render_figure_2", "render_figure_3",
    "render_figure_4", "aggregate_issues_by_method",
]
