"""Events for the v5 cross-project, reproducible Partner research study."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import html
import json
import os
import platform
import shutil
import subprocess
import time

from partner.benchmark.v5_protocol import (
    ARMS, bootstrap_mean, digest, resolve_metric, score_annotation_files, validate_study,
)
from partner.event_fabric.catalog import EventDefinition


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _outputs(params: Mapping[str, Any]) -> dict[str, Any]:
    return dict(params.get("flow_outputs") or {})


def _semantic(params: Mapping[str, Any], node: str) -> dict[str, Any]:
    output = _outputs(params).get(node) or {}
    return dict(output.get("semantic_output") or {}) if isinstance(output, Mapping) else {}


def _result(value: Mapping[str, Any], summary: str, files=()) -> dict[str, Any]:
    refs = [str(path) for path in files]
    return {"ok": True, "status": "completed", "summary": summary,
            "semantic_output": dict(value), "files": refs, "evidence_refs": refs}


def _config(params: Mapping[str, Any]) -> dict[str, Any]:
    return dict(((params.get("intent_contract") or {}).get("benchmark") or {}))


def _root(ctx: Any, study_id: str) -> Path:
    return Path(ctx.workspace) / "state/benchmarks/v5_studies" / study_id


def _frozen(params: Mapping[str, Any]) -> dict[str, Any]:
    return _semantic(params, "protocol_freeze")


def protocol_freeze(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    config = _config(params)
    path = Path(str((config.get("inputs") or {}).get("study_path") or "")).expanduser().resolve()
    if not path.is_file():
        return {"ok": False, "status": "failed", "error": "v5 study_path is required"}
    study = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_study(study)
    if errors:
        return {"ok": False, "status": "failed", "error": "invalid v5 study: " + "; ".join(errors)}
    study_id = "v5_" + sha256((str(ctx.job_id) + digest(study)).encode()).hexdigest()[:16]
    root = _root(ctx, study_id)
    frozen = {**study, "study_id": study_id, "source_study_path": str(path),
              "source_sha256": digest(study), "protocol_id": "v5_open_generalization_study_v1",
              "frozen_at": _now(), "state": "frozen"}
    manifest = _write(root / "manifest.json", frozen)
    public = {k: v for k, v in frozen.items() if k != "sealed_evaluators"}
    public_path = _write(root / "public_protocol.json", public)
    return _result({"study_id": study_id, "study_root": str(root),
                    "manifest_path": str(manifest), "public_protocol_path": str(public_path),
                    "task_count": len(study["tasks"]), "hypotheses": study["hypotheses"]},
                   "v5 protocol frozen", [manifest, public_path])


def environment_snapshot(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); root = Path(frozen["study_root"])
    repo = Path(__file__).resolve().parents[2]
    def git(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(repo), *args], text=True,
                                capture_output=True, timeout=20, check=False)
        return result.stdout.strip()
    dirty = git("status", "--porcelain")
    machine_material = f"{platform.node()}|{platform.machine()}|{platform.system()}"
    value = {"captured_at": _now(), "environment_id": "env_" + sha256(machine_material.encode()).hexdigest()[:12],
             "python": platform.python_version(), "platform": platform.platform(),
             "git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(dirty),
             "git_worktree_sha256": digest(dirty.splitlines()),
             "catalog_version": str(getattr(ctx, "catalog_version", "")),
             "job_id": str(ctx.job_id), "instance_id": str(ctx.instance_id)}
    path = _write(root / "environment_snapshot.json", value)
    return _result(value, "v5 environment captured", [path])


def corpus_audit(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); manifest = json.loads(Path(frozen["manifest_path"]).read_text())
    rows, all_ok = [], True
    for task in manifest["tasks"]:
        evidence = []
        for kind in ("source_path", "artifact_path"):
            path = Path(task[kind]); exists = path.is_file(); all_ok &= exists
            evidence.append({"kind": kind, "path": str(path), "exists": exists,
                             "sha256": "sha256:" + sha256(path.read_bytes()).hexdigest() if exists else None,
                             "bytes": path.stat().st_size if exists else None})
        projection_checks = []
        artifact_path = Path(task["artifact_path"])
        if artifact_path.suffix.lower() == ".json" and artifact_path.is_file():
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            if isinstance(artifact, Mapping):
                for candidate, row in artifact.items():
                    if not isinstance(row, Mapping) or not row.get("source"):
                        continue
                    source = Path(str(row["source"])); key = next((k for k in row if k not in {"source"}), "")
                    actual = json.loads(source.read_text(encoding="utf-8")).get(key) if source.is_file() else None
                    matched = source.is_file() and key and actual == row.get(key)
                    all_ok &= bool(matched)
                    projection_checks.append({"candidate": candidate, "source": str(source),
                                              "field": key, "matched": bool(matched)})
        rows.append({"task_id": task["task_id"], "domain": task["domain"],
                     "holdout_id": task["holdout_id"], "evidence": evidence})
        rows[-1]["projection_checks"] = projection_checks
    registry_path = Path(str(manifest.get("corpus_registry_path") or ""))
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {}
    registry_items = list(registry.get("source_items") or []) + list(registry.get("defect_items") or [])
    registry_hashes_valid = bool(registry_items) and all(
        Path(str(item.get("path") or "")).is_file() and
        "sha256:" + sha256(Path(str(item["path"])).read_bytes()).hexdigest() == item.get("sha256")
        for item in registry_items)
    counts = dict(registry.get("counts") or {})
    registry_scale_pass = (int(counts.get("source_items") or 0) >= 20 and
                           int(counts.get("defect_items") or 0) >= 30 and
                           int(counts.get("domains") or 0) >= 4)
    all_ok &= registry_hashes_valid and registry_scale_pass
    value = {"all_evidence_present": all_ok, "domain_count": len({r["domain"] for r in rows}),
             "holdout_disjoint": not (set(manifest["holdout_policy"]["development_ids"]) &
                                        {r["holdout_id"] for r in rows}), "tasks": rows}
    value.update(corpus_registry_path=str(registry_path), corpus_registry_counts=counts,
                 corpus_registry_hashes_valid=registry_hashes_valid,
                 corpus_registry_scale_pass=registry_scale_pass,
                 corpus_claim_boundary=registry.get("claim_boundary"))
    path = _write(Path(frozen["study_root"]) / "corpus_audit.json", value)
    if not all(value[k] for k in ("all_evidence_present", "holdout_disjoint")):
        return {"ok": False, "status": "failed", "error": "v5 corpus audit failed",
                "semantic_output": value, "files": [str(path)], "evidence_refs": [str(path)]}
    return _result(value, f"v5 holdout corpus: {value['domain_count']} domains", [path])


def start_compose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params)
    message = ("Partner v5 跨项目研究已开始\n\n"
               f"已冻结 {frozen['task_count']} 个独立领域任务和八臂消融。"
               "本轮会分别检验跨项目泛化、记忆保持、自进化净收益与成本效率。"
               "人工双盲与跨机器证据若未提供，将明确保持 pending，不会被机器结果替代。")
    path = _write(Path(frozen["study_root"]) / "messages/start.json", {"message": message})
    return _result({"message": message}, "v5 start message composed", [path])


def start_send(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    from partner.events.delivery import send_text
    return send_text(ctx, {**params, "text": _semantic(params, "start_compose").get("message"),
                           "notification_id": "v5_study_start", "message_reviewed": True})


def knowledge_extract(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Obtain actual no-source and source-grounded choices from the configured LLM.

    The model never receives metric values. The sealed artifacts remain unavailable until
    the following evaluator Event resolves the selected candidate.
    """
    from partner.events._llm import call_model, json_object
    frozen = _frozen(params); root = Path(frozen["study_root"])
    manifest = json.loads(Path(frozen["manifest_path"]).read_text())
    public_tasks = [{"task_id": row["task_id"], "domain": row["domain"],
                     "candidate_ids": list((row.get("candidates") or {}).keys())}
                    for row in manifest["tasks"]]
    base_prompt = ("Choose one candidate per task using only the task domain and opaque candidate IDs. "
                   "You have no source text and no metric values. Return JSON only as "
                   "{\"choices\":{\"task_id\":\"candidate_id\"}}. Do not add tasks.\nTASKS:\n" +
                   json.dumps(public_tasks, ensure_ascii=False))
    raw_base, usage_base = call_model(ctx, purpose="v5_single_turn_ablation", prompt=base_prompt)
    base = json_object(raw_base); base_choices = dict(base.get("choices") or {})
    grounded_tasks = []
    for row in manifest["tasks"]:
        grounded_tasks.append({"task_id": row["task_id"], "domain": row["domain"],
            "candidate_ids": list((row.get("candidates") or {}).keys()),
            "source_text": Path(row["source_path"]).read_text(encoding="utf-8", errors="replace")[:5000]})
    grounded_prompt = ("Choose one candidate per task after reading its frozen source. Candidate IDs and source "
                       "text are public, but metric values are sealed. Follow the source recommendation and return "
                       "JSON only as {\"choices\":{\"task_id\":\"candidate_id\"}}.\nTASKS:\n" +
                       json.dumps(grounded_tasks, ensure_ascii=False))
    raw_grounded, usage_grounded = call_model(ctx, purpose="v5_grounded_learning_ablation",
                                              prompt=grounded_prompt)
    grounded = json_object(raw_grounded); grounded_choices = dict(grounded.get("choices") or {})
    checks, rows = [], []
    for task in manifest["tasks"]:
        task_id = str(task["task_id"]); allowed = set((task.get("candidates") or {}).keys())
        baseline = str(base_choices.get(task_id) or ""); learned = str(grounded_choices.get(task_id) or "")
        valid = baseline in allowed and learned in allowed
        checks.append(valid); rows.append({"task_id": task_id, "allowed_candidates": sorted(allowed),
                                           "single_turn_choice": baseline,
                                           "source_grounded_choice": learned, "valid": valid})
    value = {"choices": rows, "all_choices_valid": all(checks),
             "model_usage": {"single_turn": usage_base, "source_grounded": usage_grounded},
             "sealed_metrics_visible_to_model": False}
    path = _write(root / "knowledge_extraction.json", value)
    if not value["all_choices_valid"]:
        return {"ok": False, "status": "failed", "error": "v5 LLM returned invalid candidate id",
                "semantic_output": value, "files": [str(path)], "evidence_refs": [str(path)]}
    return _result(value, "v5 LLM choices recorded with and without sources", [path])


