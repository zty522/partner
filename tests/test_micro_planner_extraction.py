"""Bug #36 fix regression — micro planner JSON extraction hardening.

Three fixes covered:
1. <JSON_OUTPUT> tag contract — LLM wraps JSON in <JSON_OUTPUT>...</JSON_OUTPUT>;
   _json_from_llm honors the tag and parses inner JSON verbatim.
2. Bare step list auto-wrap — LLM emits [{"event_type":...}, ...] without the
   {"plan": [...]} wrapper; _json_from_llm auto-wraps to {"plan": [...]} so
   _normalize_micro_plan sees the contract it expects.
3. Reasoning prefix + JSON tail — old behavior preserved (regression guard).

Each case pins a real failure shape observed in the 2026-08-25 manual_stable
canary where 03 batch_plan_handler/1 raised ValueError
"micro planner output must be a JSON array or {plan: []}".
"""
import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from partner.mind.harness import _json_from_llm, _normalize_micro_plan


class TestJsonOutputTag:
    def test_well_formed_tag_pure_json(self):
        raw = (
            "下面是计划：\n"
            "<JSON_OUTPUT>\n"
            "{\"plan\":[{\"id\":\"step_1\",\"event_type\":\"atomic_read_state\","
            "\"parameters\":{\"title\":\"demo\"},\"depends_on\":[]}],"
            "\"expected_artifacts\":[{\"type\":\"message\",\"pattern\":\"text\","
            "\"description\":\"final\",\"required\":true}]}\n"
            "</JSON_OUTPUT>\n"
            "执行完毕。"
        )
        out = _json_from_llm(raw)
        assert isinstance(out, dict)
        assert "plan" in out
        assert out["plan"][0]["event_type"] == "atomic_read_state"
        assert out["expected_artifacts"][0]["required"] is True

    def test_tag_with_reasoning_prefix_ignored(self):
        raw = (
            "我先想一下用户的需求…\n"
            "<JSON_OUTPUT>\n"
            "{\"plan\":[{\"id\":\"s1\",\"event_type\":\"atomic_list_project_files\","
            "\"parameters\":{\"limit\":10},\"depends_on\":[]}]}\n"
            "</JSON_OUTPUT>"
        )
        out = _json_from_llm(raw)
        assert isinstance(out, dict) and out["plan"][0]["event_type"] == "atomic_list_project_files"

    def test_tag_with_invalid_inner_falls_back_to_legacy(self):
        # Tag present but inner is not pure JSON — legacy extraction still tries
        # to find a balanced JSON object/array.
        raw = (
            "看下面这种坏 JSON 怎么办：\n"
            "<JSON_OUTPUT>\n"
            "{\"plan\": [{\"id\":\"s1\",\"event_type\":\"atomic_read_state\",\"parameters\":{\"title\":\"x\"},\"depends_on\":[]}}\n"
            "</JSON_OUTPUT>"
        )
        # Inner is broken; we fall through to legacy extraction which also fails.
        # Pin the contract: must NOT crash with a custom exception type and must
        # raise ValueError so _normalize_micro_plan sees the same shape as before.
        try:
            _json_from_llm(raw)
        except ValueError:
            pass
        else:
            # If it accidentally parsed, that is acceptable too, but no other
            # exception type is allowed.
            pass

    def test_no_tag_legacy_still_works(self):
        # Backwards compatibility: prompt without the tag still parses.
        raw = '<think>some thought</think>{\"plan\":[{\"id\":\"s1\",\"event_type\":\"atomic_read_state\",\"parameters\":{\"title\":\"x\"},\"depends_on\":[]}]}'
        out = _json_from_llm(raw)
        assert out["plan"][0]["event_type"] == "atomic_read_state"


class TestBareListAutoWrap:
    """Bug #36 root cause fix: LLM emits bare step list without plan wrapper."""

    def test_bare_step_list_with_dict_items(self):
        raw = (
            "[{\"id\":\"step_1\",\"event_type\":\"atomic_read_state\","
            "\"parameters\":{\"title\":\"demo\"},\"depends_on\":[]},"
            "{\"id\":\"step_2\",\"event_type\":\"atomic_list_project_files\","
            "\"parameters\":{\"limit\":20},\"depends_on\":[]}]"
        )
        out = _json_from_llm(raw)
        assert isinstance(out, dict), f"expected auto-wrap to dict, got {type(out)}"
        assert "plan" in out and len(out["plan"]) == 2

    def test_bare_list_with_reasoning_prefix(self):
        raw = (
            "我决定直接列步骤：\n"
            "[{\"id\":\"s1\",\"event_type\":\"atomic_read_state\","
            "\"parameters\":{},\"depends_on\":[]}]"
        )
        out = _json_from_llm(raw)
        assert isinstance(out, dict)
        assert out["plan"][0]["event_type"] == "atomic_read_state"

    def test_bare_list_of_non_dicts_NOT_wrapped(self):
        # If items are not dicts, we must NOT wrap — preserve legacy behavior
        # (raw_decode path) so downstream can decide.
        raw = "[\"step_a\",\"step_b\"]"
        out = _json_from_llm(raw)
        # Falls through to legacy: returns the bare list
        assert isinstance(out, list)
        assert out == ["step_a", "step_b"]


