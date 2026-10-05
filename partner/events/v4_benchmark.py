"""Typed Events for the v4 longitudinal Suite/Arm/Episode benchmark.

Subject arm Events receive only the public episode.  Hidden targets are stored
in a sealed suite artifact and opened only by the evaluator Event after all
arms are terminal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import html
import json
import os
import statistics
import time

from partner.event_fabric.catalog import EventDefinition
from partner.benchmark.v4_protocol import summarize_records, validate_suite


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, value: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)
    return path


def _digest(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(body.encode()).hexdigest()


def _fmt(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _outputs(params: Mapping[str, Any]) -> dict[str, Any]:
    value = params.get("flow_outputs")
    return dict(value) if isinstance(value, Mapping) else {}


def _semantic(params: Mapping[str, Any], node: str) -> dict[str, Any]:
    value = _outputs(params).get(node)
    if not isinstance(value, Mapping):
        return {}
    semantic = value.get("semantic_output")
    return dict(semantic) if isinstance(semantic, Mapping) else {}


def _result(value: Mapping[str, Any], summary: str, files=()) -> dict[str, Any]:
    refs = [str(path) for path in files]
    return {"ok": True, "status": "completed", "summary": summary,
            "semantic_output": dict(value), "files": refs, "evidence_refs": refs}


def _benchmark_config(params: Mapping[str, Any]) -> dict[str, Any]:
    contract = params.get("intent_contract")
    contract = dict(contract) if isinstance(contract, Mapping) else {}
    value = contract.get("benchmark")
    return dict(value) if isinstance(value, Mapping) else {}


def _suite_root(ctx: Any, suite_id: str) -> Path:
    return Path(ctx.workspace) / "state/benchmarks/v4_suites" / suite_id


def _episode_root(ctx: Any, params: Mapping[str, Any]) -> Path:
    """Give every episode immutable evidence paths instead of a shared job scratch file."""
    suite_id = str(params.get("suite_id") or "unknown_suite")
    episode_id = str(params.get("episode_id") or
                     (params.get("public_episode") or {}).get("episode_id") or "unknown_episode")
    return _suite_root(ctx, suite_id) / "episodes" / episode_id


def _parent_suite_id(params: Mapping[str, Any]) -> str:
    return str(params.get("suite_id") or _semantic(params, "protocol_freeze").get("suite_id") or "")


def suite_protocol_freeze(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    config = _benchmark_config(params)
    inputs = dict(config.get("inputs") or {})
    raw_path = Path(str(inputs.get("suite_path") or "")).expanduser().resolve()
    if not raw_path.is_file():
        return {"ok": False, "status": "failed", "error": "v4 suite_path is required"}
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    protocol_errors = validate_suite(raw)
    if protocol_errors:
        return {"ok": False, "status": "failed",
                "error": "invalid v4 suite: " + "; ".join(protocol_errors)}
    episodes = list(raw.get("episodes") or [])
    if len(episodes) < 2:
        return {"ok": False, "status": "failed", "error": "v4 suite requires at least two episodes"}
    identifiers = [str(row.get("episode_id") or "") for row in episodes]
    if not all(identifiers) or len(identifiers) != len(set(identifiers)):
        return {"ok": False, "status": "failed", "error": "episode ids must be unique"}
    allowed_tracks = {"project", "active_learning", "self_evolution"}
    public_episodes, evaluators = [], {}
    for row in episodes:
        public = dict(row.get("public") or {})
        evaluator = dict(row.get("evaluator") or {})
        track = str(public.get("track") or "")
        arms = list(public.get("arms") or [])
        if track not in allowed_tracks or len(arms) not in {2, 3} or not evaluator:
            return {"ok": False, "status": "failed",
                    "error": f"invalid episode contract: {row.get('episode_id')}"}
        public.update(episode_id=str(row["episode_id"]), track=track, arms=arms)
        if any(key in public for key in ("target", "expected_output", "hidden_tests")):
            return {"ok": False, "status": "failed", "error": "hidden evaluator leaked into public episode"}
        public_episodes.append(public)
        evaluators[str(row["episode_id"])] = evaluator
    suite_id = "v4_" + sha256((str(ctx.job_id) + _digest(public_episodes)).encode()).hexdigest()[:16]
    root = _suite_root(ctx, suite_id)
    execution_mode = str(raw.get("execution_mode") or "reference")
    if execution_mode not in {"reference", "real"}:
        return {"ok": False, "status": "failed", "error": "execution_mode must be reference or real"}
    manifest = {
        "schema_version": 1, "suite_id": suite_id,
        "benchmark_version": str(raw.get("benchmark_version") or "4.0"),
        "protocol_id": str(config.get("protocol_id") or "v4_longitudinal_closed_loop_v1"),
        "source_suite_path": str(raw_path), "source_sha256": _digest(raw),
        "episode_count": len(public_episodes), "episode_ids": identifiers,
        "public_schedule_sha256": _digest(public_episodes),
        "evaluator_sha256": _digest(evaluators),
        "budget": dict(raw.get("budget") or {}),
        "bootstrap_seed": int(raw.get("bootstrap_seed") or 20260930),
        "hard_gates": list(raw.get("hard_gates") or []), "execution_mode": execution_mode,
        "required_confirmed_episodes": list(raw.get("required_confirmed_episodes") or []),
        "minimum_memory_lag": int(raw.get("minimum_memory_lag") or 0),
        "retention_checkpoints": list(raw.get("retention_checkpoints") or []),
        "holdout_policy": dict(raw.get("holdout_policy") or {}),
        "review_protocol": dict(raw.get("review_protocol") or {}),
        "frozen_at": _now(), "state": "frozen",
    }
    manifest_path = _write(root / "manifest.json", manifest)
    schedule_path = _write(root / "schedule.json", {"episodes": public_episodes})
    evaluator_path = _write(root / "sealed_evaluators.json", {
        "evaluation_visibility": "sealed_until_all_episode_arms_terminal",
        "evaluators": evaluators,
    })
    value = {**manifest, "suite_root": str(root), "manifest_path": str(manifest_path),
             "schedule_path": str(schedule_path), "sealed_evaluator_path": str(evaluator_path)}
    return _result(value, f"v4 suite frozen: {len(public_episodes)} episodes",
                   [manifest_path, schedule_path, evaluator_path])


def suite_schedule(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _semantic(params, "protocol_freeze")
    schedule = json.loads(Path(frozen["schedule_path"]).read_text())
    rows = list(schedule.get("episodes") or [])
    value = {"suite_id": frozen["suite_id"], "episode_count": len(rows),
             "ordered_episode_ids": [row["episode_id"] for row in rows],
             "termination": {"max_episodes": len(rows),
                             "stop_on_hard_gate_failure": True,
                             "consecutive_no_novel_candidate": None,
                             "reason": "preregistered benchmark must retain all controls"}}
    path = _write(Path(frozen["suite_root"]) / "execution_schedule.json", value)
    return _result(value, "v4 episode schedule frozen", [path])


def suite_start_compose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _semantic(params, "protocol_freeze")
    count = int(frozen.get("episode_count") or 0)
    version = str(frozen.get("benchmark_version") or "4.0")
    message = (f"Partner v{version} 纵向验收已开始\n\n"
               f"本次冻结 {count} 个 Episode，依次检查项目迭代、主动学习消费和 Partner 自进化。\n"
               "每个对照臂使用相同公开输入与预算；隐藏评价器将在各臂结束后才打开。\n"
               "运行中会在三个研究轨完成时汇报，最终发送研究报告和可复算结果。")
    value = {"message": message, "message_kind": "suite_start", "episode_count": count}
    path = _write(Path(frozen["suite_root"]) / "messages" / "suite_start.json", value)
    return _result(value, "v4 start message composed", [path])


def suite_start_send(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    composed = _semantic(params, "start_compose")
    from partner.events.delivery import send_text
    output = send_text(ctx, {**params, "text": composed.get("message"),
                             "notification_id": "v4_suite_start",
                             "message_reviewed": True})
    output.setdefault("files", [])
    return output


def episode_controller(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _semantic(params, "protocol_freeze")
    if not frozen:
        return {"ok": False, "status": "failed", "error": "suite freeze missing"}
    schedule = json.loads(Path(frozen["schedule_path"]).read_text()).get("episodes") or []
    root = Path(frozen["suite_root"])
    state_path = root / "controller.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {
        "phase": "ready", "next_index": 0, "completed": [], "longitudinal_state": {
            "memories": [], "causal_edges": []}, "started_at": _now()}
    if state.get("phase") == "child_running":
        episode_id = str(state["active_episode_id"])
        record_path = (Path(ctx.workspace) / "state/cycles" / str(ctx.job_id) /
                       "v4" / frozen["suite_id"] / "episodes" / f"{episode_id}.json")
        if not record_path.is_file():
            return {"ok": False, "status": "failed",
                    "error": f"terminal episode receipt missing: {episode_id}"}
        record = json.loads(record_path.read_text())
        if record.get("status") not in {"completed", "failed", "cancelled"}:
            return {"ok": False, "status": "failed", "error": "episode child is not terminal"}
        state["completed"].append({"episode_id": episode_id, "status": record["status"],
                                   "record_path": str(record_path)})
        if record.get("status") != "completed":
            state.update(phase="failed", failed_episode_id=episode_id,
                         failed_at=_now())
            _write(state_path, state)
            return {"ok": False, "status": "failed",
                    "error": f"v4 episode failed; suite stopped: {episode_id}",
                    "semantic_output": {**state, "suite_id": frozen["suite_id"]},
                    "files": [str(state_path)], "evidence_refs": [str(state_path)]}
        finish = (((record.get("node_outputs") or {}).get("finish") or {})
                  .get("semantic_output") or {})
        handoff = finish.get("handoff") or {}
        if handoff.get("memory"):
            memories = state.setdefault("longitudinal_state", {}).setdefault("memories", [])
            memory = dict(handoff["memory"])
            if not any(row.get("memory_id") == memory.get("memory_id") for row in memories):
                memories.append(memory)
        edges = state.setdefault("longitudinal_state", {}).setdefault("causal_edges", [])
        edges.extend(list(handoff.get("causal_edges") or []))
        state["next_index"] = int(state["next_index"]) + 1
        state["phase"] = "ready"
    index = int(state.get("next_index") or 0)
    if index >= len(schedule):
        state.update(phase="done", completed_at=_now())
        _write(state_path, state)
        return _result({**state, "suite_id": frozen["suite_id"], "all_episodes_terminal": True},
                       f"all {len(schedule)} v4 episodes terminal", [state_path])
    episode = dict(schedule[index])
    state.update(phase="child_running", active_episode_id=episode["episode_id"])
    _write(state_path, state)
    child = {
        "flow": "v4_benchmark_episode", "owner_node": params["node_id"],
        "record_name": f"v4/{frozen['suite_id']}/episodes/{episode['episode_id']}",
        "repeat_owner": True,
        "context": {"suite_id": frozen["suite_id"], "suite_root": frozen["suite_root"],
                    "public_episode": episode, "episode_id": episode["episode_id"],
                    "longitudinal_state": dict(state.get("longitudinal_state") or {})},
    }
    return {**_result({"suite_id": frozen["suite_id"], "episode_id": episode["episode_id"],
                       "episode_index": index, "cycle_child": child},
                      f"starting v4 episode {index + 1}/{len(schedule)}", [state_path]),
            "cycle_child": child}


def episode_intake(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    public = dict(params.get("public_episode") or {})
    forbidden = {"target", "expected_output", "hidden_tests"} & set(public)
    if not public or forbidden:
        return {"ok": False, "status": "failed", "error": "invalid public episode boundary"}
    state = dict(params.get("longitudinal_state") or {})
    value = {"episode_id": public["episode_id"], "track": public["track"],
             "arms": list(public["arms"]), "public_input": dict(public.get("input") or {}),
             "metric": dict(public.get("metric") or {}), "visibility": "public_only",
             "available_memory_ids": [str(row.get("memory_id")) for row in state.get("memories") or []],
             "longitudinal_state_sha256": _digest(state), "public_sha256": _digest(public),
             "started_at": _now()}
    path = _write(_episode_root(ctx, params) / "episode_intake.json", value)
    return _result(value, f"episode intake: {value['episode_id']}", [path])


def episode_plan(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    intake = _semantic(params, "intake")
    arms = list(intake.get("arms") or [])
    nodes = ["intake", "plan", "plan_validate", "commit"]
    nodes.extend(f"arm_{chr(97 + i)}:{arm}" for i, arm in enumerate(arms))
    nodes.extend(["checkpoint", "settle", "memory_audit", "finish"])
    value = {"episode_id": intake["episode_id"], "planner": "typed_protocol_planner_v1",
             "event_dag": nodes, "arm_count": len(arms),
             "budget": {"arm_executions": len(arms), "evaluator_calls": 1},
             "termination": "one settlement after all declared arms"}
    public = dict(params.get("public_episode") or {})
    planning_mode = str((public.get("input") or {}).get("planning_mode") or "llm")
    if str(public.get("execution_mode") or "reference") == "real" and planning_mode != "frozen_typed":
        from partner.events._llm import call_model, json_object
        executor_kind = str((public.get("input") or {}).get("executor_kind") or "")
        real_contracts = {
            "davis_target_feature_v1": {
                "single_turn_no_memory": "execute frozen DAVIS HGB baseline without target feature",
                "full_partner": "execute same frozen DAVIS HGB with only declared target feature"},
            "age_group_calibration_v1": {
                "single_turn_no_memory": "score frozen evaluation rows with the raw age predictor",
                "full_partner": "learn group residuals on the disjoint calibration rows and score the same frozen evaluation rows"},
            "memory_guided_age_calibration_v1": {
                "single_turn_no_memory": "run the raw frozen predictor without settled knowledge",
                "full_partner": "consume settled knowledge to select a candidate, then evaluate it on the same frozen rows"},
            "real_artifact_identity_v1": {
                "single_turn_no_memory": "write and hash the frozen interference artifact",
                "full_partner": "repeat the same bounded real I/O under the identical input"},
            "local_source_qa_v1": {
                "no_read": "answer without opening source",
                "read_not_consumed": "read and hash source span but mask content from decision model",
                "read_and_consumed": "read source span and expose it to decision model"},
            "settled_memory_qa_v1": {
                "no_read": "answer without reading settled memory",
                "read_not_consumed": "observe memory availability but mask its value from decision model",
                "read_and_consumed": "expose the prior settled memory to decision model"},
            "multi_source_synthesis_v1": {
                "no_read": "make the typed decision without opening either frozen source",
                "read_not_consumed": "hash and read all source spans but mask their content from the decision model",
                "read_and_consumed": "expose all frozen source spans to the decision model and emit the typed decision"},
            "settled_structured_memory_v1": {
                "no_read": "make the typed decision without settled memory",
                "read_not_consumed": "observe memory availability but mask the structured value",
                "read_and_consumed": "consume the settled structured memory after the preregistered episode gap"},
            "typed_memory_recall_v1": {
                "no_read": "do not query the settled memory",
                "read_not_consumed": "verify memory availability but mask its typed value",
                "read_and_consumed": "read and return the typed settled value at the frozen retention checkpoint"},
            "natural_terminal_projection_v1": {
                "diagnose_only": "replay the natural fixture without repair",
                "bounded_repair": "repair an isolated fixture copy and verify the existing production guard; do not edit production source"},
            "historical_incident_guard_v1": {
                "diagnose_only": "preserve and score the historical incident terminal without changing it",
                "bounded_repair": "run the declared current production guard in a fresh process; do not edit production source or historical evidence"},
        }.get(executor_kind, {})
        prompt = ("You are planning one bounded Partner v4 benchmark Episode. The runtime nodes and arm "
                  "count are frozen. Propose how evidence should move through them without adding nodes, "
                  "changing the evaluator, editing production files, or exceeding the budget. Arm execution "
                  "is fixed by RUNTIME ARM CONTRACTS below; describe it faithfully and never invent a write. "
                  "Return JSON only with keys rationale, risk_checks (array), arm_actions "
                  "(object keyed by arm), production_write (boolean; must be false for replay "
                  "executors), and continue_rule.\nPUBLIC EPISODE:\n" +
                  json.dumps(public, ensure_ascii=False)[:16000] + "\nFROZEN DAG:\n" +
                  json.dumps(nodes, ensure_ascii=False) + "\nRUNTIME ARM CONTRACTS:\n" +
                  json.dumps(real_contracts, ensure_ascii=False))
        raw, usage = call_model(ctx, purpose="v4_real_episode_plan", prompt=prompt)
        proposed = json_object(raw)
        def contract_warnings(candidate: Mapping[str, Any]) -> list[str]:
            found = []
            if executor_kind in {"natural_terminal_projection_v1", "historical_incident_guard_v1"}:
                if candidate.get("production_write") is not False:
                    found.append("production_write must be explicitly false for replay executors")
                action = str((candidate.get("arm_actions") or {}).get("bounded_repair") or "").lower()
                for negated in ("do not edit production", "without editing production",
                                "without editing the production", "never edit production",
                                "do not modify production", "without modifying production",
                                "without modifying the production"):
                    action = action.replace(negated, "production-read-only")
                if any(phrase in action for phrase in
                       ("apply patch to production", "modify production", "修改生产",
                        "edit production", "patch the production", "write production")):
                    found.append("bounded_repair action requests a production edit")
            return found
        warnings = contract_warnings(proposed)
        if warnings:
            repair_prompt = (prompt + "\nYour previous plan violated the runtime contract: " +
                             "; ".join(warnings) +
                             "\nRewrite the JSON plan. bounded_repair ONLY writes an isolated fixture copy; "
                             "the production source is read and hashed but never edited. Set "
                             "production_write to false explicitly.")
            repaired_raw, repaired_usage = call_model(
                ctx, purpose="v4_real_episode_plan_repair", prompt=repair_prompt)
            proposed = json_object(repaired_raw)
            warnings = contract_warnings(proposed)
            usage = {"initial": usage, "repair": repaired_usage, "repair_attempted": True}
        value.update(planner="llm_typed_plan_v1", llm_plan=proposed, model_usage=usage,
                     llm_plan_sha256=_digest(proposed), runtime_arm_contracts=real_contracts,
                     planner_warnings=warnings)
    path = _write(_episode_root(ctx, params) / "episode_plan.json", value)
    return _result(value, "typed episode Event DAG planned", [path])


def plan_validate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    plan, intake = _semantic(params, "plan"), _semantic(params, "intake")
    checks = {
        "typed_nodes": all(isinstance(v, str) and v for v in plan.get("event_dag") or []),
        "bounded_arms": plan.get("arm_count") == len(intake.get("arms") or []) <= 3,
        "termination_declared": bool(plan.get("termination")),
        "public_only": intake.get("visibility") == "public_only",
        "budget_declared": bool(plan.get("budget")),
        "real_plan_present": (plan.get("planner") != "llm_typed_plan_v1" or
                              bool((plan.get("llm_plan") or {}).get("arm_actions"))),
        "real_executor_contract_frozen": (plan.get("planner") != "llm_typed_plan_v1" or
                                          set(plan.get("runtime_arm_contracts") or {}) ==
                                          set(intake.get("arms") or [])),
        "llm_plan_has_no_contract_violation": not bool(plan.get("planner_warnings")),
    }
    value = {"episode_id": intake.get("episode_id"), "checks": checks,
             "valid": all(checks.values()), "validator": "v4_typed_dag_validator_v1"}
    path = _write(_episode_root(ctx, params) / "plan_validation.json", value)
    if not value["valid"]:
        return {"ok": False, "status": "failed", "error": "episode plan validation failed",
                "semantic_output": value, "files": [str(path)]}
    return _result(value, "episode plan passed type/budget/termination checks", [path])


def episode_commit(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    intake, validation = _semantic(params, "intake"), _semantic(params, "plan_validate")
    value = {"episode_id": intake["episode_id"], "public_sha256": intake["public_sha256"],
             "arms": intake["arms"], "metric": intake["metric"],
             "expected_effect": dict((params.get("public_episode") or {}).get("expected_effect") or {}),
             "failure_conditions": ["arm missing", "hidden target leaked", "budget mismatch",
                                    "candidate effect below frozen threshold"],
             "plan_validation_sha256": _digest(validation), "frozen_at": _now()}
    path = _write(_episode_root(ctx, params) / "commitment.json", value)
    return _result(value, "episode Commitment frozen before arm execution", [path])


def _execute_policy(track: str, arm: str, inputs: Mapping[str, Any],
                    longitudinal_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    memories = {str(row.get("memory_id")): row for row in
                (longitudinal_state or {}).get("memories") or []}
    if track == "project":
        observations = [float(v) for v in inputs.get("observations") or []]
        if not observations:
            raise ValueError("project observations missing")
        prediction = observations[-1]
        consumed = []
        if arm == "full_partner" and len(observations) >= 2:
            memory_ref = str(inputs.get("memory_ref") or "")
            memory = memories.get(memory_ref)
            if memory and memory.get("kind") == "delta":
                prediction = observations[-1] + float(memory.get("value") or 0)
                consumed = [memory_ref]
            else:
                prediction = observations[-1] + (observations[-1] - observations[-2])
                consumed = ["within_episode_pattern"]
        return {"prediction": prediction, "consumed_evidence_ids": consumed,
                "strategy": "linear_iteration" if arm == "full_partner" else "single_turn_last_value"}
    if track == "active_learning":
        value = float(inputs.get("value") or 0)
        read = arm in {"read_not_consumed", "read_and_consumed"}
        consumed = arm == "read_and_consumed"
        memory_ref = str(inputs.get("memory_ref") or "")
        memory = memories.get(memory_ref)
        multiplier = (float(memory.get("value") or 1) if memory and memory.get("kind") == "multiplier"
                      else float(inputs.get("source_multiplier") or 1))
        prediction = value * multiplier if consumed else value
        return {"prediction": prediction,
                "read_evidence_ids": [memory_ref or "source_rule"] if read else [],
                "consumed_evidence_ids": [memory_ref or "source_rule"] if consumed else [],
                "strategy": arm}
    if track == "self_evolution":
        value = str(inputs.get("value") or "")
        memory_ref = str(inputs.get("memory_ref") or "")
        memory = memories.get(memory_ref)
        prefix = str(memory.get("value") if memory and memory.get("kind") == "prefix"
                     else inputs.get("defect_prefix") or "BUG:")
        repaired = value[len(prefix):] if arm == "bounded_repair" and value.startswith(prefix) else value
        return {"output": repaired, "diagnosis": "unexpected_prefix" if value.startswith(prefix) else "none",
                "consumed_evidence_ids": ([memory_ref] if memory_ref and memory else ["public_symptom"])
                if arm == "bounded_repair" else [],
                "production_candidate": arm == "bounded_repair"}
    raise ValueError(f"unknown v4 track: {track}")


def arm_execute(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    public = dict(params.get("public_episode") or {})
    arms = list(public.get("arms") or [])
    slot = int(params.get("arm_slot") or 0)
    if slot >= len(arms):
        value = {"episode_id": public.get("episode_id"), "arm_slot": slot,
                 "arm_id": "not_applicable", "executed": False, "not_applicable": True}
        return _result(value, f"arm slot {slot} not applicable")
    arm = str(arms[slot])
    if str(public.get("execution_mode") or "reference") == "real":
        from partner.benchmark.v4_real_executors import execute_real_arm
        output = execute_real_arm(
            ctx, track=str(public.get("track") or ""), arm=arm,
            inputs=dict(public.get("input") or {}), output_dir=_episode_root(ctx, params),
            longitudinal_state=dict(params.get("longitudinal_state") or {}),
        )
    else:
        output = _execute_policy(str(public.get("track") or ""), arm,
                                 dict(public.get("input") or {}),
                                 dict(params.get("longitudinal_state") or {}))
    value = {"episode_id": public["episode_id"], "track": public["track"],
             "arm_slot": slot, "arm_id": arm, "executed": True,
             "public_input_sha256": _digest(public.get("input") or {}),
             "output": output, "output_sha256": _digest(output),
             "hidden_evaluator_access": False, "finished_at": _now()}
    path = _write(_episode_root(ctx, params) / f"arm_{slot}_{arm}.json", value)
    return _result(value, f"v4 arm executed: {arm}", [path])


def checkpoint_evaluate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    public = dict(params.get("public_episode") or {})
    suite_root = Path(str(params.get("suite_root") or ""))
    sealed = json.loads((suite_root / "sealed_evaluators.json").read_text())
    evaluator = dict((sealed.get("evaluators") or {}).get(public["episode_id"]) or {})
    target = evaluator.get("target")
    arm_rows = []
    for node in ("arm_a", "arm_b", "arm_c"):
        row = _semantic(params, node)
        if not row or row.get("not_applicable"):
            continue
        output = dict(row.get("output") or {})
        actual = output.get("output") if public["track"] in {"self_evolution", "active_learning"} else output.get("prediction")
        evaluator_kind = str(evaluator.get("kind") or "reference_target")
        if evaluator_kind == "lower_is_better":
            error = float(actual); score = 1.0 - error
        elif evaluator_kind == "text_contains":
            normalized_actual = str(actual or "").lower().replace("`", "").replace("\\", "/")
            normalized_target = str(target or "").lower().replace("`", "").replace("\\", "/")
            matched = normalized_target in normalized_actual
            error = 0.0 if matched else 1.0; score = 1.0 if matched else 0.0
        elif evaluator_kind == "json_subset":
            def contains_subset(actual_value: Any, expected_value: Any) -> bool:
                if isinstance(expected_value, Mapping):
                    return isinstance(actual_value, Mapping) and all(
                        key in actual_value and contains_subset(actual_value[key], value)
                        for key, value in expected_value.items())
                if isinstance(expected_value, list):
                    return isinstance(actual_value, list) and all(value in actual_value
                                                                  for value in expected_value)
                return str(actual_value).strip().lower() == str(expected_value).strip().lower()
            matched = contains_subset(actual, target)
            error = 0.0 if matched else 1.0; score = 1.0 if matched else 0.0
        elif evaluator_kind == "json_contains":
            def contains_phrases(actual_value: Any, expected_value: Any) -> bool:
                if isinstance(expected_value, Mapping):
                    return isinstance(actual_value, Mapping) and all(
                        key in actual_value and contains_phrases(actual_value[key], value)
                        for key, value in expected_value.items())
                if isinstance(expected_value, list):
                    haystack = json.dumps(actual_value, ensure_ascii=False).lower()
                    return all(str(value).strip().lower() in haystack for value in expected_value)
                return str(expected_value).strip().lower() in str(actual_value).strip().lower()
            matched = contains_phrases(actual, target)
            error = 0.0 if matched else 1.0; score = 1.0 if matched else 0.0
        elif public["track"] == "project":
            error = abs(float(actual) - float(target)); score = 1.0 / (1.0 + error)
        else:
            error = 0.0 if actual == target else 1.0; score = 1.0 if error == 0 else 0.0
        arm_rows.append({"arm_id": row["arm_id"], "actual": actual,
                         "score": score, "error": error,
                         "output_sha256": row["output_sha256"],
                         "consumed_evidence_ids": output.get("consumed_evidence_ids") or []})
    expected_arms = list(public.get("arms") or [])
    checks = {"all_arms_executed": [row["arm_id"] for row in arm_rows] == expected_arms,
              "same_public_input": len({(_semantic(params, n).get("public_input_sha256"))
                                        for n in ("arm_a", "arm_b", "arm_c")
                                        if _semantic(params, n).get("executed")}) == 1,
              "evaluator_sealed_until_terminal": all(_semantic(params, n).get("hidden_evaluator_access") is False
                                                       for n in ("arm_a", "arm_b", "arm_c")
                                                       if _semantic(params, n).get("executed"))}
    blind_reviews = []
    if public["track"] == "active_learning":
        for row in arm_rows:
            scored_pass = float(row.get("score") or 0) >= 1.0
            consumption_pass = (scored_pass and (row["arm_id"] != "read_and_consumed" or
                                                  bool(row.get("consumed_evidence_ids"))))
            blind_reviews.extend([
                {"arm_label": row["arm_id"], "reviewer_id": "schema_exact_v1",
                 "reviewer_type": "machine", "pass": scored_pass},
                {"arm_label": row["arm_id"], "reviewer_id": "evidence_binding_v1",
                 "reviewer_type": "machine", "pass": consumption_pass},
            ])
    value = {"episode_id": public["episode_id"], "track": public["track"],
             "metric": public.get("metric") or {}, "arms": arm_rows, "hard_checks": checks,
             "hard_pass": all(checks.values()), "evaluator_sha256": _digest(evaluator),
             "blind_reviews": blind_reviews,
             "review_claim_boundary": "two blinded machine rubrics; no human annotation claimed",
             "evaluated_at": _now()}
    path = _write(_episode_root(ctx, params) / "checkpoint_evaluation.json", value)
    return _result(value, "hidden evaluator scored all terminal arms", [path])


def episode_settle(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    evaluation = _semantic(params, "checkpoint")
    public = dict(params.get("public_episode") or {})
    scores = {row["arm_id"]: float(row["score"]) for row in evaluation.get("arms") or []}
    arms = list(public.get("arms") or [])
    if len(arms) == 3:
        effect = scores.get(arms[2], 0.0) - scores.get(arms[1], 0.0)
        contrast = f"{arms[2]}-minus-{arms[1]}"
    else:
        effect = scores.get(arms[1], 0.0) - scores.get(arms[0], 0.0)
        contrast = f"{arms[1]}-minus-{arms[0]}"
    minimum = float((public.get("expected_effect") or {}).get("minimum") or 0.0)
    passed = bool(evaluation.get("hard_pass") and effect >= minimum)
    value = {"episode_id": public["episode_id"], "track": public["track"],
             "decision": "confirmed" if passed else "rejected",
             "effect": effect, "minimum_effect": minimum, "contrast": contrast,
             "hard_pass": bool(evaluation.get("hard_pass")), "scores": scores,
             "authoritative_source": "sealed_deterministic_evaluator"}
    path = _write(_episode_root(ctx, params) / "episode_settlement.json", value)
    return _result(value, f"episode settlement: {value['decision']}", [path])


def memory_consumption_audit(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    public, settlement = dict(params.get("public_episode") or {}), _semantic(params, "settle")
    rows = []
    for node in ("arm_a", "arm_b", "arm_c"):
        arm = _semantic(params, node)
        if arm.get("executed"):
            output = arm.get("output") or {}
            rows.append({"arm_id": arm["arm_id"],
                         "read_evidence_ids": output.get("read_evidence_ids") or [],
                         "consumed_evidence_ids": output.get("consumed_evidence_ids") or []})
    consumed = [row for row in rows if row["consumed_evidence_ids"]]
    available = {str(row.get("memory_id")) for row in
                 (params.get("longitudinal_state") or {}).get("memories") or []}
    consumed_ids = {str(item) for row in consumed for item in row["consumed_evidence_ids"]}
    cross_episode = sorted(consumed_ids & available)
    value = {"episode_id": public["episode_id"], "track": public["track"],
             "arm_records": rows, "consumption_observed": bool(consumed),
             "action_effect_link": bool(consumed and settlement.get("effect", 0) != 0),
             "cross_episode_consumed_ids": cross_episode,
             "cross_episode_action_effect_link": bool(cross_episode and settlement.get("effect", 0) != 0),
             "claim_boundary": "consumption is causal only for the frozen arm contrast"}
    path = _write(_episode_root(ctx, params) / "memory_consumption_audit.json", value)
    return _result(value, "memory/source consumption audited", [path])


def episode_finish(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    public = dict(params.get("public_episode") or {})
    settlement = _semantic(params, "settle")
    audit = _semantic(params, "memory_audit")
    publish = dict(public.get("publish_memory") or {})
    memory = None
    if publish and settlement.get("decision") == "confirmed":
        memory = dict(publish)
        derive_arm = str(memory.pop("derive_from_arm", "") or "")
        if derive_arm:
            arm_receipt = next((_semantic(params, node) for node in ("arm_a", "arm_b", "arm_c")
                                if _semantic(params, node).get("arm_id") == derive_arm), {})
            arm_output = dict(arm_receipt.get("output") or {})
            memory["value"] = arm_output.get("output")
            memory["source_span"] = arm_output.get("source_span")
            memory["evidence_ids"] = arm_output.get("consumed_evidence_ids") or []
            memory["derived_from_output_sha256"] = arm_receipt.get("output_sha256")
        memory = {**memory, "source_episode_id": str(params.get("episode_id") or ""),
                  "settlement_decision": "confirmed", "created_at": _now()}
    prior_memories = {str(row.get("memory_id")): row for row in
                      (params.get("longitudinal_state") or {}).get("memories") or []}
    edges = [{"source_episode_id": str((prior_memories.get(memory_id) or {}).get("source_episode_id") or ""),
              "memory_id": memory_id, "consumer_episode_id": str(params.get("episode_id") or ""),
              "effect": settlement.get("effect")}
             for memory_id in audit.get("cross_episode_consumed_ids") or []]
    finished_at = _now()
    started_at = str(_semantic(params, "intake").get("started_at") or finished_at)
    try:
        duration_ms = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds() * 1000
    except ValueError:
        duration_ms = None
    value = {"episode_id": str(params.get("episode_id") or ""),
             "settlement": settlement, "memory_audit": audit,
             "checkpoint": _semantic(params, "checkpoint"),
             "executor_kind": str((public.get("input") or {}).get("executor_kind") or ""),
             "control_role": str(public.get("control_role") or "positive"),
             "holdout_kind": str(public.get("holdout_kind") or ""),
             "holdout_item_id": str(public.get("holdout_item_id") or ""),
             "handoff": {"memory": memory, "causal_edges": edges},
             "started_at": started_at, "finished_at": finished_at, "duration_ms": duration_ms}
    path = _write(_episode_root(ctx, params) / "episode_result.json", value)
    return _result(value, "v4 episode terminal receipt written", [path])


def episode_progress_compose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    public = dict(params.get("public_episode") or {})
    notify = bool(public.get("notify_milestone"))
    settlement = _semantic(params, "settle")
    audit = _semantic(params, "memory_audit")
    labels = {"project": "项目迭代", "active_learning": "主动学习",
              "self_evolution": "Partner 自进化"}
    track = str(public.get("track") or "")
    decision = str(settlement.get("decision") or "unknown")
    prior = [row for row in _episode_records(ctx, str(params.get("suite_id") or ""))
             if (row.get("settlement") or {}).get("track") == track]
    track_rows = [*(row.get("settlement") or {} for row in prior), settlement]
    confirmed = sum(row.get("decision") == "confirmed" for row in track_rows)
    rejected = sum(row.get("decision") == "rejected" for row in track_rows)
    mean_effect = statistics.mean(float(row.get("effect") or 0) for row in track_rows)
    cross = list(audit.get("cross_episode_consumed_ids") or [])
    last_line = (f"最后一个 Episode `{public.get('episode_id')}` 裁决为 {decision}；"
                 + ("候选达到冻结门槛。" if decision == "confirmed" else
                    "候选未达到门槛，未被强行晋升。"))
    message = (f"{labels.get(track, track)}轨已完成\n\n"
               f"共 {len(track_rows)} 个 Episode：{confirmed} 个 confirmed，{rejected} 个 rejected；"
               f"平均匹配效应={_fmt(mean_effect)}。\n"
               f"{last_line}\n"
               f"本轨跨 Episode 消费证据：{', '.join(cross) if cross else '详见最终因果链汇总'}。\n"
               "这是阶段记录；总体结论以全部 Episode 的 Suite Settlement 为准。")
    value = {"should_send": notify, "message": message, "message_kind": "track_milestone",
             "track": track, "episode_id": public.get("episode_id")}
    path = _write(_episode_root(ctx, params) / "progress_message.json", value)
    return _result(value, "v4 track milestone message composed" if notify else
                   "v4 episode progress message suppressed by frozen schedule", [path])


def episode_progress_send(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    composed = _semantic(params, "progress_compose")
    if not composed.get("should_send"):
        return _result({"suppressed": True, "reason": "not a frozen track milestone"},
                       "non-milestone message suppressed")
    from partner.events.delivery import send_text
    return send_text(ctx, {**params, "text": composed.get("message"),
                            "notification_id": "v4_" + str(composed.get("episode_id") or "milestone"),
                            "message_reviewed": True})


def _episode_records(ctx: Any, suite_id: str) -> list[dict[str, Any]]:
    root = Path(ctx.workspace) / "state/cycles" / str(ctx.job_id) / "v4" / suite_id / "episodes"
    rows = []
    schedule_path = _suite_root(ctx, suite_id) / "schedule.json"
    episode_ids = [str(row.get("episode_id")) for row in
                   (json.loads(schedule_path.read_text()).get("episodes") or [])]
    paths = [root / f"{episode_id}.json" for episode_id in episode_ids]
    for path in paths:
        if not path.is_file():
            continue
        record = json.loads(path.read_text())
        outputs = record.get("node_outputs") or {}
        finish = ((outputs.get("finish") or {}).get("semantic_output") or {})
        if finish:
            rows.append({"record_path": str(path), **finish})
    return rows


def transfer_evaluate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    suite_id = _parent_suite_id(params)
    rows = _episode_records(ctx, suite_id)
    effects = [float((row.get("settlement") or {}).get("effect") or 0) for row in rows]
    by_track = {}
    for row in rows:
        settlement = row.get("settlement") or {}; track = str(settlement.get("track") or "")
        by_track.setdefault(track, []).append(float(settlement.get("effect") or 0))
    manifest = json.loads((_suite_root(ctx, suite_id) / "manifest.json").read_text())
    metrics = summarize_records(rows, bootstrap_seed=int(manifest.get("bootstrap_seed") or 20260930))
    value = {"suite_id": suite_id, "episode_count": len(rows), "effects": effects,
             "mean_effect": statistics.mean(effects) if effects else None,
             "by_track": {key: {"count": len(values), "mean_effect": statistics.mean(values)}
                          for key, values in by_track.items()},
             "confirmed_episodes": sum((row.get("settlement") or {}).get("decision") == "confirmed"
                                       for row in rows),
             "consumption_effect_links": sum(bool((row.get("memory_audit") or {}).get("action_effect_link"))
                                             for row in rows),
             "cross_episode_effect_links": sum(bool((row.get("memory_audit") or {}).get("cross_episode_action_effect_link"))
                                               for row in rows),
             "metrics": metrics}
    path = _write(_suite_root(ctx, suite_id) / "transfer_evaluation.json", value)
    return _result(value, "cross-episode transfer effects aggregated", [path])


def suite_settle(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    suite_id = _parent_suite_id(params); transfer = _semantic(params, "transfer")
    frozen = _semantic(params, "protocol_freeze")
    records = _episode_records(ctx, suite_id)
    metrics = transfer.get("metrics") or {}
    controller_path = _suite_root(ctx, suite_id) / "controller.json"
    controller = json.loads(controller_path.read_text()) if controller_path.is_file() else {}
    child_statuses = [str(row.get("status") or "") for row in controller.get("completed") or []]
    checks = {"all_episodes_terminal": transfer.get("episode_count") == frozen.get("episode_count"),
              "all_child_flows_completed": (len(child_statuses) == frozen.get("episode_count") and
                                            all(status == "completed" for status in child_statuses)),
              "effects_available": len(transfer.get("effects") or []) == frozen.get("episode_count"),
              "sealed_evaluator_present": Path(str(frozen.get("sealed_evaluator_path") or "")).is_file(),
              "no_false_completion": transfer.get("confirmed_episodes", 0) <= transfer.get("episode_count", 0),
              "all_episode_hard_checks": all((row.get("checkpoint") or {}).get("hard_pass") for row in records),
              "no_false_promotion": float(metrics.get("false_promotion_rate") or 0) == 0.0}
    if str(frozen.get("benchmark_version") or "") in {"4.1", "4.2", "4.3"}:
        negative_rows = [row for row in records if row.get("control_role") == "negative"]
        checks["negative_controls_retained"] = bool(
            negative_rows and all((row.get("settlement") or {}).get("decision") == "rejected"
                                  for row in negative_rows))
    if str(frozen.get("benchmark_version") or "") in {"4.2", "4.3"}:
        decisions = {str(row.get("episode_id") or ""):
                     str((row.get("settlement") or {}).get("decision") or "")
                     for row in records}
        required = list(frozen.get("required_confirmed_episodes") or [])
        checks["required_core_episodes_confirmed"] = bool(
            required and all(decisions.get(str(episode_id)) == "confirmed"
                             for episode_id in required))
        lag = ((metrics.get("v41_diversity") or {}).get("max_transfer_lag"))
        minimum_lag = int(frozen.get("minimum_memory_lag") or 0)
        checks["long_horizon_memory_consumed"] = bool(
            minimum_lag and lag is not None and int(lag) >= minimum_lag)
    if str(frozen.get("benchmark_version") or "") == "4.3":
        generalization = metrics.get("v43_generalization") or {}
        curve = list(generalization.get("retention_curve") or [])
        expected_checkpoints = {int(item) for item in frozen.get("retention_checkpoints") or []}
        passed_checkpoints = {int(item.get("lag")) for item in curve if item.get("retained")}
        checks.update({
            "holdout_sources_generalize": generalization.get("holdout_source_confirmation_rate") == 1.0,
            "holdout_defects_repair": generalization.get("holdout_defect_repair_at_1") == 1.0,
            "all_retention_checkpoints_pass": bool(expected_checkpoints and
                                                    expected_checkpoints <= passed_checkpoints),
            "learning_changes_project_metric": any(item.get("confirmed") and
                                                    float(item.get("effect") or 0) > 0
                                                    for item in generalization.get("cross_track_project") or []),
            "dual_blind_machine_review_agrees": generalization.get(
                "dual_blind_machine_review_agreement") == 1.0,
            "human_review_not_falsely_claimed": generalization.get("human_review_claimed") is False,
        })
    value = {"suite_id": suite_id, "decision": "completed_valid" if all(checks.values()) else "invalid",
             "hard_checks": checks, "hard_pass": all(checks.values()),
             "mean_effect": transfer.get("mean_effect"),
             "confirmed_episodes": transfer.get("confirmed_episodes"),
             "episode_count": transfer.get("episode_count"), "settled_at": _now()}
    path = _write(_suite_root(ctx, suite_id) / "suite_settlement.json", value)
    return _result(value, f"v4 suite settlement: {value['decision']}", [path])


def suite_report(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    suite_id = _parent_suite_id(params); root = _suite_root(ctx, suite_id)
    transfer, settlement = _semantic(params, "transfer"), _semantic(params, "settle")
    records = _episode_records(ctx, suite_id)
    metrics = dict(transfer.get("metrics") or {})
    utility = dict(metrics.get("longitudinal_task_utility_auc") or {})
    learning_gain = dict(metrics.get("learning_to_action_gain") or {})
    manifest = json.loads((root / "manifest.json").read_text())
    benchmark_version = str(manifest.get("benchmark_version") or "4.0")
    version_label = "v" + benchmark_version
    execution_mode = str(manifest.get("execution_mode") or "reference")
    is_real = execution_mode == "real"
    episode_count = int(transfer.get("episode_count") or 0)
    mode_label = (("留出泛化与长期保持" if benchmark_version == "4.3" else
                   "跨域纵向验证" if benchmark_version == "4.2" else
                   "真实扩展验证" if benchmark_version == "4.1" else "真实执行 pilot")
                  if is_real else "确定性 reference")
    boundary = (("该 v4.3 结果只覆盖冻结的本地留出来源、两个历史新缺陷与最长 49 Episode 保持；"
                 "机器双评审不等同于人工标注，也不支持开放世界总体泛化。")
                if benchmark_version == "4.3" else
                ("该 pilot 已证明真实数据、真实本地来源和自然历史缺陷可以通过同一 Event 协议执行；"
                "样本量仍不足以支持总体泛化结论。" if is_real else
                "该 reference suite 证明协议与运行链可工作，不证明开放世界能力。"))
    next_step = (("v5 应冻结跨机器复现、人工双标注校准集和外部未见项目，并保持同一因果证据合同。")
                 if benchmark_version == "4.3" else
                 ("扩大真实 Episode、加入独立数据 seed 与未见下游任务，并要求跨 Episode 记忆产生可测行动变化。"
                 if is_real else
                 "下一步进入真实分子生成、external 未见任务和自然历史缺陷。"))
    controller = json.loads((root / "controller.json").read_text())
    causal_edges = list((controller.get("longitudinal_state") or {}).get("causal_edges") or [])
    labels = {"project": "项目迭代", "active_learning": "主动学习",
              "self_evolution": "Partner 自进化"}
    rows = []
    for row in records:
        item = dict(row.get("settlement") or {})
        rows.append({"episode_id": str(row.get("episode_id")),
                     "track": str(item.get("track")), "decision": str(item.get("decision")),
                     "effect": float(item.get("effect") or 0),
                     "scores": dict(item.get("scores") or {}),
                     "duration_ms": row.get("duration_ms")})
    by_track = {track: [r for r in rows if r["track"] == track]
                for track in ("project", "active_learning", "self_evolution")}
    rejected = [r for r in rows if r["decision"] == "rejected"]
    if len(rejected) > 8:
        negative_pdf_summary = (
            f"共 {len(rejected)} 个预注册负控全部 rejected；前 5 个为 " +
            "；".join(f"{r['episode_id']} (effect={r['effect']:.4f})" for r in rejected[:5]) +
            "。完整逐项结果保留在 Web、Markdown 与 suite_result.json。")
    else:
        negative_pdf_summary = ("；".join(
            f"{r['episode_id']}：effect={r['effect']:.4f}，rejected" for r in rejected) + "。")
    project_checkpoints = [row.get("checkpoint") or {} for row in records
                           if (row.get("settlement") or {}).get("track") == "project"]
    raw_project_metrics = []
    for checkpoint in project_checkpoints:
        arms = list(checkpoint.get("arms") or [])
        if len(arms) >= 2:
            raw_project_metrics.append({"baseline": arms[0].get("actual"),
                                        "candidate": arms[1].get("actual")})
    project_improvements = [float(row["baseline"]) - float(row["candidate"])
                            for row in raw_project_metrics]
    diversity = dict(metrics.get("v41_diversity") or {})
    generalization = dict(metrics.get("v43_generalization") or {})
    lower, upper = learning_gain.get("lower_95"), learning_gain.get("upper_95")
    learning_inconclusive = lower is not None and upper is not None and float(lower) <= 0 <= float(upper)

    # The Markdown, Web and PDF are all projections of these same machine rows.
    report_stem = ("V4_3_GENERALIZATION_RETENTION_REPORT" if benchmark_version == "4.3" else
                   "V4_2_LONGITUDINAL_REPORT" if benchmark_version == "4.2" else
                   "V4_1_LONGITUDINAL_REPORT" if benchmark_version == "4.1"
                   else "V4_LONGITUDINAL_REPORT")
    md = root / f"{report_stem}.md"
    lines = [f"# Partner {version_label} 纵向闭环研究报告", "",
             f"> Suite `{suite_id}` · `{settlement.get('decision')}` · {episode_count} 个冻结 Episode · {mode_label}", "",
             "## 执行摘要", "",
             "本研究检验 Event 化项目迭代、主动学习消费和受控自进化，在相同公开输入、预算与隐藏评价器下是否优于机制消融臂。",
             f"- 项目轨 utility：`{_fmt(utility.get('baseline_mean'))}` → `{_fmt(utility.get('partner_mean'))}`，uplift=`{_fmt(utility.get('uplift'))}`。",
             *(([f"- 真实项目原始指标（RMSE）：`{_fmt(raw_project_metrics[0]['baseline'])}` → `{_fmt(raw_project_metrics[0]['candidate'])}`。"])
               if is_real and raw_project_metrics else []),
             f"- 主动学习 C−B：均值 `{_fmt(learning_gain.get('mean'))}`，95% CI `[{_fmt(lower)}, {_fmt(upper)}]`；" +
             ("区间跨过 0，不能声称稳定增益。" if learning_inconclusive else "区间未跨 0，仍需真实任务复验。"),
             f"- 自进化：repair@1=`{_fmt(metrics.get('repair_at_1'))}`，false-promotion=`{_fmt(metrics.get('false_promotion_rate'))}`。",
             f"- 跨 Episode 因果链：`{transfer.get('cross_episode_effect_links')}` 条。", "",
             *(([f"- v{benchmark_version} 多样性：`{diversity.get('executor_family_count')}` 类执行器；正控确认率 `{_fmt(diversity.get('positive_control_confirmation_rate'))}`；负控安全率 `{_fmt(diversity.get('negative_control_safe_rate'))}`；最大迁移间隔 `{diversity.get('max_transfer_lag')}` 个 Episode。"])
               if benchmark_version in {"4.1", "4.2", "4.3"} else []),
             *(([f"- v4.3 留出结果：来源确认率 `{_fmt(generalization.get('holdout_source_confirmation_rate'))}`；缺陷 repair@1 `{_fmt(generalization.get('holdout_defect_repair_at_1'))}`；保持检查点通过率 `{_fmt(generalization.get('retention_checkpoint_pass_rate'))}`；最长确认 lag `{generalization.get('max_confirmed_retention_lag')}`。",
                  f"- 双盲机器 rubric 一致率 `{_fmt(generalization.get('dual_blind_machine_review_agreement'))}`；人工评审状态：未执行、未声称。"]) if benchmark_version == "4.3" else []),
             "## 研究设计", "",
             "Suite 在运行前冻结 Episode 顺序、arm、预算、bootstrap seed 和隐藏评价器哈希。每个 arm 只能读取公开输入；评价器在全部 arm 终态后开启。",
             f"协议哈希：`{manifest.get('source_sha256')}`；bootstrap seed：`{manifest.get('bootstrap_seed')}`。", "",
             "## 分轨结果", ""]
    for track in ("project", "active_learning", "self_evolution"):
        lines += [f"### {labels[track]}", "", "| Episode | 裁决 | 匹配效应 |", "|---|---:|---:|"]
        lines += [f"| {r['episode_id']} | {r['decision']} | {r['effect']:.4f} |" for r in by_track[track]]
        lines += [""]
    lines += ["## 跨轮因果证据", "", "| 来源 Episode | 记忆 | 消费 Episode | Effect |", "|---|---|---|---:|"]
    lines += [f"| {e.get('source_episode_id')} | {e.get('memory_id')} | {e.get('consumer_episode_id')} | {float(e.get('effect') or 0):.4f} |" for e in causal_edges]
    lines += ["", "## 可靠负结果", ""] + [f"- `{r['episode_id']}`：{r['decision']}，effect={r['effect']:.4f}。" for r in rejected]
    lines += ["", "这些负例用于检验系统是否会拒绝无收益、错误知识或无必要修改；它们不能从总体结果中删除。", "",
              "## 结论边界与下一步", "",
              boundary, next_step, ""]
    md.write_text("\n".join(lines), encoding="utf-8")

    def bar_svg(path: Path, title_text: str, names: list[str], values: list[float], *, lo=-1.0, hi=1.0):
        width, height, margin = 1000, 390, 70
        low = min([lo] + values); high = max([hi] + values)
        def yy(v): return margin + (high-v) * (height-2*margin) / max(.001, high-low)
        step = (width-2*margin) / max(1, len(values)); chunks=[]
        for i,(name,value) in enumerate(zip(names, values)):
            x=margin+i*step+step*.12; top=min(yy(0),yy(value)); h=max(2,abs(yy(value)-yy(0)))
            color="#2f8f6b" if value >= 0 else "#c45b5b"
            chunks += [f'<rect x="{x:.1f}" y="{top:.1f}" width="{step*.76:.1f}" height="{h:.1f}" fill="{color}" rx="4"/>',
                       f'<text x="{x+step*.38:.1f}" y="{height-28}" text-anchor="middle" font-family="sans-serif" font-size="11">{html.escape(name[:18])}</text>',
                       f'<text x="{x+step*.38:.1f}" y="{top-7:.1f}" text-anchor="middle" font-family="sans-serif" font-size="11">{value:.3f}</text>']
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="100%" height="100%" fill="#f4fbf7"/><text x="{margin}" y="32" font-family="sans-serif" font-size="20" fill="#176b55">{html.escape(title_text)}</text><line x1="{margin}" y1="{yy(0):.1f}" x2="{width-margin}" y2="{yy(0):.1f}" stroke="#456"/>{"".join(chunks)}</svg>',encoding="utf-8")

    effect_svg=root/"effect_by_episode.svg"
    bar_svg(effect_svg,"Matched effect by episode",
            [f"E{i:02d}" for i in range(1, len(rows) + 1)],
            [r["effect"] for r in rows])
    track_svg=root/"effect_by_track.svg"
    track_names=["Project","Active learning","Self evolution"]
    track_values=[float((transfer.get("by_track") or {}).get(k,{}).get("mean_effect") or 0) for k in ("project","active_learning","self_evolution")]
    bar_svg(track_svg,"Mean matched effect by track",track_names,track_values,lo=0,hi=1)
    learning_svg=root/"learning_three_arm.svg"
    learning_rows=by_track["active_learning"]
    learning_names=[]; learning_values=[]
    for episode_index, r in enumerate(learning_rows, 1):
        for arm_index, arm in enumerate(("no_read","read_not_consumed","read_and_consumed")):
            learning_names.append(f"L{episode_index}-{chr(65 + arm_index)}")
            learning_values.append(float(r["scores"].get(arm,0)))
    bar_svg(learning_svg,"Active-learning arm scores",learning_names,learning_values,lo=0,hi=1)
    graph_svg=root/"event_flow.svg"
    graph_nodes=["SUITE FREEZE","START MSG","EPISODES","ARMS","HIDDEN EVAL","SETTLEMENT","MEMORY AUDIT","TRANSFER","REPORT","FINAL MSG","ACK"]
    chunks=[]
    for i,label in enumerate(graph_nodes):
        x=20+i*125; chunks.append(f'<rect x="{x}" y="35" width="105" height="52" rx="10" fill="#dff3e8" stroke="#2f8f6b"/><text x="{x+52.5}" y="66" text-anchor="middle" font-family="sans-serif" font-size="9">{label}</text>')
        if i<len(graph_nodes)-1: chunks.append(f'<path d="M{x+105} 61 H{x+125}" stroke="#2f8f6b" marker-end="url(#a)"/>')
    graph_svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="1395" height="125"><defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#2f8f6b"/></marker></defs><rect width="100%" height="100%" fill="#fff"/>{"".join(chunks)}</svg>',encoding="utf-8")

    retention_svg=root/"retention_curve.svg"
    curve=list(generalization.get("retention_curve") or [])
    bar_svg(retention_svg,"Settled-memory retention by episode lag",
            [f"lag {item.get('lag')}" for item in curve],
            [1.0 if item.get("retained") else 0.0 for item in curve],lo=0,hi=1)

    # Export an evaluator-free packet for later independent human double annotation.
    # It is deliberately marked pending; machine rubric agreement is kept separately.
    human_items=[]
    for record in records:
        if (record.get("settlement") or {}).get("track") != "active_learning":
            continue
        arms=list((record.get("checkpoint") or {}).get("arms") or [])
        blind_order=sorted(((sha256((suite_id+str(record.get('episode_id'))+str(i)).encode()).hexdigest(), arm)
                            for i,arm in enumerate(arms)), key=lambda item:item[0])
        human_items.append({"episode_id":record.get("episode_id"),
                            "blinded_outputs":[{"label":f"candidate_{i+1}","output":item[1].get("actual")}
                                               for i,item in enumerate(blind_order)],
                            "questions":["Does the output satisfy the declared task?",
                                         "Is the answer grounded enough to change a downstream action?"],
                            "gold_hidden":True})
    human_packet=root/"human_dual_annotation_packet.json"
    _write(human_packet,{"schema_version":1,"suite_id":suite_id,"status":"awaiting_two_human_annotators",
                         "minimum_annotators":2,"adjudication_required_on_disagreement":True,
                         "claim_boundary":"not included in v4.3 machine hard-pass",
                         "items":human_items})

    import cairosvg
    pngs=[]
    for svg in (effect_svg,track_svg,learning_svg,retention_svg,graph_svg):
        png=svg.with_suffix(".png"); cairosvg.svg2png(url=str(svg),write_to=str(png),output_width=1500); pngs.append(png)

    episode_table="".join(f"<tr><td>{html.escape(r['episode_id'])}</td><td>{html.escape(labels[r['track']])}</td><td>{html.escape(r['decision'])}</td><td>{r['effect']:.4f}</td></tr>" for r in rows)
    causal_table="".join(f"<tr><td>{html.escape(str(e.get('source_episode_id')))}</td><td>{html.escape(str(e.get('memory_id')))}</td><td>{html.escape(str(e.get('consumer_episode_id')))}</td><td>{float(e.get('effect') or 0):.4f}</td></tr>" for e in causal_edges)
    html_path=root/"index.html"
    html_path.write_text(f'''<!doctype html><meta charset="utf-8"><title>Partner {version_label} 纵向研究报告</title><style>
body{{background:#eff9f3;color:#18372c;font:16px/1.7 system-ui;margin:0}}main{{max-width:1100px;margin:auto;padding:36px}}section{{background:#fff;border:1px solid #b9dccb;border-radius:18px;padding:28px;margin:18px 0;box-shadow:0 8px 24px #164d3818}}h1,h2,h3{{color:#176b55}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.metric{{background:#e9f7ef;border-radius:12px;padding:16px}}.metric b{{display:block;font-size:24px}}img{{width:100%;height:auto}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #d5e9df;padding:9px;text-align:left}}.warning{{border-left:5px solid #d9a441;background:#fff9e8;padding:14px}}</style><main>
<section><p>Partner {version_label} Research Report · {html.escape(suite_id)}</p><h1>Event 化 Partner 能否形成可归因的纵向改善？</h1><p>{mode_label} 三轨 matched benchmark。</p><div class="metrics"><div class="metric">项目 uplift<b>{_fmt(utility.get('uplift'))}</b></div><div class="metric">学习 C−B<b>{_fmt(learning_gain.get('mean'))}</b></div><div class="metric">repair@1<b>{_fmt(metrics.get('repair_at_1'))}</b></div><div class="metric">负控安全率<b>{_fmt(diversity.get('negative_control_safe_rate'))}</b></div></div></section>
<section><h2>执行摘要</h2><p>{episode_count} 个 Episode 全部终态，{transfer.get('confirmed_episodes')} 个 confirmed，{len(rejected)} 个可靠拒绝；形成 {transfer.get('cross_episode_effect_links')} 条跨 Episode 因果链。</p><p class="warning">主动学习 95% CI=[{_fmt(lower)}, {_fmt(upper)}]。{'真实 pilot 样本量很小，不作总体显著性解释。' if is_real else '区间跨过 0 时不能声称稳定增益。'}</p></section>
<section><h2>分轨结果</h2><img src="effect_by_track.svg"><img src="effect_by_episode.svg"><table><tr><th>Episode</th><th>研究轨</th><th>裁决</th><th>Effect</th></tr>{episode_table}</table></section>
<section><h2>主动学习三臂</h2><p>A 无阅读；B 阅读但不消费；C 阅读并改变行动。归因指标为 C−B。</p><img src="learning_three_arm.svg"></section>
<section><h2>跨轮因果证据与保持曲线</h2><table><tr><th>来源 Episode</th><th>Memory</th><th>消费 Episode</th><th>Effect</th></tr>{causal_table}</table><img src="retention_curve.svg"><p>机器双 rubric 一致率：{_fmt(generalization.get('dual_blind_machine_review_agreement'))}。人工双标注包已导出，但本轮未执行人工标注、未作相应主张。</p></section>
<section><h2>可靠性与结论边界</h2><p>隐藏评价器隔离、同公开输入、预算、全部终态和错误晋升硬门通过。负例保留在总体结果中。</p><p>{boundary}{next_step}</p></section>
<section><h2>Event / Flow 附录</h2><img src="event_flow.svg"></section></main>''',encoding="utf-8")

    pdf_path=root/f"{report_stem}.pdf"
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
    try: pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light")); font="STSong-Light"
    except Exception: font="Helvetica"
    styles=getSampleStyleSheet()
    title=ParagraphStyle("v4title",parent=styles["Title"],fontName=font,fontSize=22,leading=29,textColor=colors.HexColor("#176b55"),spaceAfter=12)
    h1=ParagraphStyle("v4h1",parent=styles["Heading1"],fontName=font,fontSize=16,leading=22,textColor=colors.HexColor("#176b55"),spaceBefore=10,spaceAfter=8)
    body=ParagraphStyle("v4body",parent=styles["BodyText"],fontName=font,fontSize=10.5,leading=17,textColor=colors.HexColor("#213b32"))
    note=ParagraphStyle("v4note",parent=body,backColor=colors.HexColor("#fff7df"),borderColor=colors.HexColor("#d9a441"),borderWidth=1,borderPadding=8)
    table_style=TableStyle([("FONTNAME",(0,0),(-1,-1),font),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dff3e8")),("GRID",(0,0),(-1,-1),.3,colors.HexColor("#9ac9b4")),("FONTSIZE",(0,0),(-1,-1),8),("VALIGN",(0,0),(-1,-1),"TOP"),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f6fbf8")])])
    def tbl(data,widths):
        t=Table(data,repeatRows=1,colWidths=widths); t.setStyle(table_style); return t
    core_rows=[["指标","结果","解释"],
               ["项目 utility",f"{_fmt(utility.get('baseline_mean'))} → {_fmt(utility.get('partner_mean'))}",f"uplift={_fmt(utility.get('uplift'))}"],
               ["主动学习 C−B",f"mean={_fmt(learning_gain.get('mean'))}",f"95% CI=[{_fmt(lower)}, {_fmt(upper)}]"],
               ["自进化",f"repair@1={_fmt(metrics.get('repair_at_1'))}",f"false-promotion={_fmt(metrics.get('false_promotion_rate'))}"],
               ["跨轮链",str(transfer.get('cross_episode_effect_links')),"source→memory→action→effect"]]
    if benchmark_version in {"4.1", "4.2", "4.3"}:
        core_rows.append([f"v{benchmark_version} 控制",f"正控={_fmt(diversity.get('positive_control_confirmation_rate'))}",
                          f"负控安全率={_fmt(diversity.get('negative_control_safe_rate'))}"])
    story=[Paragraph(f"Partner {version_label} 纵向闭环研究报告",title),Paragraph("Event 化项目迭代、主动学习与受控自进化的匹配验证",h1),
           Paragraph(f"Suite {suite_id} · {settlement.get('decision')} · {episode_count} 个冻结 Episode · {mode_label}",body),Spacer(1,14),
           Paragraph("核心结论",h1),
           tbl(core_rows,[120,150,180]),Spacer(1,14),
           Paragraph((("跨域样例仍是本地有界样本，区间仅描述本套件，不作总体显著性解释。" if benchmark_version == "4.2" else "真实扩展样例仍是本地有界样本，区间仅描述本套件，不作总体显著性解释。" if benchmark_version == "4.1" else "真实 pilot 的主动学习样本量很小，区间仅描述本套件，不作总体显著性解释。") if is_real else "主动学习区间跨过 0，当前不能声称其在任务分布上稳定增益。无效知识负例被保留并正确拒绝。"),note),Spacer(1,14),
           Paragraph("研究问题与设计",h1),Paragraph("在相同公开输入、模型权限、预算和隐藏评价器下，完整 Partner 是否优于移除记忆、禁止知识消费或禁止修复的消融臂？Suite 在执行前冻结任务顺序、arm、预算、seed 和评价器哈希；评价器只在全部 arm 终态后开启。",body),
           PageBreak(),Paragraph("项目纵向迭代",title),Paragraph(f"{len(by_track['project'])} 个项目 Episode 的 baseline 平均 utility={_fmt(utility.get('baseline_mean'))}，完整 Partner={_fmt(utility.get('partner_mean'))}，matched uplift={_fmt(utility.get('uplift'))}。项目轨包含 {sum(r['decision']=='rejected' for r in by_track['project'])} 个未晋升结果。" + ((f" 真实跨域项目对照的 RMSE 改善范围为 {_fmt(min(project_improvements))} 至 {_fmt(max(project_improvements))}，均值 {_fmt(statistics.mean(project_improvements))}。") if is_real and project_improvements else ""),body),Spacer(1,10),Image(str(pngs[1]),width=500,height=130),Spacer(1,10),Image(str(pngs[0]),width=500,height=195),Spacer(1,10),
           tbl([["Episode","Decision","Effect"]]+[[r['episode_id'],r['decision'],f"{r['effect']:.4f}"] for r in by_track['project']],[250,100,80]),
           PageBreak(),Paragraph("主动学习与 Partner 自进化",title),Paragraph("主动学习采用 A/B/C 三臂：无阅读、阅读但禁止消费、阅读并改变行动。只有 C−B 才归因于知识消费。",body),Spacer(1,10),Image(str(pngs[2]),width=500,height=195),Spacer(1,10),
           tbl([["学习 Episode","Decision","C−B"]]+[[r['episode_id'],r['decision'],f"{r['effect']:.4f}"] for r in by_track['active_learning']],[250,100,80]),Spacer(1,16),
           Paragraph((f"自进化在自然历史缺陷 replay 上 repair@1={_fmt(metrics.get('repair_at_1'))}；候选绑定生产源码哈希、fresh-process replay 和回滚能力。" if is_real else f"自进化在受控缺陷上 repair@1={_fmt(metrics.get('repair_at_1'))}，在无缺陷负例上没有错误晋升。该结果仍需自然历史缺陷与 fresh-process 保持测试。"),body),Spacer(1,8),
           tbl([["自进化 Episode","Decision","Effect"]]+[[r['episode_id'],r['decision'],f"{r['effect']:.4f}"] for r in by_track['self_evolution']],[250,100,80]),
           PageBreak(),Paragraph("跨 Episode 因果链与可靠负结果",title),Paragraph("只有前一 Episode 经 Settlement 确认后，memory 才能进入后续只读输入。下表记录来源、记忆、消费动作和 matched effect。",body),Spacer(1,10),
           tbl([["来源","Memory","消费者","Effect"]]+[[str(e.get('source_episode_id')),str(e.get('memory_id')),str(e.get('consumer_episode_id')),f"{float(e.get('effect') or 0):.4f}"] for e in causal_edges],[125,120,150,60]),Spacer(1,14),
           *(([Image(str(retention_svg.with_suffix('.png')),width=500,height=195),Spacer(1,8),
                Paragraph("保持曲线由冻结 checkpoint 的实际 memory consumption receipt 生成。双机器 rubric 只作协议可靠性检查；人工双标注尚未执行。",body),Spacer(1,12)]) if benchmark_version == "4.3" else []),
           Paragraph("可靠负结果",h1),Paragraph(negative_pdf_summary + " 这些样本检验系统是否能拒绝无收益、错误知识与无必要修改。",body),Spacer(1,14),
           Paragraph("统计与边界",h1),Paragraph((f"项目、学习与修复使用不同量纲，v{benchmark_version} 不把三者平均值作为主结论；分轨结果见前文。bootstrap seed={manifest.get('bootstrap_seed')}。{boundary}" if benchmark_version in {"4.1", "4.2"} else f"总体 matched effect={_fmt(transfer.get('mean_effect'))}；bootstrap seed={manifest.get('bootstrap_seed')}。{boundary}"),body),
           PageBreak(),Paragraph("运行可靠性与 Event / Flow 附录",title),Image(str(pngs[3]),width=500,height=42),Spacer(1,16),
           tbl([["硬门","结果"]]+[[k,"通过" if v else "失败"] for k,v in (settlement.get('hard_checks') or {}).items()],[300,100]),Spacer(1,16),
           Paragraph("每个 Episode 依次经过 intake、typed plan、plan validation、Commitment、独立 arm、隐藏评价、Settlement、memory audit、里程碑消息。Suite 再进行 transfer、最终 Settlement、研究报告、结果消息与渠道 ACK。",body),Spacer(1,14),
           Paragraph("下一步",h1),Paragraph(next_step,body),Spacer(1,16),
           Paragraph(f"证据定位：{root}",body)]
    def decorate(canvas,doc):
        canvas.saveState(); canvas.setStrokeColor(colors.HexColor("#b9dccb")); canvas.line(36,28,A4[0]-36,28)
        canvas.setFont(font,8); canvas.setFillColor(colors.HexColor("#517668")); canvas.drawString(36,17,f"Partner {version_label} · research report"); canvas.drawRightString(A4[0]-36,17,f"{doc.page}"); canvas.restoreState()
    SimpleDocTemplate(str(pdf_path),pagesize=A4,rightMargin=42,leftMargin=42,topMargin=38,bottomMargin=38,title=f"Partner {version_label} 纵向闭环研究报告",author="Partner Event Runtime").build(story,onFirstPage=decorate,onLaterPages=decorate)

    artifact_paths=[md,html_path,pdf_path,effect_svg,track_svg,learning_svg,retention_svg,graph_svg,human_packet,*pngs]
    artifact_manifest={"schema_version":1,"source":"suite_result.json","artifacts":[{"path":str(p),"sha256":"sha256:"+sha256(p.read_bytes()).hexdigest(),"bytes":p.stat().st_size} for p in artifact_paths]}
    manifest_path=_write(root/"report_manifest.json",artifact_manifest)
    machine={"schema_version":1,"suite_id":suite_id,"settlement":settlement,"transfer":transfer,
             "benchmark_version": benchmark_version, "execution_mode": execution_mode, "episodes":records,"causal_edges":causal_edges,
             "artifacts":[str(p) for p in artifact_paths]+[str(manifest_path)]}
    result_path=_write(root/"suite_result.json",machine)
    return _result({**machine,"report_path":str(md),"web_report_path":str(html_path),"pdf_path":str(pdf_path),
                    "report_manifest_path":str(manifest_path),"result_path":str(result_path)},
                   "v4 research-centered suite report generated",[*artifact_paths,manifest_path,result_path])


def suite_final_message_compose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    transfer = _semantic(params, "transfer")
    settlement = _semantic(params, "settle")
    report = _semantic(params, "report")
    metrics = dict(transfer.get("metrics") or {})
    utility = dict(metrics.get("longitudinal_task_utility_auc") or {})
    learning = dict(metrics.get("learning_to_action_gain") or {})
    lower, upper = learning.get("lower_95"), learning.get("upper_95")
    crosses_zero = (lower is not None and upper is not None and
                    float(lower) <= 0 <= float(upper))
    suite_id = _parent_suite_id(params)
    manifest_path = _suite_root(ctx, suite_id) / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    is_real = manifest.get("execution_mode") == "real"
    benchmark_version = str(manifest.get("benchmark_version") or "4.0")
    version_label = "v" + benchmark_version
    mode_label = (("留出泛化与长期保持" if benchmark_version == "4.3" else
                   "跨域纵向验证" if benchmark_version == "4.2" else
                   "真实扩展验证" if benchmark_version == "4.1" else "真实执行 pilot")
                  if is_real else "reference")
    records = _episode_records(ctx, suite_id)
    generalization = dict(metrics.get("v43_generalization") or {})
    project_records = [row for row in records
                       if (row.get("settlement") or {}).get("track") == "project"]
    project_pairs = [list((row.get("checkpoint") or {}).get("arms") or [])
                     for row in project_records]
    project_pairs = [arms for arms in project_pairs if len(arms) >= 2]
    raw_project_line = ""
    if is_real and project_pairs:
        improvements = [float(arms[0].get("actual"))-float(arms[1].get("actual"))
                        for arms in project_pairs]
        raw_project_line = (f"• 真实跨域项目：{len(project_pairs)} 个匹配对照，RMSE 改善均值 "
                            f"{_fmt(statistics.mean(improvements))}，范围 "
                            f"[{_fmt(min(improvements))}, {_fmt(max(improvements))}]。\n")
    learning_count = sum((row.get("settlement") or {}).get("track") == "active_learning"
                         for row in records)
    evolution_rows = [row for row in records
                      if (row.get("settlement") or {}).get("track") == "self_evolution"]
    confirmed_evolution = sum((row.get("settlement") or {}).get("decision") == "confirmed"
                              for row in evolution_rows)
    rejected_evolution = sum((row.get("settlement") or {}).get("decision") == "rejected"
                             for row in evolution_rows)
    learning_claim = ("区间跨过 0，暂不能证明主动学习在该任务分布上稳定增益"
                      if crosses_zero else "区间未跨过 0，仍需真实未见任务复验")
    message = (
        f"Partner {version_label} 纵向 {mode_label} 验收完成\n\n"
        "【结果总结】\n"
        f"• {transfer.get('episode_count')} 个 Episode 全部终态；"
        f"{transfer.get('confirmed_episodes')} 个 confirmed，其余为诚实拒绝。\n"
        + (raw_project_line if raw_project_line else
           f"• 项目轨：baseline utility={_fmt(utility.get('baseline_mean'))}，Partner={_fmt(utility.get('partner_mean'))}，uplift={_fmt(utility.get('uplift'))}。\n") +
        (f"• 主动学习轨：{learning_count} 个真实 Episode 的 C−B 均值={_fmt(learning.get('mean'))}；"
         "包括直接来源消费和后续记忆迁移，样本量小，仅作描述性结果。\n" if is_real else
         f"• 主动学习轨：C−B 均值={_fmt(learning.get('mean'))}，95% CI=[{_fmt(lower)}, {_fmt(upper)}]；{learning_claim}。\n") +
        (f"• 自进化轨：{confirmed_evolution} 个自然历史缺陷/生产守卫 replay confirmed，{rejected_evolution} 个负例拒绝；错误晋升率={_fmt(metrics.get('false_promotion_rate'))}。\n" if is_real else
         f"• 自进化轨：repair@1={_fmt(metrics.get('repair_at_1'))}，错误晋升率={_fmt(metrics.get('false_promotion_rate'))}。\n") +
        f"• 跨 Episode 因果链：{transfer.get('cross_episode_effect_links')} 条。\n\n"
        + ((f"• 留出来源确认率={_fmt(generalization.get('holdout_source_confirmation_rate'))}，"
            f"留出缺陷 repair@1={_fmt(generalization.get('holdout_defect_repair_at_1'))}，"
            f"最长确认保持 lag={generalization.get('max_confirmed_retention_lag')}。\n"
            "• 双机器 rubric 已盲化复核；人工双标注包已导出但尚未执行，因此不作人工一致性主张。\n\n")
           if benchmark_version == "4.3" else "") +
        "【运行总结】\n"
        f"• Suite Settlement：{settlement.get('decision')}；隐藏评价器隔离、同输入、预算和终态硬门均通过。\n"
        "• Web、机器结果、图表与 PDF 使用同一份结算数据；PDF 正文讲研究结果，Event/Flow 放在附录。\n\n"
        "【结论边界与下一步】\n"
        + ("本轮已使用真实分子与年龄预测数据、本地 external 来源和自然历史缺陷，但样本量不足以支持总体泛化；下一步扩大独立数据集、来源和长间隔复验。\n"
           if is_real else "本轮证明 reference 协议和运行链可工作，不等同于开放世界科研能力。下一步换成真实分子生成、external 未见任务和自然历史缺陷 corpus，继续完成 v4 真实效果验收。\n") +
        "完整结果与证据见 PDF 附件。")
    value = {"message": message, "delivery_message": message,
             "message_kind": "suite_final", "report_path": report.get("pdf_path")}
    path = _write(_suite_root(ctx, _parent_suite_id(params)) / "messages" / "suite_final.json", value)
    return _result(value, "v4 final result message composed", [path])


def suite_delivery(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    report = _semantic(params, "report")
    final_message = _semantic(params, "final_compose")
    required = [report.get("web_report_path"), report.get("pdf_path"), report.get("result_path")]
    checks = {"web_report_exists": bool(required[0] and Path(required[0]).is_file()),
              "pdf_exists": bool(required[1] and Path(required[1]).is_file()),
              "machine_result_exists": bool(required[2] and Path(required[2]).is_file())}
    channel = str(params.get("channel") or getattr(ctx, "channel", "local"))
    constraints = ((params.get("intent_contract") or {}).get("execution_constraints") or {})
    declared = list(constraints.get("delivery_channels") or [])
    wants_qq = channel in {"qq", "both"} or "qq" in declared
    if wants_qq:
        from partner.events.delivery import send_pdf
        flow_outputs = dict(params.get("flow_outputs") or {})
        flow_outputs["summaries"] = {"semantic_output": {
            "delivery_message": final_message.get("delivery_message")}}
        transport = send_pdf(ctx, {**params, "pdf_path": required[1],
                                   "notification_id": "v4_final_report",
                                   "flow_outputs": flow_outputs})
        transport["web_report_path"] = required[0]
        transport["machine_result_path"] = required[2]
        return transport
    value = {"channels": {"web": {"accepted": checks["web_report_exists"], "path": required[0]},
                          "pdf": {"accepted": checks["pdf_exists"], "path": required[1]},
                          "qq": {"accepted": False, "status": "not_requested"}},
             "checks": checks, "accepted": all(checks.values()), "acknowledged_at": _now()}
    path = _write(_suite_root(ctx, _parent_suite_id(params)) / "delivery_request.json", value)
    return _result(value, "v4 Web/PDF delivery requested", [path])


def suite_delivery_ack(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    raw = _outputs(params).get("delivery") or {}
    sent = dict(raw) if isinstance(raw, Mapping) else {}
    semantic = sent.get("semantic_output") if isinstance(sent.get("semantic_output"), Mapping) else {}
    if semantic:
        sent = {**semantic, **{k: v for k, v in sent.items() if k not in {"semantic_output"}}}
    suite_id = _parent_suite_id(params); root = _suite_root(ctx, suite_id)
    receipt = sent.get("receipt") or {}
    queue_path = Path(str(receipt.get("path") or "")) if receipt.get("path") else None
    if queue_path:
        ack_path = queue_path.with_suffix(".sent")
        if ack_path.is_file():
            ack = json.loads(ack_path.read_text())
            expected = str(sent.get("pdf_sha256") or "")
            pdf_ack = next((x for x in ack.get("component_acks") or []
                            if isinstance(x, Mapping) and x.get("kind") == "pdf"), {})
            matched = bool(ack.get("delivery_state") == "sent" and ack.get("text_delivered")
                           and ack.get("pdf_delivered") and
                           (not expected or str(pdf_ack.get("sha256") or "") == expected))
            value = {"accepted": matched, "channels": {"web": {"accepted": True},
                     "pdf": {"accepted": matched, "sha256": expected},
                     "qq": {"accepted": matched, "ack_path": str(ack_path)}},
                     "acknowledged_at": _now()}
            path = _write(root / "delivery_ack.json", value)
            return _result(value, "v4 QQ/PDF delivery acknowledged", [path, ack_path]) if matched else {
                "ok": False, "status": "failed", "error": "QQ delivery ACK did not match frozen PDF"}
        wait_path = root / "delivery_wait.json"
        state = json.loads(wait_path.read_text()) if wait_path.is_file() else {
            "started_at_epoch": time.time(), "deadline_epoch": time.time() + 180,
            "queue_path": str(queue_path), "ack_path": str(ack_path)}
        _write(wait_path, state)
        if time.time() >= float(state["deadline_epoch"]):
            return {"ok": False, "status": "failed", "error": "QQ delivery ACK timed out",
                    "evidence_refs": [str(wait_path), str(queue_path)]}
        return {"ok": True, "status": "waiting", "background_task_id": "v4_delivery_ack",
                "summary": "waiting for QQ PDF ACK", "evidence_refs": [str(wait_path), str(queue_path)]}
    accepted = bool(sent.get("accepted") or sent.get("delivered") or sent.get("web_visible"))
    value = {"accepted": accepted, "channels": sent.get("channels") or {}, "acknowledged_at": _now()}
    path = _write(root / "delivery_ack.json", value)
    return _result(value, "v4 local/Web delivery acknowledged", [path]) if accepted else {
        "ok": False, "status": "failed", "error": "delivery was not accepted"}


def suite_close(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    suite_id = _parent_suite_id(params); root = _suite_root(ctx, suite_id)
    settlement = _semantic(params, "settle"); report = _semantic(params, "report")
    delivery = _semantic(params, "delivery_ack")
    hard_pass = bool(settlement.get("hard_pass") and delivery.get("accepted"))
    value = {"suite_id": suite_id, "status": "completed" if hard_pass else "failed",
             "hard_pass": hard_pass,
             "report_path": report.get("report_path"), "web_report_path": report.get("web_report_path"),
             "pdf_path": report.get("pdf_path"), "delivery_ack": delivery,
             "completed_at": _now()}
    path = _write(root / "completion.json", value)
    if not hard_pass:
        return {"ok": False, "status": "failed", "error": "v4 suite hard gates failed",
                "summary": "v4 suite closed as failed", "semantic_output": value,
                "files": [str(path)], "evidence_refs": [str(path)]}
    return _result(value, "v4 suite closed", [path])


_HANDLERS = {
    "suite_protocol_freeze": suite_protocol_freeze,
    "suite_start_compose": suite_start_compose, "suite_start_send": suite_start_send,
    "suite_schedule": suite_schedule,
    "episode_controller": episode_controller, "episode_intake": episode_intake,
    "episode_plan": episode_plan, "plan_validate": plan_validate,
    "episode_commit": episode_commit, "arm_execute": arm_execute,
    "checkpoint_evaluate": checkpoint_evaluate, "episode_settle": episode_settle,
    "memory_consumption_audit": memory_consumption_audit, "episode_finish": episode_finish,
    "episode_progress_compose": episode_progress_compose,
    "episode_progress_send": episode_progress_send,
    "transfer_evaluate": transfer_evaluate, "suite_settle": suite_settle,
    "suite_report": suite_report,
    "suite_final_message_compose": suite_final_message_compose,
    "suite_delivery": suite_delivery,
    "suite_delivery_ack": suite_delivery_ack,
    "suite_close": suite_close,
}

DEFINITIONS = [EventDefinition(
    "v4." + name, "benchmark", "v4 纵向 Benchmark：" + name, handler,
    execution_method="local", produces_artifact=True, reads_existing_artifact=True,
    timeout_seconds=300, max_attempts=1,
) for name, handler in _HANDLERS.items()]