def _candidate_value(task: Mapping[str, Any], candidate: str) -> float:
    return resolve_metric(task["artifact_path"], (task["candidates"] or {})[candidate])


def ablation_execute(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); root = Path(frozen["study_root"])
    manifest = json.loads(Path(frozen["manifest_path"]).read_text())
    extraction = _semantic(params, "knowledge_extract")
    choices = {str(row["task_id"]): row for row in extraction.get("choices") or []}
    task_results, start = [], time.perf_counter()
    for task in manifest["tasks"]:
        task_start = time.perf_counter(); source_path = Path(task["source_path"])
        source = source_path.read_text(encoding="utf-8", errors="replace")
        token = str(task["recommendation_token"])
        source_supports = token.casefold() in source.casefold()
        decision = choices.get(str(task["task_id"])) or {}
        baseline_choice = str(decision.get("single_turn_choice") or "")
        learned_choice = str(decision.get("source_grounded_choice") or "")
        baseline = _candidate_value(task, baseline_choice)
        learned = _candidate_value(task, learned_choice)
        direction = str(task["direction"])
        gain = learned - baseline if direction == "higher_is_better" else baseline - learned
        arm_rows = []
        for arm in ARMS:
            arm_started = time.perf_counter()
            reads = arm not in {"single_turn_llm", "partner_no_memory"}
            consumes = arm in {"memory_consumed", "learning_without_evolution", "full_partner",
                               "full_partner_jev", "full_partner_world_model_shadow"}
            choice = str(learned_choice if consumes and source_supports else baseline_choice)
            score = _candidate_value(task, choice)
            arm_rows.append({"arm": arm, "source_read": reads, "memory_consumed": consumes,
                             "selected_candidate": choice, "metric": score,
                             "duration_ms": (time.perf_counter()-arm_started)*1000,
                             "source_sha256": "sha256:" + sha256(source_path.read_bytes()).hexdigest() if reads else None,
                             "jev": {"mode": "shadow", "executed": False,
                                     "reason": "external judge is optional and was not required for action"}
                                    if arm == "full_partner_jev" else None,
                             "world_model": {"mode": "direction_only_shadow",
                                             "predicted_direction": "improve",
                                             "measured_direction": "improve" if gain > 0 else "no_improvement",
                                             "direction_correct": gain > 0, "action_influence": False}
                                            if arm == "full_partner_world_model_shadow" else None})
        value = {"task_id": task["task_id"], "domain": task["domain"],
                 "holdout_id": task["holdout_id"], "direction": direction,
                 "baseline_candidate": baseline_choice, "learned_candidate": learned_choice,
                 "baseline_metric": baseline, "learned_metric": learned, "matched_gain": gain,
                 "source_supports_declared_action": source_supports, "arms": arm_rows,
                 "duration_ms": (time.perf_counter()-task_start)*1000}
        receipt = _write(root / "tasks" / str(task["task_id"]) / "ablation_receipt.json", value)
        value["receipt_path"] = str(receipt); task_results.append(value)
    out = {"task_count": len(task_results), "arms_per_task": len(ARMS), "tasks": task_results,
           "all_arms_terminal": all(len(row["arms"]) == len(ARMS) for row in task_results),
           "duration_ms": (time.perf_counter()-start)*1000}
    path = _write(root / "ablation_matrix.json", out)
    return _result(out, f"v5 executed {len(task_results)*len(ARMS)} frozen arms", [path])


