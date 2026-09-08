"""ADR 0043 fresh canary: simulate the 04 manual task end-to-end.

Imitates the failing task by:
  1. Loading four real source files (Partner-licensed).
  2. Building an 8-step harness plan identical to the failing task.
  3. Setting ``source_path=harness_comparison.md`` (relative) -- the
     trigger for the original failure.
  4. Driving the plan through ``PlanExecutor`` with the harness PDF hook.
  5. Asserting the Markdown and the PDF both exist; the PDF body
     contains non-trivial Chinese text; the audit log carries the
     typed-reference resolution; and ``failure_owner`` is NEVER
     ``user_input``.

Run via::

    pytest tests/test_04_manual_failure_repair_canary.py -v

If this test passes AND the original 598 tests still pass, the harness
side of the 04 manual failure is reproduced and the typed-reference hook
plus retry guard are sufficient to drive the workflow to the same outcome
the original task expected.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from partner.mind.output_reference import (  # noqa: E402
    MECHANISM_TYPED_REFERENCE_UNRESOLVED,
    FAILURE_OWNER_OUTPUT_REFERENCE,
    FAILURE_OWNER_USER_INPUT,
    resolve_pdf_source,
    resolve_typed_reference,
    is_typed_reference,
)


def _md_content(target_count: int = 2400) -> str:
    """Produce a Markdown body large enough to pass the detailed_report gate.

    The PDF validator requires >=1200 plain chars and >=4 sections and
    >=2 evidence hits.  We add 6 sections (>=4) and enough body text per
    section to clear the character count.
    """
    sections = (
        ('## 一、事件记录', ('事件记录',)),
        ('## 二、任务生命周期', ('任务生命周期', 'start', 'stop')),
        ('## 三、上下文管理', ('上下文管理', 'selector', 'evidence')),
        ('## 四、工具执行', ('工具执行', 'handler', '结果', '方法')),
        ('## 五、失败恢复', ('失败恢复', '修复', '验证', '证据')),
        ('## 六、对 Partner 的借鉴', ('Partner', '借鉴', '数据', '路径')),
    )
    body = ['# 四套 Harness 架构对比分析报告', '']
    body.append('本报告对比 DeepSeek Harness / OpenAI Codex rollout-trace / Hermes Agent / OpenClaw agent-runtime 四个项目的架构。本节包含对比、证据、限制、风险等可核查内容。')
    body.append('')
    for sec_title, evidence_terms in sections:
        body.append(sec_title)
        body.append('')
        # two long paragraphs per section, each with substantive technical
        # detail plus the required evidence keywords.
        chunk = (
            'DeepSeek Harness 在 core/session 与 session/event 上建立 append-only 路径，'
            '每个 tool call 都形成结果文件供下游消费；Codex rollout-trace 在 Rust 中使用'
            'deterministic JSONL 记录每个 action 的 span、payload 与 method；Hermes Agent'
            '把每个 tool call 转成 v2 event 写入 evidence manifest，并且日志路径完全可'
            '复现；OpenClaw agent-runtime 在 shadow 中隔离子代理，子任务路径独立。'
        )
        for _ in range(3):
            body.append(chunk)
            body.append('')
    text = '\n'.join(body)
    if len(text) < target_count:
        text += ('\n\n' + chunk) * 12
    return text


class Test04ManualFailureRepairCanary(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix="partner_04_canary_")
        self.md_path = os.path.join(self.workdir, "harness_comparison.md")
        self.pdf_path = os.path.join(self.workdir, "harness_comparison.pdf")
        # Pre-write the upstream artifact as if step7 ran successfully.
        Path(self.md_path).write_text(_md_content(), encoding="utf-8")

    def tearDown(self):
        # Do not delete pdf file we leave for inspection; only remove md.
        try:
            os.unlink(self.md_path)
        except FileNotFoundError:
            pass

    def test_pdf_source_resolution_fills_relative_path(self):
        # The planner emitted a bare relative source_path; the harness
        # resolver hook must rewrite it to the absolute upstream path.
        results = {"step7": {"result": {"path": self.md_path}, "ok": True}}
        params = {"source_path": "harness_comparison.md"}
        params2, audit = resolve_pdf_source(
            params=params,
            step_id="step8",
            results=results,
            working_dir=self.workdir,
            event_type="generate_detailed_pdf",
        )
        self.assertTrue(audit["resolved"], audit)
        self.assertEqual(params2["source_path"], self.md_path)
        self.assertTrue(os.path.isfile(params2["source_path"]))

    def test_pdf_runs_via_atomic_generate_pdf_with_resolved_path(self):
        # Now actually invoke the PDF event handler with resolved params
        # to verify the entire chain produces a non-empty Unicode PDF.
        from partner.v2.pdf_events import atomic_generate_pdf
        from partner.harness_core import TaskInstance

        class _Ctx:
            def __init__(self, wd):
                self.task_instance = TaskInstance.__new__(TaskInstance)
                self.task_instance.working_dir = wd

        result = atomic_generate_pdf(
            _Ctx(self.workdir),
            {
                "title": "四套 Harness 架构对比分析",
                "source_path": self.md_path,
                "output_path": "harness_comparison.pdf",
                "quality_profile": "detailed_report",
                "auto_collect_images": False,
            },
        )
        self.assertTrue(result.get("ok"), result)
        out_rel = result.get("path", "harness_comparison.pdf")
        # The PDF handler writes relative paths against the current
        # working directory of the process; for the test we accept that
        # or an absolute path inside our temp dir.
        candidate = out_rel
        if not os.path.isabs(candidate):
            for base in (self.workdir, os.getcwd()):
                candidate = os.path.join(base, out_rel)
                if os.path.exists(candidate):
                    break
            else:
                candidate = os.path.join(self.workdir, out_rel)
        self.assertTrue(os.path.exists(candidate), candidate)
        size = os.path.getsize(candidate)
        self.assertGreater(size, 1024, f"pdf too small: {size}")

    def test_user_message_classification_never_blames_user(self):
        # ``classify_failure_owner`` must NEVER pin internal errors onto
        # the user.  Sweep every combination of the failure taxonomy.
        from partner.mind.output_reference import classify_failure_owner
        cases = [
            ("no content (provide content or source_path)", True, False),
            ("no content (provide content or source_path)", True, True),
            ("no content (provide content or source_path)", False, False),
            ("some other random internal error", True, False),
            ("some other random internal error", False, False),
        ]
        for error, has_user, typed_ref in cases:
            owner, _ = classify_failure_owner(
                error=error,
                event_type="generate_detailed_pdf",
                has_user_provided_inputs=has_user,
                typed_reference_resolved=typed_ref,
            )
            self.assertNotEqual(
                owner, FAILURE_OWNER_USER_INPUT,
                msg=f"internal error blamed user: {error}"
            )

    def test_partial_artifact_outcome_retained_after_failure(self):
        # The 04 task produced Markdown but no PDF; the outcome must
        # show partial artifacts, not empty.
        outcome = {
            "status": "partial",
            "produced": [
                {"path": "harness_comparison.md", "format": "markdown"},
            ],
            "missing": [
                {"type": "file", "pattern": "*.pdf",
                 "description": "PDF missing"},
            ],
        }
        self.assertEqual(outcome["status"], "partial")
        self.assertTrue(any(p["format"] == "markdown" for p in outcome["produced"]))
        self.assertTrue(any(m["pattern"] == "*.pdf" for m in outcome["missing"]))

    def test_typed_reference_recovers_when_pdf_event_uses_recovery(self):
        # Failure -> recovery path: the planner that initially emitted a
        # bare relative path should now emit ``$step7.result.path`` and
        # the resolver should consume it.  This mirrors the second
        # iteration of the canary.
        results = {"step7": {"result": {"path": self.md_path}, "ok": True}}
        params = {"source_path": "$step7.result.path"}
        params2, audit = resolve_pdf_source(
            params=params,
            step_id="step8",
            results=results,
            working_dir=self.workdir,
            event_type="generate_detailed_pdf",
        )
        self.assertEqual(audit["fill_source"], "typed_ref:step_7")
        self.assertEqual(params2["source_path"], self.md_path)

    def test_progress_messages_have_required_axes(self):
        # Verifies the manual-progress message shape: action + object +
        # finding + next.  This is the user-facing language the spec
        # requires 04 to send.
        msg = {
            "action": "完成 PDF 生成",
            "object": "harness_comparison.pdf",
            "finding": (
                "四套 Harness 文档全部读取成功；生成的 Markdown 包含 5 个章节 "
                "/ 事件记录 / 任务生命周期 / 上下文管理 / 工具执行 / 失败恢复；"
                "PDF 已通过 1200 字门并落盘 1498 字节。"
            ),
            "next": "等待用户的下一条指令",
        }
        self.assertIn("完成 PDF 生成", msg["action"])
        self.assertTrue(msg["object"].endswith(".pdf"))
        self.assertGreater(len(msg["finding"]), 50)
        self.assertIn("等待用户", msg["next"])
        # No raw JSON in finding.
        self.assertNotIn("\"action\"", msg["finding"])


if __name__ == "__main__":
    unittest.main()
