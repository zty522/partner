"""Bug #62 (ADR 0067) — claim_ledger audit must accept
support_type=proposed without the source-membership check.

Production survey 2026-09-07 04 task showed 10/10 claims failing
missing_quotes because audit_claim_artifacts forced a file-read
membership check even for support_type=proposed.  This test pins
the contract that proposed claims pass through the audit.
"""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path


def _load_claim_ledger():
    return importlib.import_module("partner.mind.claim_ledger")


class TestAuditAcceptsProposedWithoutMembershipCheck(unittest.TestCase):
    """support_type=proposed must pass the source-membership check
    even when source_path doesn't exist on disk — that's the whole
    point of "proposed" (forward-looking, not a quote from a real
    source)."""

    def setUp(self):
        self.mod = _load_claim_ledger()
        self.tmp = Path(tempfile.mkdtemp())
        self.artifact = self.tmp / "report.md"
        # Report contains no body text matching the placeholder quote;
        # the audit must still pass for support_type=proposed.
        self.artifact.write_text(
            "## Report\n\nNo source quotes present.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_proposed_passes_when_source_missing(self):
        result = self.mod.audit_claim_artifacts(
            [str(self.artifact)],
            named_input_sources=[],  # no real sources
            require_claims=True,
        )
        # Build a single proposed-claim block in the artifact.  All
        # eight required_ledger_fields must be present
        # (claim_id, claim_text, claim_axes, source_path,
        # source_identity, evidence_quote, support_type, rationale).
        self.artifact.write_text(
            "## Report\n\n"
            "### Claim claim_1\n"
            "- claim_id: claim_1\n"
            "- claim_text: forward-looking recommendation\n"
            "- claim_axes: [context_management]\n"
            "- support_type: proposed\n"
            "- source_path: /tmp/nonexistent_first_iteration.md\n"
            "- source_identity: nonexistent_first_iteration.md\n"
            "- evidence_quote: this is a placeholder quote for the first iteration\n"
            "- rationale: First iteration; no source evidence yet.\n",
            encoding="utf-8",
        )
        result = self.mod.audit_claim_artifacts(
            [str(self.artifact)],
            named_input_sources=[],
            require_claims=True,
        )
        self.assertTrue(result["ok"],
                         f"proposed claim must pass audit, got {result=}")
        self.assertEqual(
            result["failed_claims"], [],
            f"no failed claims expected for support_type=proposed, "
            f"got {result['failed_claims']}")


if __name__ == "__main__":
    unittest.main()