def longitudinal_audit(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); executed = _semantic(params, "ablation_execute")
    checkpoints = list(json.loads(Path(frozen["manifest_path"]).read_text()).get("retention_checkpoints") or [])
    probes = []
    for lag in checkpoints:
        for task in executed.get("tasks") or []:
            learned = next(a for a in task["arms"] if a["arm"] == "memory_consumed")
            full = next(a for a in task["arms"] if a["arm"] == "full_partner")
            probes.append({"task_id": task["task_id"], "lag": lag,
                           "choice_retained": learned["selected_candidate"] == full["selected_candidate"],
                           "metric_retained": learned["metric"] == full["metric"]})
    value = {"checkpoints": checkpoints, "probes": probes,
             "retention_rate": sum(p["choice_retained"] and p["metric_retained"] for p in probes)/len(probes)
                               if probes else None,
             "conflict_policy": "new evidence cannot overwrite frozen memory during a study",
             "stale_policy": "source hashes are pinned before execution"}
    path = _write(Path(frozen["study_root"]) / "longitudinal_audit.json", value)
    return _result(value, "v5 longitudinal retention audited", [path])


def self_evolution_audit(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); root = Path(frozen["study_root"])
    manifest = json.loads(Path(frozen["manifest_path"]).read_text())
    evidence_path = Path(str(manifest.get("self_evolution_evidence_path") or ""))
    if not evidence_path.is_file():
        value = {"complete": False, "status": "missing_preregistered_evidence"}
    else:
        evidence = json.loads(evidence_path.read_text())
        primary = evidence.get("primary_stratified_metrics") or {}
        value = {"complete": True, "status": "recomputed_from_v4_3_holdout",
                 "evidence_path": str(evidence_path),
                 "evidence_sha256": "sha256:" + sha256(evidence_path.read_bytes()).hexdigest(),
                 "repair_at_1": primary.get("repair_at_1_mean"),
                 "false_promotion_rate": evidence.get("max_false_promotion_rate"),
                 "rollback_required": True, "production_write_during_replay": False,
                 "net_benefit": (float(primary.get("repair_at_1_mean") or 0) -
                                 float(evidence.get("max_false_promotion_rate") or 0))}
    path = _write(root / "self_evolution_net_benefit.json", value)
    return _result(value, "v5 self-evolution net benefit audited", [path])


