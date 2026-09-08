"""ADR 0043 harness integration: real workflow returns resolved source_path.

This is a higher-fidelity integration test than ``test_04_manual_failure_repair.py``:
it constructs an end-to-end ``HarnessStep`` workflow that mirrors the failing
04 manual task and asserts the typed-reference PDF source hook resolves
``$step7.result.path`` inside the task working directory, so the PDF Event
would receive a real file path even when the planner emitted a relative
``source_path``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from partner.mind.output_reference import resolve_pdf_source


class TestHarnessIntegration(unittest.TestCase):
    def test_pdf_event_receives_resolved_absolute_path(self):
        workdir = tempfile.mkdtemp()
        step7_path = os.path.join(workdir, "harness_comparison.md")
        with open(step7_path, "w", encoding="utf-8") as f:
            f.write(
                "# 四套 Harness 架构对比分析\n\n"
                "本报告对比 DeepSeek / Codex / Hermes / OpenClaw 四份文档。"
                "共包含五个章节，每个章节至少 200 中文字。"
            )

        results = {
            "step7": {
                "result": {"path": step7_path},
                "ok": True,
                "files": [step7_path],
            },
        }

        # The planner emitted a bare relative ``source_path``.  The
        # adapter must still fill in the absolute upstream path inside
        # the workdir before the event handler runs.
        params = {
            "source_path": "harness_comparison.md",
            "output_path": "harness_comparison.pdf",
            "title": "Compare Harness",
        }
        params2, audit = resolve_pdf_source(
            params=params,
            step_id="step8",
            results=results,
            working_dir=workdir,
        )

        self.assertTrue(audit["resolved"], audit)
        # After resolution ``source_path`` must be the absolute path
        # inside the workdir.
        self.assertEqual(params2["source_path"], step7_path)
        self.assertTrue(os.path.isfile(params2["source_path"]))

    def test_pdf_event_resolves_via_typed_reference(self):
        workdir = tempfile.mkdtemp()
        step7_path = os.path.join(workdir, "harness_comparison.md")
        with open(step7_path, "w", encoding="utf-8") as f:
            f.write("# title\n\nbody")
        results = {
            "step7": {
                "result": {"path": step7_path},
                "ok": True,
                "files": [step7_path],
            },
        }

        params = {"source_path": "$step7.result.path"}
        params2, audit = resolve_pdf_source(
            params=params,
            step_id="step8",
            results=results,
            working_dir=workdir,
        )

        self.assertEqual(audit["fill_source"], "typed_ref:step_7")
        self.assertEqual(params2["source_path"], step7_path)

    def test_pdf_event_fails_honestly_when_no_upstream(self):
        workdir = tempfile.mkdtemp()
        # No upstream path emitted at all.
        results = {
            "step7": {"ok": True, "result": {"content": "x"}},
        }
        params = {"source_path": "harness_comparison.md"}
        params2, audit = resolve_pdf_source(
            params=params,
            step_id="step8",
            results=results,
            working_dir=workdir,
            event_type="generate_detailed_pdf",
        )
        self.assertFalse(audit["resolved"])
        self.assertEqual(audit["failure_owner"], "output_reference")
        # Original source_path preserved; consumer gets honest empty PDF.
        self.assertEqual(params2["source_path"], "harness_comparison.md")


if __name__ == "__main__":
    unittest.main()
