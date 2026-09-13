import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "benchmark_tasks", Path(__file__).parents[1] / "partner/core/tasks.py")
tasks = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tasks)


def test_01(): assert tasks.normalize_retry("Rate_Limited") == "retryable"
def test_02(): assert tasks.clamp(-2, 0, 3) == 0
def test_03(): assert tasks.unique_ordered(["z", "z", "a", "z"]) == ["z", "a"]
def test_04():
    assert tasks.parse_bool("TRUE") is True
    with pytest.raises(ValueError): tasks.parse_bool("perhaps")
def test_05(): assert tasks.capped_backoff(1.5, 1, 20) == 3.0
def test_06():
    assert tasks.host_allowed("api.example.com", {"example.com"})
    assert not tasks.host_allowed("notexample.com", {"example.com"})
def test_07(): assert tasks.redact_bearer("x bearer SECRET-9 y") == "x bearer <REDACTED> y"
def test_08():
    assert tasks.chunks([], 3) == []
    with pytest.raises(ValueError): tasks.chunks([1], 0)
def test_09(): assert tasks.version_key("10.0.1") == (10, 0, 1)
def test_10():
    base = {"a": 1}; result = tasks.merge_config(base, {"b": 2}); result["a"] = 9
    assert base == {"a": 1}
def test_11():
    assert tasks.timeout_seconds(float("inf"), 15) == 15
    assert tasks.timeout_seconds("4", 15) == 4
def test_12():
    assert tasks.top_k([1, 3, 2], 0) == []
    assert tasks.top_k([1, 3, 2], 9) == [3, 2, 1]
def test_13(): assert tasks.safe_mean([1.0, 2.0, 3.0]) == 2.0
def test_14(tmp_path):
    root = tmp_path / "app"; child = root / "x"
    assert tasks.is_within(str(root), str(child))
def test_15():
    assert tasks.canonical_status("SUCCESS") == "done"
    assert tasks.canonical_status("error") == "failed"
def test_16():
    assert tasks.retry_after(-3) == 0
    assert tasks.retry_after("bad") == 0
def test_17(): assert tasks.idempotency_key(["A", " b ", "C"]) == "a:b:c"
def test_18():
    assert tasks.confidence(float("inf")) == 0.0
    assert tasks.confidence(-0.4) == 0.0
def test_19():
    assert tasks.has_extension("x.json", {".JSON"})
    assert not tasks.has_extension("x.txt", {"pdf"})
def test_20(): assert tasks.next_offset(0, 25, 25) == 25