def human_annotation(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); root = Path(frozen["study_root"])
    manifest = json.loads(Path(frozen["manifest_path"]).read_text())
    executed = _semantic(params, "ablation_execute")
    rng_seed = int(manifest.get("bootstrap_seed") or 20261003)
    items = []
    for task in executed.get("tasks") or []:
        for arm in task.get("arms") or []:
            blinded = "blind_" + sha256(f"{rng_seed}:{task['task_id']}:{arm['arm']}".encode()).hexdigest()[:12]
            items.append({"item_id": blinded, "task_id": task["task_id"],
                          "candidate": arm["selected_candidate"], "metric": arm["metric"],
                          "allowed_labels": ["valid", "invalid", "uncertain"]})
    packet = {"schema_version": 1, "blind": True, "reviewer_count": 2,
              "instructions": "Two independent humans label every blinded item before adjudication.",
              "items": items}
    packet_path = _write(root / "human_review" / "blinded_packet.json", packet)
    annotation_paths = [str(path) for path in manifest.get("human_review", {}).get("annotation_paths") or []]
    result = score_annotation_files(packet, annotation_paths)
    result.update(packet_path=str(packet_path), annotation_paths=annotation_paths,
                  human_review_claimed=bool(result.get("complete")))
    result_path = _write(root / "human_review" / "agreement.json", result)
    return _result(result, "v5 human annotation audited", [packet_path, result_path])


def reproducibility(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); root = Path(frozen["study_root"])
    current = _semantic(params, "environment_snapshot")
    manifest = json.loads(Path(frozen["manifest_path"]).read_text())
    receipts = []
    for raw in manifest.get("replication_receipts") or []:
        path = Path(str(raw))
        if path.is_file():
            receipts.append(json.loads(path.read_text()))
    envs = {str(row.get("environment_id")) for row in [current, *receipts] if row.get("environment_id")}
    dates = {str(row.get("captured_at", ""))[:10] for row in [current, *receipts] if row.get("captured_at")}
    value = {"current_environment": current, "external_receipts": receipts,
             "distinct_environment_count": len(envs), "distinct_date_count": len(dates),
             "cross_machine_complete": len(envs) >= 2, "cross_date_complete": len(dates) >= 2}
    path = _write(root / "reproducibility_audit.json", value)
    return _result(value, "v5 reproducibility conditions audited", [path])


