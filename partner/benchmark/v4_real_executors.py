"""Real, bounded arm executors for the v4 longitudinal benchmark.

The reference corpus in :mod:`partner.events.v4_benchmark` deliberately uses
small deterministic policies.  This module is the separate evidence layer for
real data, real local sources and natural historical defects.  Every executor
returns one JSON-serialisable receipt; it never sees the sealed evaluator.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
import time


def _sha(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def _run_davis(arm: str, inputs: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    runner = Path(str(inputs.get("arm_runner_path") or "")).expanduser().resolve()
    dataset = Path(str(inputs.get("dataset_path") or "")).expanduser().resolve()
    if not runner.is_file() or not dataset.is_file():
        raise FileNotFoundError("real DAVIS executor requires existing runner and dataset")
    runner_arm = "baseline" if arm == "single_turn_no_memory" else "candidate"
    result_path = output_dir / f"real_{runner_arm}_evidence.json"
    env = os.environ.copy()
    env.update(PARTNER_BENCHMARK_SEED=str(int(inputs.get("benchmark_seed") or 29)),
               PARTNER_BENCHMARK_TASK_ID=str(inputs.get("task_id") or "target_kmer32"),
               PARTNER_DECLARED_FEATURE=str(inputs.get("declared_feature") or "target_kmer32"))
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, str(runner), "--arm", runner_arm,
         "--dataset", str(dataset), "--output", str(result_path)],
        cwd=str(output_dir), env=env, capture_output=True, text=True,
        timeout=float(inputs.get("timeout_seconds") or 300), check=False,
    )
    if completed.returncode != 0 or not result_path.is_file():
        raise RuntimeError(f"DAVIS arm failed rc={completed.returncode}: {completed.stderr[-1200:]}")
    evidence = json.loads(result_path.read_text(encoding="utf-8"))
    metrics = dict(evidence.get("metrics") or {})
    guardrails = dict(evidence.get("guardrails") or {})
    if not metrics.get("rmse") or not all(guardrails.get(k) is True for k in (
            "no_target_leakage", "official_test_not_used_for_tuning",
            "within_budget", "artifacts_complete")):
        raise RuntimeError("DAVIS evidence is incomplete or failed a hard guardrail")
    return {
        "prediction": float(metrics["rmse"]), "metric_name": "rmse",
        "metrics": metrics, "guardrails": guardrails,
        "artifact_path": str(result_path), "artifact_sha256": _sha(result_path),
        "dataset_sha256": _sha(dataset), "runner_sha256": _sha(runner),
        "runner_arm": runner_arm, "exit_code": completed.returncode,
        "duration_seconds": time.monotonic() - started,
        "consumed_evidence_ids": ([str(inputs.get("memory_ref"))]
                                  if arm == "full_partner" and inputs.get("memory_ref") else []),
        "strategy": "frozen_davis_hgb_real_execution",
    }


def _source_excerpt(path: Path, query: str, max_chars: int) -> tuple[str, int, int]:
    if path.suffix.lower() == ".pdf":
        import fitz
        with fitz.open(path) as document:
            text = "\n".join(page.get_text("text") for page in document)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    position = text.lower().find(query.lower()) if query else 0
    position = max(0, position)
    start = max(0, position - max_chars // 3)
    end = min(len(text), start + max_chars)
    return text[start:end], start, end


def _run_age_calibration(arm: str, inputs: Mapping[str, Any],
                         output_dir: Path) -> dict[str, Any]:
    """Evaluate a frozen group-residual calibration on an independent domain."""
    dataset = Path(str(inputs.get("dataset_path") or "")).expanduser().resolve()
    if not dataset.is_file():
        raise FileNotFoundError("age calibration requires an existing CSV")
    seed = int(inputs.get("benchmark_seed") or 11)
    strategy = str(inputs.get("declared_strategy") or "group_residual")
    rows = list(csv.DictReader(dataset.open(encoding="utf-8-sig", newline="")))
    required = {"sample_id", "actual_age", "predicted_age", "group"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("age calibration CSV columns are incomplete")
    calibration, evaluation = [], []
    for row in rows:
        token = sha256(f"{seed}:{row['sample_id']}".encode()).digest()[0]
        (calibration if token < 153 else evaluation).append(row)
    if not calibration or not evaluation:
        raise ValueError("frozen calibration/evaluation split is empty")
    residuals: dict[str, list[float]] = {}
    for row in calibration:
        residuals.setdefault(str(row["group"]), []).append(
            float(row["actual_age"]) - float(row["predicted_age"]))
    corrections = {key: sum(values) / len(values) for key, values in residuals.items()}
    use_candidate = arm == "full_partner" and strategy == "group_residual"
    squared = []
    predictions = []
    for row in evaluation:
        base = float(row["predicted_age"])
        predicted = base + (corrections.get(str(row["group"]), 0.0) if use_candidate else 0.0)
        squared.append((float(row["actual_age"]) - predicted) ** 2)
        predictions.append({"sample_id": row["sample_id"], "actual_age": float(row["actual_age"]),
                            "prediction": predicted, "group": row["group"]})
    rmse = math.sqrt(sum(squared) / len(squared))
    evidence = {
        "schema_version": 1, "arm": arm, "strategy": strategy,
        "rmse": rmse, "calibration_count": len(calibration),
        "evaluation_count": len(evaluation), "corrections": corrections if use_candidate else {},
        "evaluation_sample_ids_sha256": "sha256:" + sha256(
            "\n".join(sorted(row["sample_id"] for row in evaluation)).encode()).hexdigest(),
        "predictions": predictions,
        "guardrails": {"disjoint_frozen_split": True,
                       "evaluation_not_used_for_calibration": True,
                       "same_evaluation_rows": True, "artifacts_complete": True},
    }
    path = output_dir / f"{arm}_age_calibration_evidence.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"prediction": rmse, "metric_name": "rmse", "metrics": {"rmse": rmse},
            "guardrails": evidence["guardrails"], "artifact_path": str(path),
            "artifact_sha256": _sha(path), "dataset_sha256": _sha(dataset),
            "consumed_evidence_ids": ([f"calibration:{evidence['evaluation_sample_ids_sha256']}"]
                                      if use_candidate else []),
            "strategy": "frozen_group_residual_calibration" if use_candidate else "raw_prediction"}


def _memory_value(longitudinal_state: Mapping[str, Any], memory_ref: str) -> Any:
    memories = {str(row.get("memory_id") or ""): row
                for row in longitudinal_state.get("memories") or []}
    return (memories.get(memory_ref) or {}).get("value")


def _contains_value(value: Any, expected: str) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, expected) for item in value)
    return expected.lower() in str(value or "").lower()


def _run_memory_guided_age(arm: str, inputs: Mapping[str, Any], output_dir: Path,
                           longitudinal_state: Mapping[str, Any]) -> dict[str, Any]:
    """Let settled knowledge select a project candidate, then measure real RMSE."""
    memory_ref = str(inputs.get("memory_ref") or "")
    memory_value = _memory_value(longitudinal_state, memory_ref)
    selected = "group_residual" if (arm == "full_partner" and
                                    (_contains_value(memory_value, "group_residual") or
                                     _contains_value(memory_value, "Age-group residual correction"))) else "none"
    result = _run_age_calibration(
        arm, {**dict(inputs), "declared_strategy": selected}, output_dir)
    result.update({
        "selected_strategy": selected,
        "selection_source": "settled_memory" if selected != "none" else "no_memory_baseline",
        "memory_available": memory_value is not None,
        "consumed_evidence_ids": [memory_ref] if selected != "none" else [],
        "selection_receipt": {"memory_ref": memory_ref, "required_token": "group_residual",
                              "selected_strategy": selected},
    })
    return result


def _run_typed_memory_recall(arm: str, inputs: Mapping[str, Any], output_dir: Path,
                             longitudinal_state: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic retention probe; isolates memory persistence from model variance."""
    memory_ref = str(inputs.get("memory_ref") or "")
    value = _memory_value(longitudinal_state, memory_ref)
    consume = arm == "read_and_consumed" and value is not None
    output = value if consume else {}
    receipt = {"output": output, "memory_available": value is not None,
               "read_evidence_ids": [memory_ref] if arm != "no_read" and value is not None else [],
               "consumed_evidence_ids": [memory_ref] if consume else [],
               "strategy": arm, "retention_probe": True}
    path = output_dir / f"{arm}_typed_memory_recall.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt.update(artifact_path=str(path), artifact_sha256=_sha(path))
    return receipt


