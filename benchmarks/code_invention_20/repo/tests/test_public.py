import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "benchmark_tasks", Path(__file__).parents[1] / "partner/core/tasks.py")
tasks = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tasks)


def test_01(): assert tasks.normalize_retry(" TIMEOUT ") == "retryable"
def test_02(): assert tasks.clamp(5, 0, 3) == 3
def test_03(): assert tasks.unique_ordered(["b", "a", "b"]) == ["b", "a"]
def test_04(): assert tasks.parse_bool("false") is False
def test_05(): assert tasks.capped_backoff(2, 4, 10) == 10
def test_06(): assert not tasks.host_allowed("evil-example.com", {"example.com"})
def test_07(): assert tasks.redact_bearer("Authorization: Bearer abc123") == "Authorization: Bearer <REDACTED>"
def test_08(): assert tasks.chunks([1, 2, 3], 2) == [[1, 2], [3]]
def test_09(): assert tasks.version_key("2.10") > tasks.version_key("2.9")
def test_10(): assert tasks.merge_config({"a": 1}, {"a": 2})["a"] == 2
def test_11(): assert tasks.timeout_seconds(-1, 20) == 20
def test_12(): assert tasks.top_k([1, 3, 2], 2) == [3, 2]
def test_13(): assert tasks.safe_mean([]) is None
def test_14(tmp_path): assert not tasks.is_within(str(tmp_path / "app"), str(tmp_path / "application/x"))
def test_15(): assert tasks.canonical_status("surprise") == "unknown"
def test_16(): assert tasks.retry_after("12") == 12
def test_17(): assert tasks.idempotency_key([" task ", "", "RUN"]) == "task:run"
def test_18(): assert tasks.confidence(float("inf")) == 0.0
def test_19(): assert tasks.has_extension("report.PDF", {"pdf"})
def test_20(): assert tasks.next_offset(20, 10, 4) is None