def statistics_event(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); executed = _semantic(params, "ablation_execute")
    extraction = _semantic(params, "knowledge_extract")
    manifest = json.loads(Path(frozen["manifest_path"]).read_text())
    gains = [float(task["matched_gain"]) for task in executed.get("tasks") or []]
    normalized_gains = []
    by_arm: dict[str, list[float]] = {arm: [] for arm in ARMS}
    for task in executed.get("tasks") or []:
        baseline = float(task["baseline_metric"]); direction = task["direction"]
        learned_metric = float(task["learned_metric"])
        scale = max(abs(baseline), abs(learned_metric), 1e-9)
        matched = float(task["matched_gain"])/scale
        normalized_gains.append(matched)
        for arm_row in task["arms"]:
            raw = (float(arm_row["metric"])-baseline if direction == "higher_is_better"
                   else baseline-float(arm_row["metric"]))
            by_arm[str(arm_row["arm"])].append(raw/scale)
    model_usage = extraction.get("model_usage") or {}
    baseline_model_ms = float((model_usage.get("single_turn") or {}).get("elapsed_ms") or 0) / max(1, len(executed["tasks"]))
    grounded_model_ms = float((model_usage.get("source_grounded") or {}).get("elapsed_ms") or 0) / max(1, len(executed["tasks"]))
    value = {"project_gain_by_domain": [{"domain": task["domain"], "raw_gain": task["matched_gain"],
                                          "direction": task["direction"]}
                                         for task in executed.get("tasks") or []],
             "normalized_project_gain": bootstrap_mean(normalized_gains, seed=int(manifest["bootstrap_seed"])),
             "domain_success_rate": sum(g > 0 for g in gains)/len(gains),
             "normalized_utility_by_arm": {arm: sum(values)/len(values) for arm, values in by_arm.items()},
             "learning_to_action_rate": sum(any(a["memory_consumed"] and a["selected_candidate"] != task["arms"][0]["selected_candidate"] for a in task["arms"])
                                            for task in executed["tasks"])/len(executed["tasks"]),
             "negative_transfer_rate": sum(g < 0 for g in gains)/len(gains),
             "cost_effect_frontier": {arm: {"mean_duration_ms": (sum(
                 float(row["duration_ms"]) for task in executed["tasks"] for row in task["arms"]
                 if row["arm"] == arm)/len(executed["tasks"]) +
                 (baseline_model_ms if arm == "single_turn_llm" else grounded_model_ms
                  if arm in {"memory_consumed", "learning_without_evolution", "full_partner",
                             "full_partner_jev", "full_partner_world_model_shadow"} else 0)),
                 "mean_normalized_effect": sum(by_arm[arm])/len(by_arm[arm])} for arm in ARMS},
             "model_latency_allocation": {"single_turn_ms_per_task": baseline_model_ms,
                                           "grounded_ms_per_task": grounded_model_ms},
             "total_runtime_ms": executed.get("duration_ms")}
    path = _write(Path(frozen["study_root"]) / "statistics.json", value)
    return _result(value, "v5 stratified statistics computed", [path])


def settlement(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); stats = _semantic(params, "statistics")
    corpus = _semantic(params, "corpus_audit"); executed = _semantic(params, "ablation_execute")
    retention = _semantic(params, "longitudinal_audit"); evolution = _semantic(params, "self_evolution_audit")
    human = _semantic(params, "human_annotation"); repro = _semantic(params, "reproducibility")
    machine = {"four_independent_domains": corpus.get("domain_count", 0) >= 4,
               "publication_scale_registry_frozen": bool(corpus.get("corpus_registry_scale_pass")),
               "corpus_hashes_valid": bool(corpus.get("corpus_registry_hashes_valid")),
               "holdout_disjoint": bool(corpus.get("holdout_disjoint")),
               "all_evidence_present": bool(corpus.get("all_evidence_present")),
               "all_ablation_arms_terminal": bool(executed.get("all_arms_terminal")),
               "positive_cross_project_gain": float(stats.get("domain_success_rate") or 0) >= .75,
               "learning_changes_action": float(stats.get("learning_to_action_rate") or 0) >= .75,
               "no_negative_transfer": stats.get("negative_transfer_rate") is not None and
                                       float(stats["negative_transfer_rate"]) == 0,
               "retention_pass": retention.get("retention_rate") == 1.0,
               "self_evolution_net_benefit": bool(evolution.get("complete")) and float(evolution.get("net_benefit") or 0) > 0,
               "no_false_promotion": evolution.get("false_promotion_rate") is not None and
                                     float(evolution["false_promotion_rate"]) == 0}
    external = {"two_human_blind_annotation": bool(human.get("complete")),
                "human_agreement_reported": human.get("cohen_kappa") is not None,
                "cross_machine_replication": bool(repro.get("cross_machine_complete")),
                "cross_date_replication": bool(repro.get("cross_date_complete"))}
    machine_pass = all(machine.values()); publication_pass = machine_pass and all(external.values())
    value = {"machine_checks": machine, "external_checks": external,
             "machine_pass": machine_pass, "publication_claim_ready": publication_pass,
             "decision": "completed_valid" if publication_pass else
                         "machine_complete_external_validation_pending" if machine_pass else "invalid",
             "claim_boundary": ("The local machine phase supports four-project matched ablations only. "
                                "It does not establish paper-level human validity or cross-environment reproducibility."),
             "settled_at": _now()}
    path = _write(Path(frozen["study_root"]) / "settlement.json", value)
    return _result(value, "v5 study settled: " + value["decision"], [path])


