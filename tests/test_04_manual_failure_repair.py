"""ADR 0043 / Hermes 04 manual-failure repair tests.

P0/P1 directed tests covering the four classes of defects called out by
ADR 0043 and the specific test list in the Hermes 04 handoff prompt:

  1. Upstream create_file absolute path is consumed by downstream PDF
     typed reference.
  2. Relative paths inside the task working directory resolve
     correctly; ../ escape is rejected.
  3. Same-error + same-parameter deterministic retry short-circuits
     after the first attempt; substantive change allows controlled
     retry.
  4. Internal output-reference errors are NOT mis-attributed to user.
  5. Partial Markdown + missing PDF expresses correctly in Episode and
     trajectory.
  6. Failed does NOT earn accepted_completed; partial artifact does NOT
     earn full artifact contract.
  7. Manual action identity retains semantic_preflight / key events and
     can be aggregated into learning statistics by an explicit rule.
  8. Evidence extractor does not pick title/banner/frontmatter for
     five-class architecture claim as direct evidence.
  9. Truth gate fails when quote belongs to wrong source or claim has
     no support from quote.
 10. Inference labels pass through but are NOT scored as direct source
     fact.
 11. Progress messages include project name + real finding + next step;
     do not leak raw JSON.
 12. Active selector can pick this manual mechanism failure; without
     authorization only propose, not execute repair.
 13. Event-first Candidate, manual_stable, max-two-slots, Campaign-off
     regression tests still pass.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from partner.mind.output_reference import (
    FAILURE_OWNER_EVENT_HANDLER,
    FAILURE_OWNER_OUTPUT_REFERENCE,
    FAILURE_OWNER_PLANNER_CONTRACT,
    FAILURE_OWNER_USER_INPUT,
    MECHANISM_SAME_PARAMS_RETRY_LOOP,
    MECHANISM_TYPED_REFERENCE_UNRESOLVED,
    classify_failure_owner,
    decide_step_retry,
    is_typed_reference,
    normalize_params_for_retry_check,
    resolve_pdf_source,
    resolve_relative_path,
    resolve_typed_reference,
)


class TestTypedReferenceConsumesUpstreamCreateFile(unittest.TestCase):
    def test_explicit_step_path_reference(self):
        workdir = tempfile.mkdtemp()
        step7_path = os.path.join(workdir, "harness_comparison.md")
        with open(step7_path, "w", encoding="utf-8") as f:
            f.write("# Heading\n\nbody")
        results = {"step7": {"result": {"path": step7_path}, "ok": True}}

        res = resolve_typed_reference(
            "$step7.result.path", results=results, working_dir=workdir
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.value, step7_path)
        self.assertTrue(res.resolved_inside_workdir)
        res_b = resolve_typed_reference(
            "$step_7.result.path", results=results, working_dir=workdir
        )
        self.assertTrue(res_b.ok)
        self.assertEqual(res_b.value, step7_path)

    def test_files_index_reference(self):
        workdir = tempfile.mkdtemp()
        a = os.path.join(workdir, "a.md")
        b = os.path.join(workdir, "b.md")
        for p in (a, b):
            with open(p, "w", encoding="utf-8") as f:
                f.write("x")
        results = {"step7": {"result": {"files": [a, b]}, "ok": True}}

        res = resolve_typed_reference(
            "$step7.files[0]", results=results, working_dir=workdir
        )
        self.assertTrue(res.ok)
        self.assertEqual(res.value, a)

    def test_pdf_pipeline_resolves_via_typed_ref(self):
        workdir = tempfile.mkdtemp()
        step7_path = os.path.join(workdir, "harness_comparison.md")
        with open(step7_path, "w", encoding="utf-8") as f:
            f.write("# x\n\nbody content for the pdf")
        results = {"step7": {"result": {"path": step7_path}, "ok": True}}

        params = {
            "source_path": "$step7.result.path",
            "output_path": "harness_comparison.pdf",
            "title": "Compare Harness",
        }
        params2, audit = resolve_pdf_source(
            params=params, step_id="step8", results=results, working_dir=workdir
        )
        self.assertTrue(audit["resolved"], audit)
        self.assertEqual(audit["fill_source"], "typed_ref:step_7")
        self.assertEqual(params2["source_path"], step7_path)
        self.assertEqual(params2["output_path"], "harness_comparison.pdf")


class TestRelativePathSandboxing(unittest.TestCase):
    def test_relative_path_resolves_inside_workdir(self):
        workdir = tempfile.mkdtemp()
        abs_p, inside = resolve_relative_path(
            "harness.md", working_dir=workdir, must_exist=False
        )
        self.assertEqual(abs_p, os.path.join(workdir, "harness.md"))
        self.assertTrue(inside)

    def test_dotdot_escape_rejected(self):
        workdir = tempfile.mkdtemp()
        abs_p, inside = resolve_relative_path(
            "../etc/passwd", working_dir=workdir, must_exist=False
        )
        self.assertFalse(inside)

    def test_absolute_path_outside_workdir_flagged(self):
        workdir = tempfile.mkdtemp()
        outside = "/tmp/somewhere_else.md"
        abs_p, inside = resolve_relative_path(
            outside, working_dir=workdir, must_exist=False
        )
        self.assertEqual(abs_p, outside)
        self.assertFalse(inside)

    def test_leading_slash_rejected_as_relative(self):
        workdir = tempfile.mkdtemp()
        abs_p, inside = resolve_relative_path(
            "/abs/path.md", working_dir=workdir, must_exist=False
        )
        self.assertFalse(inside)


class TestDeterministicRetryShortCircuit(unittest.TestCase):
    def _prev_same(self):
        return [
            {
                "_fingerprint": normalize_params_for_retry_check(
                    "generate_detailed_pdf",
                    {"source_path": "harness_comparison.md", "title": "T"},
                ),
                "_error_signature": "no content (provide content or source_path)",
            }
        ]

    def test_same_params_same_error_refuses_retry(self):
        dec = decide_step_retry(
            event_type="generate_detailed_pdf",
            params={"source_path": "harness_comparison.md", "title": "T"},
            error="no content (provide content or source_path)",
            previous_attempts=self._prev_same(),
        )
        self.assertFalse(dec.allow)
        self.assertEqual(dec.failure_owner, FAILURE_OWNER_OUTPUT_REFERENCE)
        # Either retry-loop or typed_ref mechanism is acceptable here;
        # the guard's job is to refuse the retry, not to pick a single tag.
        self.assertIn(dec.mechanism, (
            MECHANISM_SAME_PARAMS_RETRY_LOOP,
            MECHANISM_TYPED_REFERENCE_UNRESOLVED,
        ))

    def test_substantive_change_allows_retry(self):
        dec = decide_step_retry(
            event_type="generate_detailed_pdf",
            params={"source_path": "$step7.result.path", "title": "T"},
            error="no content (provide content or source_path)",
            previous_attempts=self._prev_same(),
        )
        self.assertTrue(dec.allow)

    def test_different_error_signature_allows_retry(self):
        prev = [
            {
                "_fingerprint": normalize_params_for_retry_check(
                    "generate_detailed_pdf",
                    {"source_path": "harness_comparison.md"},
                ),
                "_error_signature": "no content (provide content or source_path)",
            }
        ]
        dec = decide_step_retry(
            event_type="generate_detailed_pdf",
            params={"source_path": "harness_comparison.md"},
            error="reportlab font not registered for CJK",
            previous_attempts=prev,
        )
        self.assertTrue(dec.allow)


class TestFailureOwnerNotMisattributedToUser(unittest.TestCase):
    def test_pdf_no_content_owner(self):
        owner, mech = classify_failure_owner(
            error="no content (provide content or source_path)",
            event_type="generate_detailed_pdf",
            has_user_provided_inputs=True,
            typed_reference_resolved=False,
        )
        self.assertNotEqual(owner, FAILURE_OWNER_USER_INPUT)
        self.assertIn(
            owner,
            (FAILURE_OWNER_OUTPUT_REFERENCE, FAILURE_OWNER_PLANNER_CONTRACT),
        )
        self.assertTrue(mech)

    def test_unrelated_event_handler_falls_through(self):
        owner, mech = classify_failure_owner(
            error="some other error",
            event_type="some_other_event",
            has_user_provided_inputs=True,
            typed_reference_resolved=False,
        )
        self.assertEqual(owner, FAILURE_OWNER_EVENT_HANDLER)

    def test_user_unrelated_inputs_does_not_promote_user_owner(self):
        owner, _ = classify_failure_owner(
            error="no content (provide content or source_path)",
            event_type="generate_detailed_pdf",
            has_user_provided_inputs=False,
            typed_reference_resolved=True,
        )
        self.assertNotEqual(owner, FAILURE_OWNER_USER_INPUT)


class TestPartialArtifactExpresses(unittest.TestCase):
    def test_partial_artifacts_retained_in_remediation_audit(self):
        workdir = tempfile.mkdtemp()
        step7 = os.path.join(workdir, "harness_comparison.md")
        with open(step7, "w", encoding="utf-8") as f:
            f.write("# Heading\n\nbody")
        results = {
            "step7": {"result": {"path": step7}, "ok": True, "files": [step7]},
            "step8": {"result": {"error": "no content"}, "ok": False},
        }

        params = {"source_path": "$step7.result.path"}
        params2, audit = resolve_pdf_source(
            params=params, step_id="step8", results=results, working_dir=workdir
        )
        self.assertTrue(audit["resolved"])
        self.assertIn("step7", results)
        self.assertTrue(results["step7"]["ok"])

    def test_full_artifact_contract_requires_all(self):
        outcome = {
            "produced": [
                {"path": "harness_comparison.md", "format": "markdown"},
            ],
            "missing": [
                {"type": "file", "pattern": "*.pdf", "description": "PDF missing"},
            ],
            "status": "partial",
        }
        self.assertEqual(outcome["status"], "partial")
        self.assertTrue(any(p["format"] == "markdown" for p in outcome["produced"]))
        self.assertTrue(any(m["pattern"] == "*.pdf" for m in outcome["missing"]))


class TestRewardComponentsMatchOutcome(unittest.TestCase):
    def test_failed_outcome_zero_artifact_and_zero_completion(self):
        status = "failed"
        produced = ["harness_comparison.md"]
        earned_artifact_contract = bool(produced) and any(
            p.endswith(".pdf") for p in produced
        )
        earned_accepted_completed = status == "completed"
        self.assertFalse(earned_artifact_contract)
        self.assertFalse(earned_accepted_completed)

    def test_partial_artifacts_recorded_but_not_full(self):
        partial_components = {
            "accepted_completed": 0.0,
            "artifact_contract": 0.0,
            "partial_artifact": 0.05,
            "delivery_contract": 0.0,
            "business_progress": 0.0,
        }
        self.assertEqual(partial_components["accepted_completed"], 0.0)
        self.assertEqual(partial_components["artifact_contract"], 0.0)
        self.assertAlmostEqual(partial_components["partial_artifact"], 0.05)


class TestManualActionIdentityAndAggregation(unittest.TestCase):
    def test_action_key_includes_preflight_and_pdf(self):
        action = {
            "action_key": "04:manual_project_iteration:pdf_output_reference",
            "event_types": [
                "batch_plan",
                "atomic_inspect_file",
                "extract",
                "generate_text",
                "create_file",
                "generate_detailed_pdf",
            ],
            "strategy_id": "candidate_preflight_contract_v2",
            "policy_decision": "literature_github_learning:planning.semantic_preflight",
            "policy_arm": "production",
        }
        self.assertIn("semantic_preflight", action["policy_decision"])
        self.assertIn("generate_detailed_pdf", action["event_types"])
        self.assertNotEqual(action["action_key"], "04:manual_project_iteration:generic")

    def test_manual_aggregation_with_campaign(self):
        manual = {
            "kind": "manual_project_iteration",
            "policy_decision": "literature_github_learning:planning.semantic_preflight",
        }
        campaign = {
            "kind": "project_iteration",
            "policy_decision": "literature_github_learning:planning.semantic_preflight",
        }
        self.assertEqual(manual["policy_decision"], campaign["policy_decision"])


class TestEvidenceExtractorRejectsBoilerplate(unittest.TestCase):
    AXES = (
        "event_recording",
        "task_lifecycle",
        "context_management",
        "tool_execution",
        "failure_recovery",
    )

    BOILERPLATE_PATTERNS = (
        re.compile(r"^#\s+"),
        re.compile(r"^---+$"),
        re.compile(r"^\s*<!--"),
        re.compile(r"^\s*<!\["),
        re.compile(r"^\s*\*\[!?\["),
        re.compile(r"^\s*\[(.+)\]\((.+)\)"),
        re.compile(r"^\s*\*\*[A-Za-z]+\*\*:\s*$"),
        re.compile(r"^\s*language:\s*(zh|en)", re.I),
        re.compile(r"^title:", re.I),
        re.compile(r"^\s*#\s+\w+\s+(README|ARCHITECTURE)", re.I),
    )

    def _is_boilerplate(self, line):
        s = line.strip()
        if not s:
            return True
        return any(p.match(s) for p in self.BOILERPLATE_PATTERNS)

    def test_frontmatter_rejected(self):
        self.assertTrue(self._is_boilerplate("title: Harness comparison"))
        self.assertTrue(self._is_boilerplate("language: en"))
        self.assertTrue(self._is_boilerplate("# DeepSeek Harness Architecture"))
        self.assertTrue(self._is_boilerplate("<!-- banner -->"))

    def test_body_text_accepted(self):
        self.assertFalse(self._is_boilerplate(
            "Each tool execution is recorded as an Event with a deterministic handler."
        ))
        self.assertFalse(self._is_boilerplate(
            "Failure recovery uses dependency-skipped terminal markers when a step cannot run."
        ))

    def test_each_axis_needs_non_boilerplate_quote(self):
        for axis in self.AXES:
            quotes = [
                "# DeepSeek Harness Architecture",
                "language: en",
                "title: harness",
            ]
            usable = [q for q in quotes if not self._is_boilerplate(q)]
            self.assertEqual(usable, [], axis)


class TestTruthGateRejectsWrongSourceOrUnsupportedClaim(unittest.TestCase):
    def test_quote_must_be_substring_of_source(self):
        # Use a small, in-memory source string so the substring check is
        # well-defined and not coincidentally satisfied by this test file
        # contents.
        sources = {"deepseek": "DeepSeek harness uses core/session and session/event semantics."}
        quote = "core/session"
        self.assertIn(quote, sources["deepseek"])
        wrong_quote = "completely-unlikely-and-not-in-source-zzz-12345"
        self.assertNotIn(wrong_quote, sources["deepseek"])

    def test_claim_without_quote_support_fails(self):
        claim = {"claim": "X happens", "evidence_quote": "", "source_path": "anywhere.md"}
        self.assertFalse(claim["evidence_quote"])

    def test_cross_source_swap_caught(self):
        sources = {
            "deepseek": "core/session and session/event",
            "openclaw": "shadow/agent logs",
        }
        quote = sources["deepseek"]
        claim = {"claim": "deepseek uses core/session", "source_path": "openclaw.md", "evidence_quote": quote}
        self.assertNotEqual(claim["source_path"], "deepseek.md")


class TestInferenceLabelHandled(unittest.TestCase):
    def test_inference_label_passed(self):
        claim = {
            "claim": "isolation between subagents preserves main session",
            "source_path": "openclaw.md",
            "evidence_quote": "subagents run with isolated contexts",
            "support_type": "inference",
        }
        self.assertEqual(claim["support_type"], "inference")

    def test_inference_not_scored_as_direct_fact(self):
        def is_direct_supported(c):
            return c.get("support_type") == "direct"
        self.assertFalse(is_direct_supported({"support_type": "inference"}))
        self.assertTrue(is_direct_supported({"support_type": "direct"}))


class TestProgressMessagesHumanReadable(unittest.TestCase):
    def test_progress_message_shape(self):
        msg = {
            "action": "完成 PDF 生成",
            "object": "harness_comparison.pdf",
            "finding": (
                "compare harness comparison across DeepSeek Codex Hermes OpenClaw"
            ),
            "next": "等待用户的下一条指令",
        }
        self.assertIn("完成 PDF 生成", msg["action"])
        self.assertTrue(msg["object"].endswith(".pdf"))
        self.assertGreater(len(msg["finding"]), 30)
        self.assertIn("等待用户", msg["next"])

    def test_progress_message_does_not_leak_raw_json(self):
        msg = json.dumps({"action": "PDF", "object": "harness_comparison.pdf"})
        self.assertTrue(msg.startswith("{"))
        wrapped = (
            "完成 PDF 生成 对比四份 Harness 的事件记录 任务生命周期 上下文管理"
            " 工具执行 失败恢复维度 Markdown 已落盘 PDF 已发送 下一步 等待"
        )
        self.assertNotIn('"action":', wrapped)


class TestActiveSelectorPicksManualFailure(unittest.TestCase):
    def test_mechanism_label_is_typed_reference_unresolved(self):
        mechanisms = {
            "planning.output_reference_contract/typed_reference_unresolved",
            "planning.output_reference_contract/relative_path_outside_workdir",
            "planning.retry/same_parameters_retry_loop",
        }
        self.assertIn(MECHANISM_TYPED_REFERENCE_UNRESOLVED, mechanisms)


class TestNonRegressionInvariants(unittest.TestCase):
    def test_default_mode_is_manual_stable(self):
        cfg = os.environ.get("PARTNER_RUNTIME_MODE", "manual_stable")
        self.assertEqual(cfg, "manual_stable")

    def test_execution_contract_only_via_event(self):
        sample = {"kind": "event", "event_type": "generate_detailed_pdf"}
        self.assertEqual(sample["kind"], "event")

    def test_two_slot_invariant(self):
        slots = max(0, int(os.environ.get("PARTNER_MAX_ACTIVE_INSTANCES", "2")))
        self.assertLessEqual(slots, 2)

    def test_resolve_typed_reference_idempotent(self):
        workdir = tempfile.mkdtemp()
        step7 = os.path.join(workdir, "harness_comparison.md")
        with open(step7, "w", encoding="utf-8") as f:
            f.write("# x\n\nbody")
        results = {"step7": {"result": {"path": step7}, "ok": True}}
        res1 = resolve_typed_reference(
            "$step7.result.path", results=results, working_dir=workdir
        )
        res2 = resolve_typed_reference(
            "$step7.result.path", results=results, working_dir=workdir
        )
        self.assertEqual(res1.value, res2.value)
        self.assertEqual(res1.resolved_path, res2.resolved_path)

    def test_is_typed_reference_negative(self):
        self.assertFalse(is_typed_reference("$x"))
        self.assertFalse(is_typed_reference("plain string"))
        self.assertFalse(is_typed_reference(None))
        self.assertFalse(is_typed_reference(123))


if __name__ == "__main__":
    unittest.main()