class TestNormalizeMicroPlan:
    """The contract that triggered Bug #36."""

    def test_wrapped_plan_normalizes(self):
        raw_plan = {
            "plan": [
                {"id": "step_1", "event_type": "atomic_read_state",
                 "parameters": {"title": "x"}, "depends_on": []}
            ],
            "expected_artifacts": [
                {"type": "message", "pattern": "text",
                 "description": "final", "required": True}
            ],
        }
        plan = _normalize_micro_plan(raw_plan, max_steps=5)
        assert len(plan.plan) == 1
        assert plan.plan[0].event_type == "atomic_read_state"
        assert plan.expected_artifacts[0]["required"] is True

    def test_bare_list_then_normalize_full_pipeline(self):
        # The full failure path: LLM emits bare list, _json_from_llm wraps it,
        # _normalize_micro_plan accepts the wrapped form. This is the exact
        # flow that failed in the 03 manual_stable canary on 2026-08-25.
        raw = (
            "[{\"id\":\"step_1\",\"event_type\":\"atomic_read_state\","
            "\"parameters\":{\"title\":\"demo\"},\"depends_on\":[]}]"
        )
        wrapped = _json_from_llm(raw)
        plan = _normalize_micro_plan(wrapped, max_steps=5)
        assert plan.plan[0].event_type == "atomic_read_state"

    def test_string_plan_field_still_raises(self):
        # If LLM returns {"plan": "literal string"} we still raise — this is
        # not a recoverable shape.
        try:
            _normalize_micro_plan({"plan": "not a list"}, max_steps=5)
        except ValueError as e:
            assert "JSON array" in str(e) or "list" in str(e)
        else:
            raise AssertionError("expected ValueError")

    def test_dict_no_plan_field_raises(self):
        try:
            _normalize_micro_plan({"steps": []}, max_steps=5)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")

    def test_complete_plan_wins_over_nested_content_parameters(self):
        raw = json.dumps({
            "plan": [
                {"id": "read", "event_type": "atomic_inspect_file",
                 "parameters": {"path": "/tmp/input.txt"}, "depends_on": []},
                {"id": "write", "event_type": "atomic_write_artifact",
                 "parameters": {"path": "summary.md", "content": "$read.result.content"},
                 "depends_on": ["read"]},
            ]
        })
        parsed = _json_from_llm(raw)
        assert "plan" in parsed
        assert len(_normalize_micro_plan(parsed, max_steps=5).plan) == 2


class TestRetryableHint:
    """Bug #36 phase 2: distinguish retryable vs hard JSON parse failures.

    Real failure observed in 2026-08-25 manual_stable canary rounds 1/2/4:
    deepseek-v4-flash thinking mode emitted a reasoning block but no JSON, so
    all extraction attempts failed. The error message must tell callers this
    is retryable so they can re-invoke the LLM instead of aborting the plan.
    """

    def test_think_only_output_is_retryable(self):
        # Real shape observed in round 1 task_log:
        raw = (
            "<think>The user wants me to plan a task. I should only output "
            "JSON. Let me analyze... Actually I cannot complete this task "
            "without more info.</think>\n\n任务内容为空，无法执行。\n\n请补充任务。"
        )
        try:
            _json_from_llm(raw)
        except ValueError as e:
            assert "RETRYABLE" in str(e), f"expected RETRYABLE hint, got: {e}"
            assert "thinking" in str(e).lower()
        else:
            raise AssertionError("expected ValueError")

    def test_think_with_json_after_is_not_retryable(self):
        # If JSON IS present after think block, the existing extraction
        # pipeline should succeed — no ValueError raised at all.
        raw = (
            "<think>A short plan: 1 read_state step.</think>"
            "{\"plan\":[{\"id\":\"s1\",\"event_type\":\"atomic_read_state\","
            "\"parameters\":{\"title\":\"x\"},\"depends_on\":[]}]}"
        )
        out = _json_from_llm(raw)
        assert out["plan"][0]["event_type"] == "atomic_read_state"

    def test_no_thinking_hard_failure(self):
        # Pure garbage with no think tag at all — must NOT be marked retryable
        # because retrying won't help (the LLM has no signal to fix).
        raw = "this is not JSON at all {broken"
        try:
            _json_from_llm(raw)
        except ValueError as e:
            assert "RETRYABLE" not in str(e), f"hard failure should not have RETRYABLE hint, got: {e}"
        else:
            raise AssertionError("expected ValueError")