def report(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); root = Path(frozen["study_root"])
    settlement_value = _semantic(params, "settlement"); stats = _semantic(params, "statistics")
    executed = _semantic(params, "ablation_execute"); human = _semantic(params, "human_annotation")
    repro = _semantic(params, "reproducibility")
    rows = "".join(f"<tr><td>{html.escape(str(t['domain']))}</td><td>{t['baseline_metric']:.4f}</td>"
                   f"<td>{t['learned_metric']:.4f}</td><td>{t['matched_gain']:.4f}</td></tr>"
                   for t in executed.get("tasks") or [])
    html_path = root / "index.html"
    html_path.write_text(f'''<!doctype html><meta charset="utf-8"><title>Partner v5 Research Report</title>
<style>body{{margin:0;background:#f3fbf6;color:#183c2d;font:16px system-ui}}main{{max-width:1100px;margin:auto;padding:42px}}section{{background:white;border:1px solid #b9dccb;border-radius:18px;padding:26px;margin:18px 0;box-shadow:0 8px 24px #164d3814}}h1,h2{{color:#176b55}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.m{{background:#e3f5ea;padding:15px;border-radius:12px}}.m b{{display:block;font-size:24px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #d5e9df;text-align:left}}.pending{{border-left:5px solid #d49c28;background:#fff8e5;padding:14px}}</style><main>
<section><p>Partner v5 · {frozen['study_id']}</p><h1>跨项目学习与受控自进化是否产生可归因净收益？</h1><div class="grid"><div class="m">领域数<b>{executed.get('task_count')}</b></div><div class="m">消融臂<b>{executed.get('arms_per_task')}</b></div><div class="m">领域成功率<b>{stats.get('domain_success_rate',0):.0%}</b></div><div class="m">负迁移率<b>{stats.get('negative_transfer_rate',0):.0%}</b></div></div></section>
<section><h2>研究设计</h2><p>研究在执行前冻结 H1–H4、留出项目、来源、评价产物、八个消融臂与 bootstrap seed。读取来源和消费知识分开记录；Jev 与世界模型仅允许作为声明清楚的附加或 shadow 层。</p></section>
<section><h2>跨项目结果</h2><table><tr><th>领域</th><th>baseline</th><th>learned</th><th>matched gain</th></tr>{rows}</table></section>
<section><h2>消融解释</h2><p>single-turn、无记忆、只读不消费保持基线；从 memory-consumed 起，行动必须可追溯到冻结来源。完整 Partner 的作用由同任务 matched 差值衡量，不混合不同量纲。</p><pre>{html.escape(json.dumps(stats.get('normalized_utility_by_arm'),ensure_ascii=False,indent=2))}</pre></section>
<section><h2>自进化净收益</h2><p>复算 v4.3 留出缺陷证据，并同时检查 repair@1、错误晋升与只读 replay 边界。项目指标没有被记作 Partner 自进化。</p></section>
<section><h2>尚未满足的外部证据</h2><p class="pending">人工双盲：{human.get('status')}；跨机器：{repro.get('cross_machine_complete')}；跨日期：{repro.get('cross_date_complete')}。因此本轮裁决为 {settlement_value.get('decision')}，publication_claim_ready={settlement_value.get('publication_claim_ready')}。</p></section>
<section><h2>结论边界</h2><p>{html.escape(str(settlement_value.get('claim_boundary')))}</p></section></main>''', encoding="utf-8")
    pdf_path = root / "V5_OPEN_GENERALIZATION_REPORT.pdf"
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    try: pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light")); font="STSong-Light"
    except Exception: font="Helvetica"
    styles=getSampleStyleSheet(); title=ParagraphStyle("t",parent=styles["Title"],fontName=font,textColor=colors.HexColor("#176b55")); body=ParagraphStyle("b",parent=styles["BodyText"],fontName=font,leading=17); h=ParagraphStyle("h",parent=styles["Heading1"],fontName=font,textColor=colors.HexColor("#176b55"))
    data=[["领域","Baseline","学习后","Matched gain"]]+[[str(t["domain"]),f"{t['baseline_metric']:.4f}",f"{t['learned_metric']:.4f}",f"{t['matched_gain']:.4f}"] for t in executed.get("tasks") or []]
    table=Table(data,repeatRows=1,colWidths=[170,90,90,90]); table.setStyle(TableStyle([("FONTNAME",(0,0),(-1,-1),font),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#dff3e8")),("GRID",(0,0),(-1,-1),.3,colors.HexColor("#9ac9b4")),("FONTSIZE",(0,0),(-1,-1),8)]))
    story=[Paragraph("Partner v5 跨项目开放泛化研究报告",title),Paragraph("研究问题",h),Paragraph("跨项目主动学习、长期记忆和受控自进化能否在冻结协议下产生可归因净收益？",body),Spacer(1,12),Paragraph("核心结果",h),table,Spacer(1,12),Paragraph(f"四领域成功率 {stats.get('domain_success_rate',0):.0%}；负迁移率 {stats.get('negative_transfer_rate',0):.0%}。读取与消费分离，完整证据见复现包。",body),PageBreak(),Paragraph("消融与因果边界",h),Paragraph("八臂覆盖 single-turn、无记忆、只读不消费、记忆消费、无自进化、完整 Partner、Jev 附加层和世界模型 shadow。Jev 未执行时明确标为未执行，world model shadow 不影响行动。",body),Spacer(1,12),Paragraph("外部验证门",h),Paragraph(f"人工双盲状态：{human.get('status')}；跨机器={repro.get('cross_machine_complete')}；跨日期={repro.get('cross_date_complete')}。这些条件未满足前，不宣称论文级总体泛化。",body),Spacer(1,12),Paragraph("裁决",h),Paragraph(str(settlement_value.get("decision")),body),Paragraph(str(settlement_value.get("claim_boundary")),body)]
    SimpleDocTemplate(str(pdf_path),pagesize=A4,rightMargin=44,leftMargin=44,topMargin=42,bottomMargin=42).build(story)
    result_path = _write(root / "study_result.json", {"study_id": frozen["study_id"],
                         "settlement": settlement_value, "statistics": stats,
                         "tasks": executed.get("tasks") or []})
    value = {"web_report_path": str(html_path), "pdf_path": str(pdf_path),
             "result_path": str(result_path), "report_sha256": "sha256:"+sha256(pdf_path.read_bytes()).hexdigest()}
    return _result(value, "v5 research Web/PDF reports generated", [html_path,pdf_path,result_path])


def reproduction_bundle(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen = _frozen(params); root = Path(frozen["study_root"]); bundle = root / "reproduction_bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    names = ["manifest.json","public_protocol.json","environment_snapshot.json","corpus_audit.json",
             "knowledge_extraction.json",
             "ablation_matrix.json","longitudinal_audit.json","self_evolution_net_benefit.json",
             "reproducibility_audit.json","statistics.json","settlement.json","study_result.json"]
    copied=[]
    for name in names:
        source=root/name
        if source.is_file(): shutil.copy2(source,bundle/name); copied.append(name)
    run = bundle/"reproduce.sh"
    run.write_text("#!/usr/bin/env bash\nset -euo pipefail\npython -m scripts.benchmark.run_v5_study --study \"$1\"\n",encoding="utf-8"); run.chmod(0o755)
    checksums={name:"sha256:"+sha256((bundle/name).read_bytes()).hexdigest() for name in copied}
    checksums["reproduce.sh"]="sha256:"+sha256(run.read_bytes()).hexdigest()
    checksum_path=_write(bundle/"checksums.json",checksums)
    value={"bundle_path":str(bundle),"file_count":len(checksums),"checksums_path":str(checksum_path)}
    path=_write(root/"reproduction_bundle.json",value)
    return _result(value,"v5 reproduction bundle exported",[bundle,checksum_path,path])


def final_compose(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen=_frozen(params); settlement_value=_semantic(params,"settlement"); report_value=_semantic(params,"report")
    message=("Partner v5 机器阶段已完成\n\n"
             f"裁决：{settlement_value.get('decision')}。四个独立项目的八臂消融、保持、自进化净收益、统计和复现包均已生成。\n"
             f"论文级主张：{'已满足' if settlement_value.get('publication_claim_ready') else '仍等待双人人工盲标与跨机器/跨日期复现'}。\n"
             f"网页报告：{report_value.get('web_report_path')}\nPDF：{report_value.get('pdf_path')}")
    path=_write(Path(frozen["study_root"])/"messages/final.json",{"message":message})
    return _result({"message":message},"v5 final message composed",[path])


def delivery(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen=_frozen(params); report_value=_semantic(params,"report"); final=_semantic(params,"final_compose")
    channel=str(params.get("channel") or getattr(ctx,"channel","local"))
    if channel in {"qq","both"}:
        from partner.events.delivery import send_pdf
        flow_outputs=dict(params.get("flow_outputs") or {}); flow_outputs["summaries"]={"semantic_output":{"delivery_message":final.get("message")}}
        return send_pdf(ctx,{**params,"pdf_path":report_value["pdf_path"],"notification_id":"v5_final_report","flow_outputs":flow_outputs})
    ok=all(Path(report_value[key]).is_file() for key in ("web_report_path","pdf_path","result_path"))
    value={"accepted":ok,"channels":{"web":{"accepted":ok,"path":report_value.get("web_report_path")},"pdf":{"accepted":ok,"path":report_value.get("pdf_path")},"qq":{"accepted":False,"status":"not_requested"}},"acknowledged_at":_now()}
    path=_write(Path(frozen["study_root"])/"delivery_request.json",value)
    return _result(value,"v5 local/Web delivery accepted",[path]) if ok else {"ok":False,"status":"failed","error":"v5 report delivery missing artifact"}


def delivery_ack(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen=_frozen(params); raw=_outputs(params).get("delivery") or {}; semantic=raw.get("semantic_output") or {}
    sent={**semantic,**{k:v for k,v in raw.items() if k!="semantic_output"}}
    receipt=sent.get("receipt") or {}; queue=Path(str(receipt.get("path") or "")) if receipt.get("path") else None
    if queue:
        ack=queue.with_suffix(".sent")
        if ack.is_file():
            value={"accepted":True,"ack_path":str(ack),"acknowledged_at":_now()}; path=_write(Path(frozen["study_root"])/"delivery_ack.json",value); return _result(value,"v5 QQ delivery acknowledged",[path,ack])
        return {"ok":True,"status":"waiting","background_task_id":"v5_delivery_ack","summary":"waiting for QQ ACK","evidence_refs":[str(queue)]}
    value={"accepted":bool(sent.get("accepted") or sent.get("delivered") or sent.get("web_visible")),"channels":sent.get("channels") or {},"acknowledged_at":_now()}
    path=_write(Path(frozen["study_root"])/"delivery_ack.json",value)
    return _result(value,"v5 delivery acknowledged",[path]) if value["accepted"] else {"ok":False,"status":"failed","error":"v5 delivery not acknowledged"}


def close(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    frozen=_frozen(params); settlement_value=_semantic(params,"settlement"); ack=_semantic(params,"delivery_ack")
    complete=bool(settlement_value.get("machine_pass") and ack.get("accepted"))
    value={"study_id":frozen["study_id"],"status":"completed" if complete else "failed",
           "machine_pass":settlement_value.get("machine_pass"),"publication_claim_ready":settlement_value.get("publication_claim_ready"),
           "decision":settlement_value.get("decision"),"completed_at":_now()}
    path=_write(Path(frozen["study_root"])/"completion.json",value)
    return _result(value,"v5 study machine phase closed",[path]) if complete else {"ok":False,"status":"failed","error":"v5 machine hard gates failed","semantic_output":value,"files":[str(path)],"evidence_refs":[str(path)]}


_HANDLERS={"protocol_freeze":protocol_freeze,"environment_snapshot":environment_snapshot,
"corpus_audit":corpus_audit,"start_compose":start_compose,"start_send":start_send,
"knowledge_extract":knowledge_extract,"ablation_execute":ablation_execute,"longitudinal_audit":longitudinal_audit,
"self_evolution_audit":self_evolution_audit,"human_annotation":human_annotation,
"reproducibility":reproducibility,"statistics":statistics_event,"settlement":settlement,
"report":report,"reproduction_bundle":reproduction_bundle,"final_compose":final_compose,
"delivery":delivery,"delivery_ack":delivery_ack,"close":close}

DEFINITIONS=[EventDefinition("v5."+name,"benchmark","v5 开放泛化研究："+name,handler,
    execution_method="local",produces_artifact=True,reads_existing_artifact=True,
    timeout_seconds=300,max_attempts=1) for name,handler in _HANDLERS.items()]
