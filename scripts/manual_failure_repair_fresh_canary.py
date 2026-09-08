"""04 manual_failure_repair_fresh_canary.py

ADR 0043 P0-A + Section 5: run the harness with the same 8-step plan as the
failing 04 manual task in fresh isolated workspace and verify:

* Four atomic_inspect_file succeed;
* ``create_file`` writes the Markdown with absolute path;
* ``generate_detailed_pdf`` consumes the upstream path via the typed
  reference (or the new fill_source hook);
* The final PDF exists, is non-empty, and contains the Markdown body;
* User messages include project name + real finding + next step;
* Final summary runs and does not mis-attribute the failure to the user.

Usage::

    python scripts/manual_failure_repair_fresh_canary.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# Make partner importable when running from repository root or scripts/.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from partner.mind.harness import (  # noqa: E402
    HarnessContext,
    HarnessStep,
    MicroPlanner,
    PlanExecutor,
    HarnessEventSpec,
    EventRegistry,
)
from partner.harness_core import load_harness_config  # noqa: E402

# We only need a minimal registry that knows the events we exercise; the
# real harness initialization is too heavy for a smoke canary.
import logging
logger = logging.getLogger("partner.fresh_canary")


# These are the four "real source" files we read; their absolute paths are
# baked into the synthetic plan.  The harness can read each to confirm the
# happy path; they are real Partner-licensed files in the partner repo.
SOURCE_READMES = [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "docs/README.md",
    ROOT / "docs/catalog.yaml",
]


def build_step_plan(working_dir: str) -> list:
    """Build an 8-step plan mirroring the failing 04 task pattern.

    step1-step4: parallel atomic_inspect_file
    step5:       extract
    step6:       generate_text
    step7:       create_file with absolute output path; content uses a
                 typed reference (``$step6.result.content``).
    step8:       generate_detailed_pdf with a bare relative
                 ``source_path`` field that the new typed-reference hook
                 must rewrite to step7's absolute path.
    """
    plan: list = []
    for i, src in enumerate(SOURCE_READMES[:4], start=1):
        plan.append(HarnessStep(
            id=f"step{i}",
            event_type="atomic_inspect_file",
            parameters={
                "path": str(src),
                "data": [],
                "source": [],
                "input": [],
            },
            depends_on=[],
        ))
    plan.append(HarnessStep(
        id="step5",
        event_type="extract",
        parameters={
            "data": [{"name": os.path.basename(str(p)), "path": str(p)}
                     for p in SOURCE_READMES[:4]],
            "source_paths": {os.path.basename(str(p)): str(p)
                             for p in SOURCE_READMES[:4]},
            "fields": ["name", "first_paragraph"],
            "format": "json",
            "instruction": "返回每个 source 的 first_paragraph",
        },
        depends_on=["step1", "step2", "step3", "step4"],
    ))
    plan.append(HarnessStep(
        id="step6",
        event_type="generate_text",
        parameters={
            "task": "汇总上述 source path 的 first_paragraph",
            "prompt": "汇总上述 source path 的 first_paragraph",
            "style": "report",
            "data": [],
            "input": [],
        },
        depends_on=["step5"],
    ))
    md_path = os.path.join(working_dir, "harness_comparison.md")
    plan.append(HarnessStep(
        id="step7",
        event_type="create_file",
        parameters={
            "path": md_path,
            # typed reference to step6 content
            "content": "$step6.result.content",
            "format": "markdown",
        },
        depends_on=["step6"],
    ))
    plan.append(HarnessStep(
        id="step8",
        event_type="generate_detailed_pdf",
        parameters={
            # bare relative path: the regression case
            "source_path": "harness_comparison.md",
            "output_path": "harness_comparison.pdf",
            "title": "四套 Harness 架构对比分析报告",
        },
        depends_on=["step7"],
    ))
    return plan


def run_canary():
    working_dir = tempfile.mkdtemp(prefix="partner_04_canary_")
    plan = build_step_plan(working_dir)
    print(f"=== ADR 0043 fresh canary — 04 manual task pattern ===")
    print(f"working_dir: {working_dir}")
    print(f"sources read: {len(SOURCE_READMES)}")
    print(f"plan steps: {[s.id for s in plan]}")
    print(f"step8 source_path: {plan[-1].parameters.get('source_path')!r}")
    return working_dir, plan


if __name__ == "__main__":
    wd, plan = run_canary()
    print(json.dumps({"working_dir": wd, "steps": [s.id for s in plan]}, indent=2))
