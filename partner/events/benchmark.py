"""Benchmark controller, checkpoint and evaluator Events.

Handlers never start another Event.  A submit Event returns a typed child-flow
request; the runtime controller owns suspend/start/resume.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
import json
import csv
import math
import random
import subprocess
import uuid

from partner.benchmark.event_runtime import (
    BenchmarkProtocolStore, BenchmarkRunStore, digest, numeric_metrics,
    public_subject_view,
)
from partner.event_fabric.catalog import EventDefinition


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _outputs(params: Mapping[str, Any]) -> dict[str, Any]:
    value = params.get("flow_outputs")
    return dict(value) if isinstance(value, Mapping) else {}


def _semantic(params: Mapping[str, Any], node: str) -> dict[str, Any]:
    value = _outputs(params).get(node)
    if not isinstance(value, Mapping) or not isinstance(value.get("semantic_output"), Mapping):
        return {}
    return dict(value["semantic_output"])


def _config(params: Mapping[str, Any]) -> dict[str, Any]:
    contract = params.get("intent_contract")
    contract = dict(contract) if isinstance(contract, Mapping) else {}
    benchmark = contract.get("benchmark")
    return dict(benchmark) if isinstance(benchmark, Mapping) else {}


def _run_id(params: Mapping[str, Any]) -> str:
    context = params.get("run_context") if isinstance(params.get("run_context"), Mapping) else {}
    return str(params.get("benchmark_run_id") or context.get("benchmark_run_id") or
               _config(params).get("run_id") or "")


def _protocol(ctx: Any, params: Mapping[str, Any]):
    config = _config(params)
    protocol_id = str(params.get("benchmark_protocol_id") or
                      (params.get("run_context") or {}).get("benchmark_protocol_id") or
                      config.get("protocol_id") or "")
    if not protocol_id:
        raise ValueError("benchmark protocol_id is required")
    return BenchmarkProtocolStore(ctx.workspace).load(protocol_id)


def _write(ctx: Any, params: Mapping[str, Any], name: str, value: Mapping[str, Any]) -> Path:
    run_id = _run_id(params)
    if not run_id:
        raise ValueError("benchmark run_id is required")
    return BenchmarkRunStore(ctx.workspace, run_id).write(name, value)


def signal_validate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    config = _config(params)
    context = params.get("run_context") if isinstance(params.get("run_context"), Mapping) else {}
    if str(context.get("run_mode") or config.get("run_mode") or "") != "benchmark":
        return {"ok": False, "status": "failed", "error": "benchmark flow requires run_mode=benchmark"}
    protocol, path = _protocol(ctx, params)
    run_id = _run_id(params)
    result = {"run_id": run_id, "protocol_id": protocol.protocol_id,
              "protocol_version": protocol.version, "protocol_path": str(path)}
    output = _write(ctx, params, "signal.json", result)
    return {"ok": True, "status": "completed", "summary": "benchmark signal validated",
            "evidence_refs": [str(output)], "semantic_output": result}


def protocol_resolve(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, path = _protocol(ctx, params)
    payload = protocol.to_dict()
    payload["protocol_hash"] = digest(payload)
    output = _write(ctx, params, "protocol.json", payload)
    return {"ok": True, "status": "completed", "summary": f"resolved {protocol.protocol_id}",
            "evidence_refs": [str(path), str(output)], "semantic_output": payload}


def environment_preflight(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    config = _config(params)
    supplied = config.get("inputs") if isinstance(config.get("inputs"), Mapping) else {}
    missing = [key for key in protocol.required_inputs if not supplied.get(key)]
    invalid_paths = [key for key in protocol.required_inputs if key.endswith("_path")
                     and supplied.get(key) and not Path(str(supplied[key])).is_file()]
    from partner.event_flows.registry import build_flow_registry
    from partner.event_fabric.catalog import build_catalog
    registry = build_flow_registry()
    catalog = build_catalog(workspace=ctx.workspace)
    flow_errors: list[str] = []
    try:
        subject = registry.get(protocol.subject_flow)
        parent = registry.get("benchmark_experiment")
        from partner.benchmark.flow_validation import validate_benchmark_flows
        flow_errors.extend(validate_benchmark_flows(
            protocol=protocol, parent=parent, subject=subject, catalog=catalog))
    except KeyError:
        flow_errors.append(f"missing_subject_flow:{protocol.subject_flow}")
    valid = not missing and not invalid_paths and not flow_errors
    record = {"valid": valid, "required_inputs": list(protocol.required_inputs),
              "missing_inputs": missing, "flow_errors": flow_errors,
              "invalid_paths": invalid_paths,
              "catalog_version": catalog.version, "checked_at": _now()}
    output = _write(ctx, params, "preflight.json", record)
    return {"ok": valid, "status": "completed" if valid else "failed",
            "summary": "benchmark preflight passed" if valid else "benchmark preflight failed",
            "error": "missing inputs or invalid benchmark flow" if not valid else "",
            "failure_class": "benchmark_protocol" if not valid else "",
            "evidence_refs": [str(output)], "semantic_output": record}


def run_freeze(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    config = _config(params)
    supplied = config.get("inputs") if isinstance(config.get("inputs"), Mapping) else {}
    try:
        code_revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
            text=True, capture_output=True, check=True, timeout=5).stdout.strip()
    except Exception:
        code_revision = "unavailable"
    input_artifacts = {}
    for key, value in supplied.items():
        path = Path(str(value))
        if path.is_file():
            hasher = __import__("hashlib").sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
            input_artifacts[key] = {"path": str(path.resolve()), "bytes": path.stat().st_size,
                                    "sha256": hasher.hexdigest()}
    manifest = {
        "schema_version": 1, "run_id": _run_id(params), "state": "running",
        "run_mode": "benchmark", "protocol_id": protocol.protocol_id,
        "protocol_version": protocol.version, "protocol_hash": digest(protocol.to_dict()),
        "code_revision": code_revision, "catalog_version": str(
            (params.get("run_context") or {}).get("catalog_version") or ""),
        "inputs": dict(supplied), "inputs_hash": digest(supplied),
        "input_artifacts": input_artifacts,
        "arms": list(protocol.arms), "budget": dict(protocol.budget),
        "checkpoint_policy_ref": str((params.get("run_context") or {}).get(
            "checkpoint_policy_ref") or "protocol"),
        "evaluation_visibility": "hidden_until_terminal", "started_at": _now(),
    }
    output = _write(ctx, params, "manifest.json", manifest)
    return {"ok": True, "status": "completed", "summary": "benchmark run frozen",
            "evidence_refs": [str(output)], "semantic_output": manifest}


def variant_plan(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    config = _config(params)
    supplied = config.get("inputs") if isinstance(config.get("inputs"), Mapping) else {}
    variants = []
    for arm in protocol.arms:
        view = public_subject_view(protocol, supplied, arm)
        variants.append({"arm_id": arm, "subject_flow": protocol.subject_flow,
                         "subject_view": view, "subject_view_hash": digest(view)})
    record = {"variants": variants, "allowed_differences": list(protocol.allowed_arm_differences),
              "parity_hash": digest({"inputs": supplied, "budget": protocol.budget})}
    output = _write(ctx, params, "variant_plan.json", record)
    return {"ok": True, "status": "completed", "summary": "benchmark variants planned",
            "evidence_refs": [str(output)], "semantic_output": record}


def _diff_paths(left: Any, right: Any, prefix: str = "") -> set[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        paths: set[str] = set()
        for key in set(left) | set(right):
            if key == "arm_id":
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.update(_diff_paths(left.get(key), right.get(key), path))
        return paths
    return set() if left == right else {prefix}


def variant_parity_check(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    variants = _semantic(params, "variant_plan").get("variants") or []
    if len(variants) != len(protocol.arms):
        return {"ok": False, "status": "failed", "error": "variant count mismatch"}
    views = [dict(row.get("subject_view") or {}) for row in variants if isinstance(row, Mapping)]
    differences: set[str] = set()
    for view in views[1:]:
        differences.update(_diff_paths(views[0], view))
    allowed = set(protocol.allowed_arm_differences)
    unexpected = sorted(differences - allowed)
    missing_declared = sorted(allowed - differences)
    valid = not unexpected and not missing_declared
    record = {"valid": valid, "differences": sorted(differences),
              "allowed_differences": sorted(allowed), "unexpected": unexpected,
              "declared_but_unchanged": missing_declared}
    output = _write(ctx, params, "variant_parity.json", record)
    return {"ok": valid, "status": "completed" if valid else "failed",
            "summary": "variant parity passed" if valid else "variant parity failed",
            "error": "arm differences violate protocol" if not valid else "",
            "failure_class": "benchmark_protocol" if not valid else "",
            "evidence_refs": [str(output)], "semantic_output": record}


def variant_submit(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    arm = str(params.get("arm_id") or "")
    protocol, _ = _protocol(ctx, params)
    if arm not in protocol.arms:
        return {"ok": False, "status": "failed", "error": f"arm not in protocol: {arm}"}
    variants = _semantic(params, "variant_plan").get("variants") or []
    variant = next((dict(v) for v in variants if isinstance(v, Mapping) and v.get("arm_id") == arm), None)
    if variant is None:
        return {"ok": False, "status": "failed", "error": f"variant missing: {arm}"}
    request = {
        "flow": protocol.subject_flow, "owner_node": str(params.get("node_id") or ""),
        "arm_id": arm, "context": {
            "benchmark_subject_view": variant["subject_view"],
            "benchmark_arm_id": arm,
            "benchmark_run_id": _run_id(params),
            "benchmark_protocol_id": protocol.protocol_id,
            "evaluation_visibility": "hidden_until_terminal",
        },
    }
    return {"ok": True, "status": "completed", "summary": f"submit {arm} subject flow",
            "semantic_output": {"arm_id": arm, "benchmark_child": request},
            "benchmark_child": request}


def arm_collect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    arm = str(params.get("arm_id") or "")
    submit_node = f"{arm}_submit"
    source = _outputs(params).get(submit_node) or {}
    semantic = source.get("semantic_output") if isinstance(source, Mapping) else {}
    record_path = str((semantic or {}).get("record_path") or "")
    if not record_path or not Path(record_path).is_file():
        return {"ok": False, "status": "failed", "error": f"missing {arm} child record",
                "failure_class": "benchmark_artifact"}
    child = json.loads(Path(record_path).read_text(encoding="utf-8"))
    checkpoints = child.get("checkpoints") if isinstance(child.get("checkpoints"), list) else []
    artifacts = [str(v) for v in child.get("files") or [] if Path(str(v)).is_file()]
    metrics = numeric_metrics(child.get("node_outputs") or {})
    predictions: list[dict[str, Any]] = []
    guardrails: dict[str, bool] = {}
    run_config: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    evidence_documents: list[str] = []
    for raw in artifacts:
        path = Path(raw)
        if path.name not in {"benchmark_evidence.json", "metrics.json", "predictions.json",
                             "predictions.csv"}:
            continue
        try:
            if path.suffix == ".csv":
                parsed: Any = list(csv.DictReader(path.open(encoding="utf-8")))
            else:
                parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        evidence_documents.append(str(path))
        if isinstance(parsed, Mapping):
            for key, value in (parsed.get("metrics") or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    metrics[str(key)] = float(value)
            guardrails.update({str(k): bool(v) for k, v in (parsed.get("guardrails") or {}).items()
                               if isinstance(v, bool)})
            if isinstance(parsed.get("run_config"), Mapping):
                run_config.update(dict(parsed["run_config"]))
            if isinstance(parsed.get("provenance"), Mapping):
                provenance.update(dict(parsed["provenance"]))
            parsed = parsed.get("predictions") or []
        if isinstance(parsed, list):
            for index, row in enumerate(parsed):
                if not isinstance(row, Mapping):
                    continue
                try:
                    predictions.append({
                        "sample_id": str(row.get("sample_id") or row.get("id") or index),
                        "y_true": float(row.get("y_true")),
                        "y_pred": float(row.get("y_pred", row.get("prediction"))),
                    })
                except (TypeError, ValueError):
                    continue
    if predictions:
        metrics.setdefault("rmse", math.sqrt(sum(
            (row["y_true"] - row["y_pred"]) ** 2 for row in predictions) / len(predictions)))
    record = {"arm_id": arm, "child_status": child.get("status"),
              "child_flow_id": child.get("flow_id"), "checkpoints": checkpoints,
              "artifacts": artifacts, "metrics": metrics, "predictions": predictions,
              "guardrails": guardrails, "run_config": run_config, "provenance": provenance,
              "evidence_documents": evidence_documents,
              "record_path": record_path}
    output = _write(ctx, params, f"arms/{arm}/collection.json", record)
    return {"ok": True, "status": "completed", "summary": f"collected {arm}",
            "files": artifacts, "evidence_refs": [record_path, str(output)],
            "metrics": metrics, "semantic_output": record}


def deterministic_evaluate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    arm = str(params.get("arm_id") or "")
    collection = _semantic(params, f"{arm}_collect")
    protocol, _ = _protocol(ctx, params)
    expected = {str(row.get("id")) for row in protocol.checkpoints}
    observed = {str(row.get("checkpoint_id")) for row in collection.get("checkpoints") or []}
    checks = {
        "child_completed": collection.get("child_status") == "completed",
        "checkpoints_complete": expected <= observed,
        "artifacts_present": bool(collection.get("artifacts")),
    }
    record = {"arm_id": arm, "valid": all(checks.values()), "checks": checks,
              "missing_checkpoints": sorted(expected - observed)}
    output = _write(ctx, params, f"arms/{arm}/deterministic_evaluation.json", record)
    return {"ok": True, "status": "completed", "summary": f"evaluated {arm} integrity",
            "evidence_refs": [str(output)], "semantic_output": record,
            "metrics": {"integrity": 1.0 if record["valid"] else 0.0}}


def execution_parity_evaluate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    baseline, candidate = protocol.arms[0], protocol.arms[1]
    left = _semantic(params, f"{baseline}_collect")
    right = _semantic(params, f"{candidate}_collect")
    left_config = dict(left.get("run_config") or {})
    right_config = dict(right.get("run_config") or {})
    differences = _diff_paths(left_config, right_config)
    # Artifact run_config uses a concise features.declared_feature path; the
    # public arm view uses arm_configuration.features.declared_feature.
    allowed = {value.removeprefix("arm_configuration.")
               for value in protocol.allowed_arm_differences}
    unexpected = sorted(differences - allowed)
    required = sorted(allowed - differences)
    b_feature = ((left_config.get("features") or {}).get("declared_feature")
                 if isinstance(left_config.get("features"), Mapping) else None)
    c_feature = ((right_config.get("features") or {}).get("declared_feature")
                 if isinstance(right_config.get("features"), Mapping) else None)
    declared = (_config(params).get("inputs") or {}).get("declared_feature")
    feature_valid = b_feature in {None, ""} and c_feature == declared
    configs_present = bool(left_config) and bool(right_config)
    valid = configs_present and not unexpected and not required and feature_valid
    record = {"valid": valid, "configs_present": configs_present,
              "differences": sorted(differences), "allowed_differences": sorted(allowed),
              "unexpected": unexpected, "declared_but_unchanged": required,
              "baseline_declared_feature": b_feature,
              "candidate_declared_feature": c_feature,
              "expected_declared_feature": declared, "feature_valid": feature_valid}
    output = _write(ctx, params, "execution_parity.json", record)
    return {"ok": True, "status": "completed", "summary": f"execution parity: {valid}",
            "evidence_refs": [str(output)], "semantic_output": record,
            "metrics": {"execution_parity": 1.0 if valid else 0.0}}


def domain_metric_evaluate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    arm = str(params.get("arm_id") or "")
    collection = _semantic(params, f"{arm}_collect")
    protocol, _ = _protocol(ctx, params)
    metric_name = str(protocol.primary_metric.get("name") or "score")
    metrics = dict(collection.get("metrics") or {})
    value = metrics.get(metric_name)
    record = {"arm_id": arm, "metric_name": metric_name, "value": value,
              "direction": protocol.primary_metric.get("direction"),
              "status": "measured" if isinstance(value, (int, float)) else "missing",
              "all_metrics": metrics}
    output = _write(ctx, params, f"arms/{arm}/domain_metrics.json", record)
    return {"ok": True, "status": "completed", "summary": f"{arm} {metric_name}: {value}",
            "evidence_refs": [str(output)], "semantic_output": record,
            "metrics": ({metric_name: float(value)} if isinstance(value, (int, float)) else {})}


def paired_compare(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    baseline, candidate = protocol.arms[0], protocol.arms[1]
    b = _semantic(params, f"{baseline}_metric").get("value")
    c = _semantic(params, f"{candidate}_metric").get("value")
    direction = str(protocol.primary_metric.get("direction") or "lower_is_better")
    measured = isinstance(b, (int, float)) and isinstance(c, (int, float))
    effect = ((float(b) - float(c)) if direction == "lower_is_better" else
              (float(c) - float(b))) if measured else None
    baseline_collection = _semantic(params, f"{baseline}_collect")
    candidate_collection = _semantic(params, f"{candidate}_collect")
    b_rows = {str(v.get("sample_id")): v for v in baseline_collection.get("predictions") or []}
    c_rows = {str(v.get("sample_id")): v for v in candidate_collection.get("predictions") or []}
    matched_ids = sorted(set(b_rows) & set(c_rows))
    targets_match = all(b_rows[i].get("y_true") == c_rows[i].get("y_true") for i in matched_ids)
    paired_valid = not (b_rows or c_rows) or (set(b_rows) == set(c_rows) and targets_match)
    bootstrap_ci = None
    bootstrap_samples = 0
    if matched_ids and set(b_rows) == set(c_rows) and targets_match:
        count = int(protocol.budget.get("bootstrap") or 0)
        # The repetition protocol is frozen before either arm runs.  A run-id
        # derived seed would make identical evidence settle differently across
        # reruns, so use the protocol seed directly.
        rng = random.Random(int(protocol.budget.get("bootstrap_seed") or 0))
        effects = []
        for _ in range(count):
            sampled = [matched_ids[rng.randrange(len(matched_ids))] for _ in matched_ids]
            b_rmse = math.sqrt(sum((b_rows[i]["y_true"] - b_rows[i]["y_pred"]) ** 2
                                   for i in sampled) / len(sampled))
            c_rmse = math.sqrt(sum((c_rows[i]["y_true"] - c_rows[i]["y_pred"]) ** 2
                                   for i in sampled) / len(sampled))
            effects.append((b_rmse - c_rmse) if direction == "lower_is_better"
                           else (c_rmse - b_rmse))
        if effects:
            effects.sort()
            low = effects[max(0, int(0.025 * len(effects)) - 1)]
            high = effects[min(len(effects) - 1, int(0.975 * len(effects)))]
            bootstrap_ci = [low, high]
            bootstrap_samples = len(effects)
    record = {"baseline_arm": baseline, "candidate_arm": candidate,
              "baseline_value": b, "candidate_value": c, "direction": direction,
              "effect": effect, "status": "measured" if measured and paired_valid else
              "invalid" if not paired_valid else "incomplete",
              "paired_sample_count": len(matched_ids), "prediction_sets_match": set(b_rows) == set(c_rows),
              "targets_match": targets_match,
              "bootstrap_samples": bootstrap_samples, "confidence_interval": bootstrap_ci}
    output = _write(ctx, params, "comparison.json", record)
    return {"ok": True, "status": "completed", "summary": f"paired effect: {effect}",
            "evidence_refs": [str(output)], "semantic_output": record,
            "metrics": ({"effect": effect} if effect is not None else {})}


def expectation_compare(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    comparison = _semantic(params, "paired_compare")
    effect = comparison.get("effect")
    minimum = float(protocol.expected_effect.get("minimum") or 0.0)
    ci = comparison.get("confidence_interval")
    excludes = protocol.expected_effect.get("confidence_interval_must_exclude")
    ci_pass = (isinstance(ci, list) and len(ci) == 2 and float(ci[0]) > float(excludes)) \
        if excludes is not None else True
    record = {"effect": effect, "minimum": minimum, "confidence_interval": ci,
              "confidence_interval_pass": ci_pass,
              "met": isinstance(effect, (int, float)) and float(effect) >= minimum and ci_pass,
              "status": "measured" if isinstance(effect, (int, float)) else "incomplete"}
    output = _write(ctx, params, "expectation.json", record)
    return {"ok": True, "status": "completed", "summary": f"expected effect met: {record['met']}",
            "evidence_refs": [str(output)], "semantic_output": record}


def guardrail_evaluate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    collections = [_semantic(params, f"{arm}_collect") for arm in protocol.arms]
    integrity = [_semantic(params, f"{arm}_integrity") for arm in protocol.arms]
    attestations = _config(params).get("guardrail_results") or {}
    rows = []
    for guardrail in protocol.guardrails:
        identifier = str(guardrail.get("id") or "")
        values = [(row.get("guardrails") or {}).get(identifier) for row in collections]
        derived_evidence = None
        if identifier == "artifacts_complete":
            values = [row.get("valid") for row in integrity]
        elif identifier == "secondary_metrics_not_materially_worse":
            comparisons = guardrail.get("comparisons") or []
            baseline_metrics = collections[0].get("metrics") or {}
            candidate_metrics = collections[1].get("metrics") or {}
            checks = []
            for comparison in comparisons:
                metric = str(comparison.get("metric") or "")
                baseline_value = baseline_metrics.get(metric)
                candidate_value = candidate_metrics.get(metric)
                tolerance = float(comparison.get("absolute_tolerance") or 0.0)
                direction = str(comparison.get("direction") or "lower_is_better")
                measured = isinstance(baseline_value, (int, float)) and isinstance(
                    candidate_value, (int, float))
                passed = (float(candidate_value) <= float(baseline_value) + tolerance
                          if measured and direction == "lower_is_better" else
                          float(candidate_value) >= float(baseline_value) - tolerance
                          if measured and direction == "higher_is_better" else None)
                checks.append({"metric": metric, "baseline": baseline_value,
                               "candidate": candidate_value, "direction": direction,
                               "absolute_tolerance": tolerance, "passed": passed})
            derived = bool(checks) and all(row["passed"] is True for row in checks)
            values = [derived] if checks and all(row["passed"] is not None for row in checks) else []
            derived_evidence = {"method": "parent_metric_comparison", "checks": checks}
        status = ("pass" if values and all(value is True for value in values) else
                  "fail" if any(value is False for value in values) else "unknown")
        rows.append({"id": identifier, "hard": bool(guardrail.get("hard")), "status": status,
                     "arm_evidence": values,
                     "derived_evidence": derived_evidence,
                     "operator_attestation": attestations.get(identifier),
                     "attestation_is_authoritative": False})
    hard_pass = all(row["status"] == "pass" for row in rows if row["hard"])
    record = {"guardrails": rows, "hard_pass": hard_pass,
              "unknown_hard": [r["id"] for r in rows if r["hard"] and r["status"] == "unknown"]}
    output = _write(ctx, params, "guardrails.json", record)
    return {"ok": True, "status": "completed", "summary": f"hard guardrails pass: {hard_pass}",
            "evidence_refs": [str(output)], "semantic_output": record}


def jev_evaluate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    mode = str(protocol.evaluators.get("jev") or "disabled")
    record = {"mode": mode, "status": "abstained", "authoritative": False,
              "reason": "typed judge is advisory; deterministic settlement remains authoritative"}
    if mode != "disabled":
        try:
            from partner.core_v1.models import DecisionState
            from partner.core_v1.service import jev_client
            state = DecisionState.create(
                flow_id=str(params.get("flow_id") or ""), project_id=str(params.get("project_id") or ""),
                instance_id=str(params.get("instance_id") or ""), domain="benchmark",
                objective="Review paired benchmark result and guardrails",
                facts={"comparison": _semantic(params, "paired_compare"),
                       "expectation": _semantic(params, "expectation_compare"),
                       "guardrails": _semantic(params, "guardrail_evaluate")},
                numeric_features={"effect": float(_semantic(params, "paired_compare").get("effect") or 0.0)},
                evidence_refs=[])
            judgment = jev_client(ctx.workspace).evaluate(state)
            record = {"mode": mode, **judgment.to_dict(), "authoritative": False}
        except Exception as exc:
            record["reason"] = f"{type(exc).__name__}: {exc}"
    output = _write(ctx, params, "judges/jev.json", record)
    return {"ok": True, "status": "completed", "summary": f"benchmark Jev {record['status']}",
            "evidence_refs": [str(output)], "semantic_output": record}


def llm_judge(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    mode = str(protocol.evaluators.get("llm_judge") or "disabled")
    config = _config(params)
    record: dict[str, Any] = {"mode": mode, "status": "abstained", "authoritative": False,
                              "reason": "external LLM judge not enabled for this run"}
    if mode != "disabled" and config.get("allow_external_judges") is True:
        from ._llm import call_model, json_object
        def blind_view(arm: str) -> dict[str, Any]:
            source = _semantic(params, f"{arm}_collect")
            return {"child_status": source.get("child_status"),
                    "checkpoints": source.get("checkpoints") or [],
                    "metrics": source.get("metrics") or {},
                    "guardrails": source.get("guardrails") or {}}
        original = [blind_view(protocol.arms[0]), blind_view(protocol.arms[1])]
        passes = []
        total_usage: dict[str, float] = {}
        for order in ((0, 1), (1, 0)):
            blind = {"arm_a": original[order[0]], "arm_b": original[order[1]],
                     "rubric": {"faithful_to_evidence": True, "scientific_reasoning": True}}
            raw, usage = call_model(ctx, purpose="benchmark_blind_judge", prompt=(
                "你是独立盲评员。只依据给出的检查点和证据评价，不猜测缺失事实。"
                "输出JSON: preferred_arm(a/b/tie/unknown), scores, evidence_refs, reason。\n" +
                json.dumps(blind, ensure_ascii=False)[:24000]))
            parsed = json_object(raw)
            preferred = str(parsed.get("preferred_arm") or "unknown").lower()
            canonical_preference = ({"a": order[0], "b": order[1]}.get(preferred)
                                    if preferred in {"a", "b"} else preferred)
            passes.append({"order": list(order), "preferred": preferred,
                           "canonical_preference": canonical_preference,
                           "scores": dict(parsed.get("scores") or {}),
                           "evidence_refs": list(parsed.get("evidence_refs") or []),
                           "reason": str(parsed.get("reason") or ""),
                           "raw_hash": digest(raw)})
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    total_usage[key] = total_usage.get(key, 0) + value
        agreement = passes[0]["canonical_preference"] == passes[1]["canonical_preference"]
        record = {"mode": mode, "status": "completed" if agreement else "disagreement",
                  "agreement": agreement, "passes": passes, "usage": total_usage,
                  "authoritative": False,
                  "reason": "order-swapped blind judgments agree" if agreement else
                  "order-swapped blind judgments disagree; abstain"}
    output = _write(ctx, params, "judges/llm.json", record)
    return {"ok": True, "status": "completed", "summary": f"LLM judge {record['status']}",
            "evidence_refs": [str(output)], "semantic_output": record,
            "token_usage": dict(record.get("usage") or {})}


def aggregate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    integrity = {arm: _semantic(params, f"{arm}_integrity") for arm in protocol.arms}
    record = {"validity": all(v.get("valid") is True for v in integrity.values()),
              "integrity": integrity, "comparison": _semantic(params, "paired_compare"),
              "expectation": _semantic(params, "expectation_compare"),
              "guardrails": _semantic(params, "guardrail_evaluate"),
              "execution_parity": _semantic(params, "execution_parity"),
              "jev": _semantic(params, "jev_evaluate"),
              "llm_judge": _semantic(params, "llm_judge")}
    record["validity"] = bool(record["validity"] and record["guardrails"].get("hard_pass"))
    record["validity"] = bool(record["validity"] and
                              record["comparison"].get("status") != "invalid")
    record["validity"] = bool(record["validity"] and
                              record["execution_parity"].get("valid") is True)
    guardrail_rows = record["guardrails"].get("guardrails") or []
    scores = {
        "integrity": (sum(1.0 for value in integrity.values() if value.get("valid")) /
                      max(1, len(integrity))),
        "expected_effect": 1.0 if record["expectation"].get("met") else 0.0,
        "guardrails": (sum(1.0 for row in guardrail_rows if row.get("status") == "pass") /
                       max(1, len(guardrail_rows))),
        "execution_parity": 1.0 if record["execution_parity"].get("valid") else 0.0,
    }
    record["scores"] = scores
    record["overall_score"] = (sum(scores.values()) / len(scores)) if record["validity"] else None
    output = _write(ctx, params, "aggregate.json", record)
    return {"ok": True, "status": "completed", "summary": f"benchmark validity: {record['validity']}",
            "evidence_refs": [str(output)], "semantic_output": record}


def benchmark_settlement(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    aggregate_row = _semantic(params, "aggregate")
    expectation = aggregate_row.get("expectation") or {}
    comparison = aggregate_row.get("comparison") or {}
    if not aggregate_row.get("validity"):
        decision = "invalid"
    elif comparison.get("status") != "measured":
        decision = "inconclusive"
    elif expectation.get("met"):
        decision = "confirmed"
    elif isinstance(comparison.get("effect"), (int, float)) and comparison["effect"] > 0:
        decision = "insufficient_effect"
    else:
        decision = "falsified"
    record = {"decision": decision, "valid": bool(aggregate_row.get("validity")),
              "effect": comparison.get("effect"), "expected_met": expectation.get("met"),
              "settled_at": _now(), "authoritative_source": "deterministic_evaluators"}
    output = _write(ctx, params, "settlement.json", record)
    return {"ok": True, "status": "completed", "summary": f"benchmark {decision}",
            "evidence_refs": [str(output)], "semantic_output": record}


def route_next(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    settlement = _semantic(params, "benchmark_settlement")
    decision = str(settlement.get("decision") or "inconclusive")
    route = {"confirmed": "continue_project", "falsified": "revise_scientific_hypothesis",
             "insufficient_effect": "revise_scientific_hypothesis",
             "inconclusive": "waiting", "invalid": "diagnose_invalid_run"}.get(decision, "waiting")
    record = {"primary_route": route, "reason": f"benchmark settlement={decision}",
              "automatic_self_evolution": False,
              "self_evolution_requires": "independently reproduced Partner mechanism defect"}
    return {"ok": True, "status": "completed", "summary": record["reason"],
            "semantic_output": record}


def report_compose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    protocol, _ = _protocol(ctx, params)
    settlement = _semantic(params, "benchmark_settlement")
    aggregate_row = _semantic(params, "aggregate")
    report = {
        "run_id": _run_id(params), "protocol_id": protocol.protocol_id,
        "settlement": settlement, "comparison": aggregate_row.get("comparison"),
        "expectation": aggregate_row.get("expectation"),
        "guardrails": aggregate_row.get("guardrails"),
        "integrity": aggregate_row.get("integrity"),
        "advisory_judges": {"jev": aggregate_row.get("jev"),
                             "llm": aggregate_row.get("llm_judge")},
        "route": _semantic(params, "benchmark_route"),
    }
    json_path = _write(ctx, params, "report.json", report)
    lines = [f"# Benchmark {protocol.protocol_id}", "",
             f"- Run: `{_run_id(params)}`", f"- Settlement: `{settlement.get('decision')}`",
             f"- Effect: `{settlement.get('effect')}`", f"- Valid: `{settlement.get('valid')}`",
             "", "完整结构化证据见 `report.json`。"]
    md_path = BenchmarkRunStore(ctx.workspace, _run_id(params)).directory / "report.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    message = (f"Benchmark {protocol.protocol_id} 已结算：{settlement.get('decision')}；"
               f"effect={settlement.get('effect')}；报告：{md_path}")
    return {"ok": True, "status": "completed", "summary": message,
            "human_message": message, "message": message,
            "files": [str(json_path), str(md_path)], "evidence_refs": [str(json_path), str(md_path)],
            "semantic_output": {"message": message, "report_path": str(md_path), **report}}


def report_verify(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    report = _semantic(params, "benchmark_report")
    path = Path(str(report.get("report_path") or ""))
    valid = path.is_file() and path.stat().st_size > 32 and bool(report.get("settlement"))
    return {"ok": valid, "status": "completed" if valid else "failed",
            "summary": "benchmark report verified" if valid else "benchmark report invalid",
            "error": "report missing or incomplete" if not valid else "",
            "evidence_refs": [str(path)] if path.is_file() else [],
            "semantic_output": {"verified": valid, "report_path": str(path),
                                "message": report.get("message")}}


def run_close(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    store = BenchmarkRunStore(ctx.workspace, _run_id(params))
    manifest = dict(store.read("manifest.json", {}))
    settlement = store.read("settlement.json", {})
    manifest.update({"state": "completed", "completed_at": _now(),
                     "settlement": settlement.get("decision"),
                     "valid": settlement.get("valid")})
    path = store.write("manifest.json", manifest)
    return {"ok": True, "status": "completed", "summary": "benchmark run closed",
            "evidence_refs": [str(path)], "semantic_output": manifest}


def checkpoint_capture(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    checkpoint_id = str(params.get("checkpoint_id") or "")
    if not checkpoint_id:
        return {"ok": False, "status": "failed", "error": "checkpoint_id required"}
    arm = str(params.get("benchmark_arm_id") or (params.get("run_context") or {}).get(
        "benchmark_arm_id") or "subject")
    source = params.get("previous") if isinstance(params.get("previous"), Mapping) else {}
    artifacts = [str(v) for v in source.get("files") or source.get("evidence_refs") or []]
    artifact_hashes = {}
    for raw in artifacts:
        path = Path(raw)
        if path.is_file() and path.stat().st_size <= 64 * 1024 * 1024:
            try:
                artifact_hashes[str(path)] = "sha256:" + __import__("hashlib").sha256(path.read_bytes()).hexdigest()
            except OSError:
                pass
    record = {"schema_version": 1, "checkpoint_id": checkpoint_id,
              "benchmark_run_id": _run_id(params), "arm_id": arm,
              "flow_id": str(params.get("flow_id") or ""),
              "source_node": str(params.get("source_node") or ""),
              "source_event_ids": [str((params.get("flow_event_ids") or {}).get(
                  str(params.get("source_node") or "")) or "")],
              "state_hash": digest(source), "artifact_hashes": artifact_hashes,
              "evidence_refs": artifacts, "captured_at": _now()}
    path = BenchmarkRunStore(ctx.workspace, _run_id(params)).append_checkpoint(record)
    return {"ok": True, "status": "completed", "summary": f"captured {checkpoint_id}",
            "evidence_refs": [str(path), *artifacts], "semantic_output": record}


DEFINITIONS = [
    EventDefinition("benchmark.signal_validate", "benchmark", "验证结构化 Benchmark 标志", signal_validate),
    EventDefinition("benchmark.protocol_resolve", "benchmark", "解析并固定 Benchmark 协议", protocol_resolve, reads_existing_artifact=True),
    EventDefinition("benchmark.environment_preflight", "benchmark", "检查输入、Event 与 Flow 完整性", environment_preflight, reads_existing_artifact=True),
    EventDefinition("benchmark.run_freeze", "benchmark", "冻结运行清单、版本、预算与输入", run_freeze, produces_artifact=True),
    EventDefinition("benchmark.variant_plan", "benchmark", "生成预算对称且隔离评价的实验组", variant_plan, produces_artifact=True),
    EventDefinition("benchmark.variant_parity_check", "benchmark", "确认实验组只含预声明差异", variant_parity_check, produces_artifact=True),
    EventDefinition("benchmark.variant_submit", "benchmark", "请求运行时启动被测子 Flow", variant_submit),
    EventDefinition("benchmark.arm_collect", "benchmark", "收集子 Flow 检查点和产物", arm_collect, reads_existing_artifact=True, produces_artifact=True),
    EventDefinition("benchmark.deterministic_evaluate", "benchmark", "确定性检查运行完整性", deterministic_evaluate, reads_existing_artifact=True, produces_artifact=True),
    EventDefinition("benchmark.execution_parity_evaluate", "benchmark", "核验两组实际执行仅含预声明差异", execution_parity_evaluate, reads_existing_artifact=True, produces_artifact=True),
    EventDefinition("benchmark.domain_metric_evaluate", "benchmark", "运行冻结的领域指标评价", domain_metric_evaluate, reads_existing_artifact=True, produces_artifact=True),
    EventDefinition("benchmark.paired_compare", "benchmark", "配对比较 baseline 与 candidate", paired_compare, produces_artifact=True),
    EventDefinition("benchmark.expectation_compare", "benchmark", "比较实际效果与冻结预期", expectation_compare, produces_artifact=True),
    EventDefinition("benchmark.guardrail_evaluate", "benchmark", "独立检查硬性与软性 guardrail", guardrail_evaluate, produces_artifact=True),
    EventDefinition("benchmark.jev_evaluate", "benchmark", "JEV 类型化影子评价", jev_evaluate, execution_method="external", external_call=True, produces_artifact=True),
    EventDefinition("benchmark.llm_judge", "benchmark", "盲化 LLM 语义评价", llm_judge, execution_method="llm", external_call=True, produces_artifact=True),
    EventDefinition("benchmark.aggregate", "benchmark", "聚合硬门槛、数值和顾问评价", aggregate, produces_artifact=True),
    EventDefinition("benchmark.settlement", "benchmark", "依据冻结协议作出 Benchmark 结算", benchmark_settlement, produces_artifact=True),
    EventDefinition("benchmark.route_next", "benchmark", "依据结算选择后续链路", route_next),
    EventDefinition("benchmark.report_compose", "benchmark", "生成结构化 Benchmark 报告", report_compose, produces_artifact=True),
    EventDefinition("benchmark.report_verify", "benchmark", "核验报告与结论证据", report_verify, reads_existing_artifact=True),
    EventDefinition("benchmark.run_close", "benchmark", "关闭不可变 Benchmark 运行", run_close, produces_artifact=True),
    EventDefinition("checkpoint.capture", "checkpoint", "冻结一个不含评价反馈的运行时检查点", checkpoint_capture, produces_artifact=True),
]
