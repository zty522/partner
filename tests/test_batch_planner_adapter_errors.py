"""Bug #56 fix regression — batch_planner must distinguish upstream
adapter/network errors from unavailable sentinel so the real cause surfaces
instead of a misleading "Batch planner returned invalid JSON" downstream.

Each case pins a real upstream error body shape observed in the 2026-09-05
03-instance incident where Herme­s token plan quota was exhausted and every
batch_plan LLM call returned an HTTP 429 error body. Without this guard the
robust executor passed the error body to _json_from_llm and the framework
reported a JSON parse error, masking the quota problem.

See ADR 0021.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.planner.batch_planner import (
    _is_adapter_error_text,
    _is_unavailable_sentinel,
)


class TestIsAdapterErrorText:
    """Pin the exact error-body tokens the helper must flag."""

    # ── Real instance-03 incident shape (2026-09-05 19:53:01) ──
    def test_429_quota_token_plan_exhausted(self):
        raw = (
            "API call failed after 3 retries: HTTP 429: "
            "已达到 Token Plan 用量上限：请升级 Token Plan 套餐或购买积分补充用量。 (2056)"
        )
        assert _is_adapter_error_text(raw) is True

    def test_502_bad_gateway(self):
        raw = "Server error '502 Bad Gateway' for url 'http://localhost:8100/simulate'"
        assert _is_adapter_error_text(raw) is True

    def test_connection_reset_by_peer(self):
        raw = (
            "Failed to fetch https://html.duckduckgo.com/html/?q=foo: "
            "Cannot connect to host html.duckduckgo.com:443 ssl:default "
            "[Connection reset by peer]"
        )
        assert _is_adapter_error_text(raw) is True

    def test_all_connection_attempts_failed(self):
        # Repeated WorldModel simulation fallback observed in instance_03.log
        raw = "All connection attempts failed"
        assert _is_adapter_error_text(raw) is True

    def test_5xx_generic(self):
        for code in ("500", "503", "504"):
            raw = f"HTTP {code}: upstream error body"
            assert _is_adapter_error_text(raw) is True, f"missed HTTP {code}"

    def test_timeout_token(self):
        for tok in ("Connection timed out", "ETIMEDOUT"):
            assert _is_adapter_error_text(tok) is True

    # ── Negative cases — must NOT be flagged as adapter errors ──
    def test_unavailable_sentinel_not_double_flagged(self):
        # Sentinel path is separate; both must return True for sentinel,
        # but only unavailable-sentinel check should fire there.
        raw = "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE"
        assert _is_unavailable_sentinel(raw) is True
        # Intentionally NOT matched as adapter error — different code path,
        # different retry semantics, different error message.
        assert _is_adapter_error_text(raw) is False

    def test_valid_json_not_flagged(self):
        raw = '{"plan": [{"id": "step_1", "event_type": "atomic_read_state", "parameters": {"title": "x"}, "depends_on": []}]}'
        assert _is_adapter_error_text(raw) is False

    def test_empty_string_not_flagged(self):
        assert _is_adapter_error_text("") is False

    def test_single_char_garbage_not_flagged(self):
        # The 03 incident surfaced as `"n"` once the error body was clipped.
        # That's a JSON parse failure, not an adapter error — must NOT route
        # through the adapter-error retry path; keep going through _json_from_llm
        # so the legacy "invalid JSON" path remains honest for non-adapter cases.
        assert _is_adapter_error_text("n") is False


class TestBatchPlannerCallSiteWiring:
    """Source-level guards: the adapter-error check must appear at BOTH
    call sites that previously routed everything through _json_from_llm.
    Pins the file structure so future refactors don't silently re-break
    the contract documented in ADR 0021."""

    BATCH_PLANNER_PATH = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..",
        "partner", "planner", "batch_planner.py",
    ))

    def _src(self) -> str:
        with open(self.BATCH_PLANNER_PATH, "r", encoding="utf-8") as f:
            return f.read()

    def test_helper_defined(self):
        src = self._src()
        assert "def _is_adapter_error_text(text: str) -> bool" in src, (
            "_is_adapter_error_text must be defined in batch_planner.py"
        )

    def test_helper_called_at_primary_path(self):
        # Primary LLM call: between `raw = str(result.value or "")` and
        # the `if not _is_unavailable_sentinel(raw): break` line, the
        # adapter-error short-circuit must come first.
        src = self._src()
        # Locate the primary path: result.value assignment + sentinel check
        primary_pattern = re.compile(
            r'raw = str\(result\.value or ""\)\s*\n'
            r'.*?if _is_adapter_error_text\(raw\):\s*\n'
            r'.*?if not _is_unavailable_sentinel\(raw\):\s*\n'
            r'\s*break',
            re.DOTALL,
        )
        assert primary_pattern.search(src), (
            "primary path must check _is_adapter_error_text BEFORE "
            "_is_unavailable_sentinel, then break"
        )

    def test_helper_called_at_retry_path(self):
        # Retry LLM call: same pattern in the retry loop, raw2 is the
        # retry result.value.
        src = self._src()
        retry_pattern = re.compile(
            r'raw2 = str\(result\.value or ""\)\s*\n'
            r'.*?if _is_adapter_error_text\(raw2\):\s*\n'
            r'.*?if not _is_unavailable_sentinel\(raw2\):\s*\n'
            r'.*?try:\s*\n'
            r'\s*micro_plan = _normalize_micro_plan\(_json_from_llm\(raw2\)',
            re.DOTALL,
        )
        assert retry_pattern.search(src), (
            "retry path must check _is_adapter_error_text BEFORE "
            "_is_unavailable_sentinel, then attempt JSON parse"
        )

    def test_no_invalid_json_message_for_adapter_errors(self):
        # The error message produced when adapter-error is detected must
        # NOT include "invalid JSON" — that's the misleading downstream
        # symptom we're fixing.
        src = self._src()
        # Find the adapter-error raise block; its message must say
        # "adapter error" and must NOT mention "invalid JSON".
        m = re.search(
            r'raise RuntimeError\(\s*\n?\s*f"Batch planner LLM adapter error'
            r'\s*\([^)]*\):\s*\{raw\[:500\]\}"\s*\n?\s*\)',
            src,
        )
        assert m, "RuntimeError message for adapter errors must include the raw upstream body"
        assert "invalid JSON" not in m.group(0), (
            "adapter-error raise must NOT mention 'invalid JSON'"
        )


if __name__ == "__main__":
    unittest.main()