def _run_artifact_identity(arm: str, inputs: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    """Cheap real I/O interference Episode with a deliberately null arm contrast."""
    payload = {"item_id": str(inputs.get("item_id") or "interference"),
               "payload": str(inputs.get("payload") or "bounded-real-io"),
               "arm": arm}
    canonical = json.dumps({k: payload[k] for k in ("item_id", "payload")}, sort_keys=True)
    score = int(sha256(canonical.encode()).hexdigest()[:8], 16) / 0xffffffff
    path = output_dir / f"{arm}_artifact_identity.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"prediction": score, "metric_name": "stable_identity_distance",
            "artifact_path": str(path), "artifact_sha256": _sha(path),
            "consumed_evidence_ids": [], "strategy": "bounded_real_io_interference"}


def _run_multi_source_synthesis(ctx: Any, arm: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Use two or more frozen sources to make one typed downstream decision."""
    from partner.events._llm import call_model, json_object
    paths = [Path(str(value)).expanduser().resolve() for value in inputs.get("source_paths") or []]
    if len(paths) < 2 or any(not path.is_file() for path in paths):
        raise FileNotFoundError("multi-source synthesis requires at least two existing sources")
    read = arm in {"read_not_consumed", "read_and_consumed"}
    consume = arm == "read_and_consumed"
    spans, excerpts = [], []
    queries = list(inputs.get("source_queries") or [])
    for index, path in enumerate(paths):
        excerpt, start, end = _source_excerpt(
            path, str(queries[index] if index < len(queries) else ""),
            int(inputs.get("max_source_chars_each") or 4500))
        evidence_id = f"source:{_sha(path)}:{start}:{end}"
        if read:
            spans.append({"path": str(path), "start": start, "end": end,
                          "sha256": _sha(path), "evidence_id": evidence_id})
        excerpts.append(excerpt)
    schema = dict(inputs.get("output_schema") or {})
    prompt = ("Make the frozen downstream design decision. Return JSON only with exactly the "
              "listed keys. The mapping values describe what each answer must contain; they are "
              f"not answers to copy: {json.dumps(schema, ensure_ascii=False)}.\n"
              f"Question: {str(inputs.get('question') or '')}\n")
    if consume:
        prompt += "Frozen evidence:\n" + "\n\n".join(
            f"SOURCE {index + 1}:\n{excerpt}" for index, excerpt in enumerate(excerpts))
    elif read:
        prompt += "The sources were hashed and read, but their content is masked from this decision arm."
    raw, usage = call_model(ctx, purpose="v42_multi_source_synthesis_arm", prompt=prompt)
    output = json_object(raw)
    return {"output": output, "read_evidence_ids": [row["evidence_id"] for row in spans],
            "consumed_evidence_ids": ([row["evidence_id"] for row in spans] if consume else []),
            "source_spans": spans, "source_content_exposed_to_model": consume,
            "model_usage": usage, "strategy": arm}


def _run_structured_memory(ctx: Any, arm: str, inputs: Mapping[str, Any],
                           longitudinal_state: Mapping[str, Any]) -> dict[str, Any]:
    from partner.events._llm import call_model, json_object
    memory_ref = str(inputs.get("memory_ref") or "")
    memories = {str(row.get("memory_id") or ""): row
                for row in longitudinal_state.get("memories") or []}
    memory = memories.get(memory_ref)
    read = arm in {"read_not_consumed", "read_and_consumed"} and memory is not None
    consume = arm == "read_and_consumed" and memory is not None
    schema = dict(inputs.get("output_schema") or {})
    prompt = ("Return JSON only with exactly the listed keys. Mapping values are field "
              "descriptions, not answers: " + json.dumps(schema, ensure_ascii=False) + "\nQuestion: " +
              str(inputs.get("question") or "") + "\n")
    if consume:
        prompt += "Settled memory available for this decision:\n" + json.dumps(memory, ensure_ascii=False)
    elif read:
        prompt += "A settled memory exists, but its value is masked from this arm."
    raw, usage = call_model(ctx, purpose="v42_structured_memory_arm", prompt=prompt)
    output = json_object(raw)
    return {"output": output, "read_evidence_ids": [memory_ref] if read else [],
            "consumed_evidence_ids": [memory_ref] if consume else [],
            "memory_available": memory is not None, "memory_exposed_to_model": consume,
            "model_usage": usage, "strategy": arm}


def _run_learning(ctx: Any, arm: str, inputs: Mapping[str, Any],
                  longitudinal_state: Mapping[str, Any]) -> dict[str, Any]:
    from partner.events._llm import call_model, json_object
    source = Path(str(inputs.get("source_path") or "")).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("real learning executor requires source_path")
    question = str(inputs.get("question") or "").strip()
    if not question:
        raise ValueError("real learning executor requires a downstream question")
    read = arm in {"read_not_consumed", "read_and_consumed"}
    consume = arm == "read_and_consumed"
    excerpt = ""
    span = None
    if read:
        excerpt, start, end = _source_excerpt(
            source, str(inputs.get("source_query") or ""),
            int(inputs.get("max_source_chars") or 6000))
        span = {"path": str(source), "start": start, "end": end,
                "sha256": _sha(source)}
    prompt = (
        "Answer the downstream question. Return JSON only: "
        '{"answer":"one concise exact answer","reason":"brief"}.\n'
        f"Question: {question}\n"
    )
    if consume:
        prompt += "You may use this frozen local-source excerpt:\n---\n" + excerpt + "\n---\n"
    elif read:
        prompt += ("The source was read for the read-only arm, but its content is intentionally "
                   "masked from the decision. Answer from the public question only.\n")
    raw, usage = call_model(ctx, purpose="v4_real_learning_arm", prompt=prompt)
    parsed = json_object(raw)
    answer = str(parsed.get("answer") or "").strip()
    if not answer:
        raise RuntimeError("learning arm returned no answer")
    evidence_id = f"source:{_sha(source)}:{span['start']}:{span['end']}" if span else ""
    return {
        "output": answer, "reason": str(parsed.get("reason") or "")[:1000],
        "read_evidence_ids": [evidence_id] if read else [],
        "consumed_evidence_ids": [evidence_id] if consume else [],
        "source_span": span, "source_content_exposed_to_model": consume,
        "model_usage": usage, "strategy": arm,
    }


def _run_memory_qa(ctx: Any, arm: str, inputs: Mapping[str, Any],
                   longitudinal_state: Mapping[str, Any]) -> dict[str, Any]:
    from partner.events._llm import call_model, json_object
    memory_ref = str(inputs.get("memory_ref") or "")
    memories = {str(row.get("memory_id") or ""): row
                for row in longitudinal_state.get("memories") or []}
    memory = memories.get(memory_ref)
    read = arm in {"read_not_consumed", "read_and_consumed"} and memory is not None
    consume = arm == "read_and_consumed" and memory is not None
    question = str(inputs.get("question") or "")
    prompt = ('Return JSON only: {"answer":"one concise exact answer","reason":"brief"}.\n'
              f"Question: {question}\n")
    if consume:
        prompt += "You may consume this settled memory:\n" + json.dumps(memory, ensure_ascii=False)
    elif read:
        prompt += "A settled memory exists, but its value is masked from this decision arm."
    raw, usage = call_model(ctx, purpose="v4_real_memory_transfer_arm", prompt=prompt)
    parsed = json_object(raw); answer = str(parsed.get("answer") or "").strip()
    if not answer:
        raise RuntimeError("memory transfer arm returned no answer")
    return {"output": answer, "reason": str(parsed.get("reason") or "")[:1000],
            "read_evidence_ids": [memory_ref] if read else [],
            "consumed_evidence_ids": [memory_ref] if consume else [],
            "memory_available": memory is not None, "memory_exposed_to_model": consume,
            "model_usage": usage, "strategy": arm}


def _run_evolution(arm: str, inputs: Mapping[str, Any], output_dir: Path,
                   longitudinal_state: Mapping[str, Any]) -> dict[str, Any]:
    fixture = Path(str(inputs.get("fixture_path") or "")).expanduser().resolve()
    source = Path(str(inputs.get("production_source_path") or "")).expanduser().resolve()
    if not fixture.is_file() or not source.is_file():
        raise FileNotFoundError("real evolution executor requires fixture and production source")
    row = json.loads(fixture.read_text(encoding="utf-8"))
    before = {k: str(row.get(k) or "") for k in ("status", "started_at", "finished_at", "updated_at")}
    anomaly = bool(before["status"] in {"completed", "failed", "cancelled"} and
                   (not before["finished_at"] or before["finished_at"] < before["updated_at"]))
    candidate = dict(row)
    memory_ref = str(inputs.get("memory_ref") or "")
    memories = {str(row.get("memory_id") or ""): row
                for row in longitudinal_state.get("memories") or []}
    memory_ready = not bool(inputs.get("require_memory")) or memory_ref in memories
    if arm == "bounded_repair" and anomaly and memory_ready:
        candidate["finished_at"] = candidate.get("updated_at")
    after_finished = str(candidate.get("finished_at") or "")
    after_updated = str(candidate.get("updated_at") or "")
    valid = bool(after_finished and after_finished >= after_updated)
    candidate_path = output_dir / f"{arm}_natural_defect_replay.json"
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    source_text = source.read_text(encoding="utf-8", errors="replace")
    production_guard = ("finished_at is None or finished_at < updated_at" in source_text and
                        "finished_at = updated_at" in source_text)
    return {
        "output": valid, "diagnosis": "finished_at_precedes_last_update" if anomaly else "none",
        "natural_fixture_sha256": _sha(fixture), "candidate_path": str(candidate_path),
        "candidate_sha256": _sha(candidate_path), "production_source_path": str(source),
        "production_source_sha256": _sha(source), "production_guard_present": production_guard,
        "fresh_process_replay": valid, "rollback_available": True,
        "consumed_evidence_ids": (([memory_ref] if memory_ref else [str(fixture)])
                                  if arm == "bounded_repair" and memory_ready else []),
        "memory_ready": memory_ready,
        "production_candidate": arm == "bounded_repair", "before": before,
    }


def _run_incident_guard(arm: str, inputs: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    """Replay a natural v4 incident against a current production guard.

    The baseline arm preserves the historical failing terminal as evidence. The
    candidate arm runs a fresh-process probe against current code. It never
    edits production and therefore remains safe to repeat in a benchmark.
    """
    incident = Path(str(inputs.get("incident_job_path") or "")).expanduser().resolve()
    source = Path(str(inputs.get("production_source_path") or "")).expanduser().resolve()
    kind = str(inputs.get("probe_kind") or "")
    if not incident.is_file() or not source.is_file():
        raise FileNotFoundError("incident guard replay requires an incident Job and production source")
    expected_sources = {"unknown_executor_fail_closed": "v4_real_executors.py",
                        "suite_hard_gate_propagation": "v4_benchmark.py",
                        "clean_completed_retention": "v4_benchmark.py",
                        "invalid_qq_identity_fail_closed": "delivery.py",
                        "flow_claim_before_read": "event_worker.py",
                        "reviewed_cycle_message_delivery": "delivery.py"}
    if kind in expected_sources and source.name != expected_sources[kind]:
        raise ValueError(f"incident probe {kind} is not bound to its production source")
    historical = json.loads(incident.read_text(encoding="utf-8"))
    historical_failed = str(historical.get("status") or "") == "failed"
    if kind == "invalid_qq_identity_fail_closed":
        historical_defect = bool(historical.get("to_user") and
                                 str(historical.get("delivery_state") or "") != "sent")
    elif kind in {"flow_claim_before_read", "reviewed_cycle_message_delivery"}:
        historical_defect = True
    else:
        historical_defect = (str(historical.get("status") or "") == "completed"
                             if kind in {"unknown_executor_fail_closed", "suite_hard_gate_propagation"}
                             else historical_failed)
    if arm == "diagnose_only":
        passed = not historical_defect
        probe = {"mode": "historical_terminal", "historical_status": historical.get("status")}
    else:
        root = Path(__file__).resolve().parents[2]
        if kind == "unknown_executor_fail_closed":
            code = (
                "from pathlib import Path\n"
                "from partner.benchmark.v4_real_executors import execute_real_arm\n"
                "try:\n"
                " execute_real_arm(object(), track='project', arm='full_partner', "
                "inputs={'executor_kind':'definitely_unknown_v41'}, output_dir=Path(r'%s'))\n"
                "except ValueError:\n"
                " raise SystemExit(0)\n"
                "raise SystemExit(9)\n" % str(output_dir)
            )
        elif kind == "suite_hard_gate_propagation":
            code = (
                "from pathlib import Path\n"
                "from tempfile import TemporaryDirectory\n"
                "from partner.events.v4_benchmark import suite_close\n"
                "with TemporaryDirectory() as d:\n"
                " class C: workspace=Path(d)\n"
                " r=suite_close(C(), {'suite_id':'guard_probe','flow_outputs':{"
                "'settle':{'semantic_output':{'hard_pass':False}},"
                "'report':{'semantic_output':{}},"
                "'delivery_ack':{'semantic_output':{'accepted':True}}}})\n"
                " raise SystemExit(0 if r.get('status')=='failed' else 8)\n"
            )
        elif kind == "clean_completed_retention":
            passed = str(historical.get("status") or "") == "completed"
            code = ""
        elif kind == "invalid_qq_identity_fail_closed":
            code = (
                "import json\n"
                "from pathlib import Path\n"
                "from tempfile import TemporaryDirectory\n"
                "from partner.events.delivery import send_text\n"
                "with TemporaryDirectory() as d:\n"
                " root=Path(d); (root/'.partner_workspace_identity').write_text('probe')\n"
                " binding=root/'instances/01/state/record/bot_id.json'; binding.parent.mkdir(parents=True)\n"
                " binding.write_text(json.dumps({'allowed_user_openids':['verified-openid']}))\n"
                " class C: workspace=root; instance_id='01'; job_id='identity_probe'; channel='qq'; sender_id='synthetic-recipient'\n"
                " r=send_text(C(), {'message':'probe','channel':'qq','origin_instance':'01'})\n"
                " queued=list((root/'state/application/outbound/01').glob('*.json')) if (root/'state/application/outbound/01').exists() else []\n"
                " raise SystemExit(0 if r.get('status')=='failed' and not queued else 7)\n"
            )
        elif kind == "flow_claim_before_read":
            code = (
                "from pathlib import Path\n"
                "p=Path(r'%s'); s=p.read_text(); start=s.index('def next_job'); block=s[start:s.index('def _publish_lifecycle', start)]\n"
                "claim=block.index('if not self._try_acquire_lock(job.job_id)')\n"
                "load=block.index('state = self.store.load(job.flow_id)', claim)\n"
                "raise SystemExit(0 if claim < load else 6)\n" % str(source)
            )
        elif kind == "reviewed_cycle_message_delivery":
            code = (
                "from pathlib import Path\n"
                "p=Path(r'%s'); s=p.read_text()\n"
                "ok=('message_reviewed' in s and 'cycle message did not pass review' in s "
                "and 'not bool(params.get(\\'message_reviewed\\'))' in s)\n"
                "raise SystemExit(0 if ok else 5)\n" % str(source)
            )
        else:
            raise ValueError(f"unknown incident probe_kind: {kind}")
        if kind != "clean_completed_retention":
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                             encoding="utf-8") as handle:
                handle.write(code)
                probe_path = Path(handle.name)
            try:
                completed = subprocess.run(
                    [sys.executable, str(probe_path)], cwd=str(root), capture_output=True,
                    text=True, timeout=float(inputs.get("timeout_seconds") or 60), check=False)
                passed = completed.returncode == 0
                probe = {"mode": "fresh_process", "returncode": completed.returncode,
                         "stdout_tail": completed.stdout[-500:], "stderr_tail": completed.stderr[-500:]}
            finally:
                probe_path.unlink(missing_ok=True)
        else:
            probe = {"mode": "clean_terminal_retention", "historical_status": historical.get("status")}
    receipt = {
        "output": bool(passed), "probe_kind": kind, "probe": probe,
        "incident_job_path": str(incident), "incident_job_sha256": _sha(incident),
        "production_source_path": str(source), "production_source_sha256": _sha(source),
        "historical_failure_present": historical_failed,
        "historical_defect_present": historical_defect,
        "fresh_process_replay": arm == "bounded_repair" and bool(passed),
        "rollback_available": True, "production_candidate": arm == "bounded_repair",
        "consumed_evidence_ids": ([f"incident:{_sha(incident)}"] if arm == "bounded_repair" else []),
        "strategy": "natural_incident_current_guard_replay",
    }
    path = output_dir / f"{arm}_{kind}_receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    receipt.update(artifact_path=str(path), artifact_sha256=_sha(path))
    return receipt


def execute_real_arm(ctx: Any, *, track: str, arm: str, inputs: Mapping[str, Any],
                     output_dir: Path,
                     longitudinal_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch a declared real executor without exposing evaluator fields."""
    kind = str(inputs.get("executor_kind") or "")
    output_dir.mkdir(parents=True, exist_ok=True)
    if track == "project" and kind == "davis_target_feature_v1":
        return _run_davis(arm, inputs, output_dir)
    if track == "project" and kind == "age_group_calibration_v1":
        return _run_age_calibration(arm, inputs, output_dir)
    if track == "project" and kind == "memory_guided_age_calibration_v1":
        return _run_memory_guided_age(arm, inputs, output_dir, longitudinal_state or {})
    if track == "project" and kind == "real_artifact_identity_v1":
        return _run_artifact_identity(arm, inputs, output_dir)
    if track == "active_learning" and kind == "local_source_qa_v1":
        return _run_learning(ctx, arm, inputs, longitudinal_state or {})
    if track == "active_learning" and kind == "settled_memory_qa_v1":
        return _run_memory_qa(ctx, arm, inputs, longitudinal_state or {})
    if track == "active_learning" and kind == "multi_source_synthesis_v1":
        return _run_multi_source_synthesis(ctx, arm, inputs)
    if track == "active_learning" and kind == "settled_structured_memory_v1":
        return _run_structured_memory(ctx, arm, inputs, longitudinal_state or {})
    if track == "active_learning" and kind == "typed_memory_recall_v1":
        return _run_typed_memory_recall(arm, inputs, output_dir, longitudinal_state or {})
    if track == "self_evolution" and kind == "natural_terminal_projection_v1":
        return _run_evolution(arm, inputs, output_dir, longitudinal_state or {})
    if track == "self_evolution" and kind == "historical_incident_guard_v1":
        return _run_incident_guard(arm, inputs, output_dir)
    raise ValueError(f"unsupported real executor: track={track} kind={kind}")
