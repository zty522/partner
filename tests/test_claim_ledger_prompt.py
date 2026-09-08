"""Bug #62 (ADR 0067) — claim_ledger prompt instruction regression.

Pin that batch_planner's manual_stable prompt includes the
"final-artifact must contain Claim Ledger" instruction so LLM
output reports include the explicit block that audit_claim_artifacts
in ``partner/mind/claim_ledger.py`` looks for.

Without the instruction, 7/7 plan steps can succeed but
``governance.ok=False`` because the audit returns
``failed_claims=['claim_ledger_missing']``.
"""
import importlib
import re
import sys
import unittest


def _load_batch_planner():
    return importlib.import_module("partner.planner.batch_planner")


class TestClaimLedgerPromptContract(unittest.TestCase):
    """The prompt instruction ``[manual_stable 报告必须包含 Claim Ledger]``
    must always be emitted by ``_build_user_contract`` (or the
    equivalent prompt assembly path) so the LLM knows to embed a
    Claim block in the .md/.txt output."""

    def setUp(self):
        self.mod = _load_batch_planner()

    def test_prompt_includes_claim_ledger_instruction(self):
        """Scan the module for the new prompt instruction string.
        We can't easily call ``_build_user_contract`` because it
        depends on heavy partner state, so we do a substring check
        on the module source."""
        import inspect
        src = inspect.getsource(self.mod)
        self.assertIn(
            "[manual_stable 报告必须包含 Claim Ledger]", src,
            "batch_planner prompt must include the Claim Ledger "
            "instruction so LLM emits explicit Claim blocks in the "
            ".md/.txt output.")
        # And the instruction must mention the consequence:
        self.assertIn(
            "claim_ledger_missing", src,
            "batch_planner prompt must reference the failure mode "
            "the truth gate produces when Claim Ledger is missing.")

    def test_prompt_shows_claim_block_template(self):
        """The instruction must include the canonical Claim block
        template so LLM can copy it verbatim into the report."""
        import inspect
        src = inspect.getsource(self.mod)
        self.assertIn("claim_id", src,
                       "Claim block template must include claim_id")
        self.assertIn("claim_text", src,
                       "Claim block template must include claim_text")
        self.assertIn("support_type", src,
                       "Claim block template must include support_type")
        self.assertIn("source_path", src,
                       "Claim block template must include source_path")
        self.assertIn("evidence_quote", src,
                       "Claim block template must include evidence_quote")


if __name__ == "__main__":
    unittest.main()