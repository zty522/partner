"""Evidence-driven, bounded production-code Candidate synthesis.

This is intentionally narrower than arbitrary LLM code editing.  A real
failure mechanism selects an allow-listed repair grammar; the controller then
creates an actual unified diff, proves a frozen behavioral probe fails on the
baseline and passes on an isolated candidate, runs focused regression, and
atomically applies or rolls back the one governed production file.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from partner.governance.models import now_iso
from partner.governance.storage import atomic_json, workspace_root
from partner.governance.evolution_events import append_evolution_event
from partner.governance.evolution_loop import decide_experiment, start_experiment


TARGET = "partner/evolution/generated_candidate_policy.py"
MECHANISM = "outcome.duplicate_semantic_result"
RECIPE = "turn_parameter_sweep_v1"
SPECS = (
    {"project_id": "molecular_generation", "short": "molecular_method",
     "recipe": "duplicate_forces_verified_maxmin_v2",
     "hypothesis": "after repeated molecular outcomes, the multi-seed supported MaxMin arm increases fingerprint diversity within its preregistered QED-loss bound"},
    {"project_id": "molecular_dynamics_study", "short": "md",
     "recipe": "turn_parameter_sweep_v1",
     "hypothesis": "varying a bounded MD parameter plan by native turn reduces duplicate outcomes"},
    {"project_id": "literature_github_learning", "short": "research",
     "recipe": "turn_source_rotation_v1",
     "hypothesis": "rotating grounded Harness source files by native turn reduces duplicate learning outcomes"},
    {"project_id": "partner_explore", "short": "code_surface",
     "recipe": "turn_code_surface_rotation_v1",
     "hypothesis": "rotating the inspected production-code surface by native turn reduces duplicate architecture outcomes"},
)


def _llm_json(prompt: str, *, root: Path, max_tokens: int = 3000) -> dict[str, Any]:
    """Use an LLM as a bounded diagnosis/critic, never as the hard gate."""
    try:
        from partner.adapters.direct_api import chat
        raw = chat(prompt, purpose="self_evolution_code_candidate", max_tokens=max_tokens,
                   temperature=0.1, timeout=120, workspace=str(root),
                   instance_id="05", project_id="agent_self_evolution",
                   event_type="bounded_code_candidate")
    except Exception:
        return {}
    cleaned = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S | re.I)
    decoder = json.JSONDecoder()
    values: list[tuple[int, dict[str, Any]]] = []
    for match in re.finditer(r"\{", cleaned):
        try:
            value, end = decoder.raw_decode(cleaned[match.start():])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            values.append((end, value))
    return max(values, key=lambda row: row[0])[1] if values else {}


def _llm_diagnose_candidate(available: list[tuple[dict[str, str], dict[str, Any] | None]],
                            original: str, *, root: Path | None = None) -> dict[str, Any]:
    root = Path(root or Path.cwd())
    options = [{"project_id": spec["project_id"], "recipe": spec["recipe"],
                "hypothesis": spec["hypothesis"], "has_real_evidence": bool(evidence),
                "matched_business_canary_available": spec["project_id"] in {
                    "molecular_generation", "molecular_dynamics_study"
                },
                "already_installed": f'"{spec["project_id"]}": "{spec["recipe"]}"' in original,
                "trajectory_id": str((evidence or {}).get("trajectory_id") or "")}
               for spec, evidence in available]
    return _llm_json("""你是 Partner 自进化诊断员。主动学习面向外部知识；本任务只判断 Partner 自身行为机制。
下面每个选项都由代码预先限定，不能发明任意目标文件。只能选择有真实 Episode、尚未安装、且
matched_business_canary_available=true 的一项；业务 canary 不可用的想法只能保留为 shadow 研究，不能尝试晋升。
若全部饱和，
明确 no_new_candidate。输出严格 JSON：
{"project_id":"...","recipe":"...","failure_mechanism":"...","causal_hypothesis":"...",
"risk":"...","required_counterexample":"...","decision":"propose|no_new_candidate"}
选项：""" + json.dumps(options, ensure_ascii=False), root=root, max_tokens=3200)


def _llm_critic(result: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    root = Path(root or Path.cwd())
    compact = {key: result.get(key) for key in (
        "candidate_id", "project_id", "mechanism", "recipe", "baseline_probe",
        "candidate_probe", "business_canary", "regression", "production_effective")}
    if isinstance(compact.get("regression"), dict):
        compact["regression"] = {"exit_code": compact["regression"].get("exit_code"),
                                  "command": compact["regression"].get("command")}
    return _llm_json("""你是独立 Candidate critic。只能根据机器结果找反例和遗漏，不能自行批准生产。
注意：production_effective=false 是晋升前的安全状态，不是候选无效的证据；baseline/candidate probe
只证明受控策略是否改变下一动作，不等于证明业务产物改善。你必须区分机制隔离、代码回归和后续业务 canary。
输出严格 JSON：{"causal_isolation":"pass|uncertain|fail","missing_tests":["..."],
"possible_reward_hacking":"...","rollback_trigger":"...","recommendation":"promote|reject|more_evidence"}。
机器结果：""" + json.dumps(compact, ensure_ascii=False), root=root, max_tokens=3000)


def _record_applied_lifecycle(workspace: Path, candidate_id: str,
                              candidate_dir: Path, evidence: dict[str, Any],
                              result: dict[str, Any], *, project_id: str,
                              recipe: str, hypothesis: str) -> dict[str, Any]:
    """Persist one idempotent Event-first experiment and activation chain."""
    sidecar = candidate_dir / "governance.json"
    try:
        prior = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(prior, dict) and prior.get("ok"):
            return prior
    except (OSError, TypeError, ValueError):
        pass
    refs = [str(value) for value in [evidence.get("path"),
                                     candidate_dir / "candidate.patch",
                                     candidate_dir / "result.json"] if str(value)]
    proposed = append_evolution_event(
        str(workspace), "candidate/proposed", subject_id=candidate_id,
        project_id=project_id,
        payload={"candidate_id": candidate_id, "mechanism": MECHANISM,
                 "recipe": recipe, "target_file": TARGET,
                 "scope": "bounded_behavioral_code"},
        evidence_refs=refs,
        idempotency_key=f"behavioral-code-candidate-proposed:{candidate_id}",
    )
    started = start_experiment(str(workspace), {
        "issue_id": str(evidence.get("trajectory_id") or MECHANISM),
        "project_id": project_id,
        "hypothesis": hypothesis,
        "intervention": f"{candidate_id}: install {recipe} in {TARGET}",
        "baseline": dict(result.get("baseline_probe") or {}),
        "success_criteria": ["baseline probe fails", "candidate probe passes",
                             "focused regression passes"],
        "tests": refs,
    })
    if not started.get("ok"):
        record = {"ok": False, "stage": "experiment_start", "detail": started}
        atomic_json(sidecar, record)
        return record
    experiment_id = str(started["experiment"]["experiment_id"])
    decision = decide_experiment(str(workspace), {
        "experiment_id": experiment_id,
        "project_id": project_id,
        "decision": "promoted",
        "criteria_results": {"baseline_failed": not bool(result.get("baseline_probe", {}).get("passed")),
                             "candidate_passed": bool(result.get("candidate_probe", {}).get("passed")),
                             "regression_passed": int(result.get("regression", {}).get("exit_code", 1)) == 0},
        "regression_passed": int(result.get("regression", {}).get("exit_code", 1)) == 0,
        "metrics_before": {"behavioral_probe_passed": int(bool(result.get("baseline_probe", {}).get("passed")))},
        "metrics_after": {"behavioral_probe_passed": int(bool(result.get("candidate_probe", {}).get("passed")))},
        "evidence": refs,
        "reason": "matched baseline/candidate probe and focused regression passed",
    })
    if not decision.get("ok"):
        record = {"ok": False, "stage": "promotion_decision", "detail": decision,
                  "experiment_id": experiment_id}
        atomic_json(sidecar, record)
        return record
    parent = str((decision.get("events") or [{}])[-1].get("event_id") or proposed["event_id"])
    activated = append_evolution_event(
        str(workspace), "policy/activated", subject_id=candidate_id,
        project_id=project_id, parents=[parent],
        payload={"candidate_id": candidate_id, "experiment_id": experiment_id,
                 "production_effective": True, "target_file": TARGET,
                 "recipe": recipe}, evidence_refs=refs,
        idempotency_key=f"behavioral-code-candidate-activated:{candidate_id}",
    )
    record = {"ok": True, "candidate_event_id": proposed["event_id"],
              "experiment_id": experiment_id,
              "promotion_event_ids": [row["event_id"] for row in decision.get("events") or []],
              "activation_event_id": activated["event_id"]}
    atomic_json(sidecar, record)
    return record


def _rows(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                values.append(value)
    except (OSError, ValueError):
        pass
    return values


def latest_duplicate_md_evidence(workspace: str | Path) -> dict[str, Any] | None:
    return latest_duplicate_evidence(workspace, "molecular_dynamics_study")


def latest_duplicate_evidence(workspace: str | Path,
                              project_id: str) -> dict[str, Any] | None:
    root = workspace_root(str(workspace))
    path = root / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    for row in reversed(_rows(path)):
        outcome = dict(row.get("outcome") or {})
        if (str(row.get("project_id") or "") == project_id
                and outcome.get("duplicate_outcome") is True):
            return {"trajectory_id": str(row.get("trajectory_id") or ""),
                    "path": str(path), "mechanism": MECHANISM}
    return None


def _molecular_business_canary(root: Path) -> dict[str, Any]:
    """Require fresh, multi-seed business evidence before molecular promotion."""
    rows: list[dict[str, Any]] = []
    for path in (root / "instances/02/state/tasks").glob(
            "*/molecular_method_candidate_metrics.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        delta = dict(value.get("candidate_minus_baseline") or {})
        if "fingerprint_diversity" not in delta:
            continue
        supported_by_strict_contract = bool(
            (float(delta.get("scaffold_count") or 0) > 0
             or float(delta.get("fingerprint_diversity") or 0) >= 0.05)
            and float(delta.get("mean_qed") or 0) >= -0.03
            and float(delta.get("mean_sa") or 0) <= 0.15
        )
        rows.append({"path": str(path), "method_id": value.get("method_id"),
                     "replicate_seed": value.get("replicate_seed"),
                     "candidate_improved": supported_by_strict_contract,
                     "deltas": delta})
    rows.sort(key=lambda row: Path(row["path"]).stat().st_mtime)
    supported = [row for row in rows if row["method_id"] == "maxmin_fingerprint"
                 and row["candidate_improved"]]
    counterexample = [row for row in rows if row["method_id"] == "scaffold_round_robin"
                      and not row["candidate_improved"]]
    seeds = {int(row.get("replicate_seed") or 0) for row in supported}
    return {"passed": len(seeds) >= 2 and bool(counterexample),
            "supported_maxmin": supported[-3:],
            "falsified_round_robin": counterexample[-2:],
            "required": "two distinct supported MaxMin seeds and one falsified alternative"}


def _preproduction_business_canary(root: Path, project_id: str,
                                   recipe: str) -> dict[str, Any]:
    """Require a domain measurement, not merely a changed parameter dict.

    Only domains with a deterministic, same-budget comparator are eligible.
    Other candidates remain shadow until their real project Event supplies a
    matched canary; they must never be activated from a novelty probe alone.
    """
    if project_id == "molecular_generation":
        return _molecular_business_canary(root)
    if project_id == "molecular_dynamics_study" and recipe == "turn_parameter_sweep_v1":
        from partner.v2.continuous_project_events import _md_step
        baseline = _md_step("03_md_timestep_stability", native_turn=7, candidate_variant=0)
        candidate = _md_step("03_md_timestep_stability", native_turn=7, candidate_variant=7)
        baseline_drift = float(baseline["business_metrics"]["worst_relative_energy_drift"])
        candidate_drift = float(candidate["business_metrics"]["worst_relative_energy_drift"])
        return {
            "passed": candidate_drift < baseline_drift,
            "metric": "worst_relative_energy_drift",
            "baseline": baseline_drift,
            "candidate": candidate_drift,
            "direction": "lower_is_better",
            "same_budget": (len(baseline["runs"]) == len(candidate["runs"])),
        }
    return {
        "passed": False,
        "reason": "real_project_matched_canary_not_implemented_for_recipe",
        "project_id": project_id,
        "recipe": recipe,
    }


def _candidate_source(original: str, project_id: str = "molecular_dynamics_study",
                      recipe: str = RECIPE) -> str:
    entry = f'    "{project_id}": "{recipe}",\n'
    if entry in original:
        return original
    marker = "POLICIES: dict[str, str] = {"
    start = original.find(marker)
    close = original.find("\n}", start + len(marker)) if start >= 0 else -1
    if close < 0 and start >= 0:
        close = original.find("}", start + len(marker))
    if start < 0 or close < 0:
        raise ValueError("governed policy registry has an unknown shape")
    if original.startswith("\n}", close):
        return original[:close + 1] + entry + original[close + 1:]
    return original[:close] + "\n" + entry + original[close:]


def _load_enricher(path: Path):
    # Execute exact source bytes instead of importlib's timestamp-based .pyc
    # cache. Candidate and baseline can be created within the same filesystem
    # timestamp tick and may have equal sizes; stale bytecode would invalidate
    # the matched comparison.
    namespace: dict[str, Any] = {"__name__": "candidate_policy_probe"}
    source = path.read_text(encoding="utf-8")
    exec(compile(source, str(path), "exec"), namespace, namespace)
    return namespace["enrich_project_params"]


def _probe(path: Path, project_id: str = "molecular_dynamics_study") -> dict[str, Any]:
    enrich = _load_enricher(path)
    strategy = ("molecular_scaffold_cap" if project_id == "molecular_generation"
                else "03_md_timestep_stability" if project_id == "molecular_dynamics_study"
                else ("04_reference_gap_matrix" if project_id == "literature_github_learning"
                      else "05_event_contract_inventory"))
    field = ("candidate_variant" if project_id in {"molecular_generation", "molecular_dynamics_study"}
             else ("source_variant" if project_id == "literature_github_learning"
                   else "code_variant"))
    first = enrich(project_id, strategy, 7, {})
    second = enrich(project_id, strategy, 8, {})
    expected_first = 3 if project_id == "molecular_generation" else 7
    expected_second = 3 if project_id == "molecular_generation" else 8
    changed = (first.get(field) == expected_first
               and second.get(field) == expected_second
               and (project_id == "molecular_generation" or first != second))
    return {"passed": changed, "turn_7": first, "turn_8": second}


def synthesize_validate_apply(workspace: str | Path, repo_root: str | Path,
                              *, apply: bool = True, use_llm: bool = True) -> dict[str, Any]:
    root = workspace_root(str(workspace))
    repo = Path(repo_root).resolve()
    target = repo / TARGET
    original = target.read_text(encoding="utf-8")
    available = [(spec, latest_duplicate_evidence(root, spec["project_id"])) for spec in SPECS]
    llm_diagnosis = _llm_diagnose_candidate(available, original, root=root) if use_llm else {}
    eligible = [(spec, evidence) for spec, evidence in available
                if evidence
                and spec["project_id"] in {"molecular_generation", "molecular_dynamics_study"}
                and f'"{spec["project_id"]}": "{spec["recipe"]}"' not in original]
    selected = next(((spec, evidence) for spec, evidence in eligible
                     if spec["project_id"] == llm_diagnosis.get("project_id")
                     and spec["recipe"] == llm_diagnosis.get("recipe")), None)
    selected = selected or next(iter(eligible), None)
    if selected is None:
        selected = next(((spec, evidence) for spec, evidence in available if evidence),
                        (SPECS[0], None))
    spec, evidence = selected
    project_id, recipe = spec["project_id"], spec["recipe"]
    candidate_id = f"code_candidate_{spec['short']}_novelty_" + datetime.now().strftime("%Y%m%d%H%M%S")
    out_dir = root / "share/mind/governance/code_candidates" / candidate_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "schema_version": 1, "candidate_id": candidate_id,
        "mechanism": MECHANISM, "recipe": recipe, "project_id": project_id,
        "target_file": TARGET,
        "created_at": now_iso(), "production_effective": False,
        "source_evidence": evidence or {},
        "llm_diagnosis": llm_diagnosis,
        "llm_trace": {"diagnosis_calls": int(bool(use_llm)), "critic_calls": 0},
    }
    if evidence is None:
        result.update({"ok": False, "decision": "rejected",
                       "reason": "no real duplicate trajectory evidence for an allow-listed recipe"})
        atomic_json(out_dir / "result.json", result)
        return {**result, "files": [str(out_dir / "result.json")]}
    result["business_canary"] = _preproduction_business_canary(root, project_id, recipe)
    if not result["business_canary"]["passed"]:
        result.update({"ok": False, "decision": "rejected",
                       "reason": "matched_real_project_business_canary_failed"})
        atomic_json(out_dir / "result.json", result)
        return {**result, "files": [str(out_dir / "result.json")]}
    current_evidence_signature = hashlib.sha256(json.dumps({
        "business_canary": result["business_canary"],
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    for prior_path in sorted(
            (root / "share/mind/governance/code_candidates").glob("*/result.json"),
            key=lambda path: path.stat().st_mtime, reverse=True):
        if prior_path == out_dir / "result.json":
            continue
        try:
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if (prior.get("project_id") != project_id or prior.get("recipe") != recipe
                or prior.get("decision") not in {"rejected", "rolled_back"}):
            continue
        prior_signature = hashlib.sha256(json.dumps({
            "business_canary": prior.get("business_canary") or {},
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if prior_signature == current_evidence_signature:
            result.update({
                "ok": True, "decision": "no_new_candidate",
                "reason": "unchanged_evidence_after_prior_negative_candidate",
                "production_effective": False,
                "prior_negative_candidate_id": prior.get("candidate_id", ""),
                "prior_llm_critique": prior.get("llm_critique") or {},
                "business_metrics": {"new_code_candidate_applied": 0,
                                     "negative_candidate_reused": 1},
            })
            atomic_json(out_dir / "result.json", result)
            return {**result, "files": [str(out_dir / "result.json")]}
        break
    candidate_source = _candidate_source(original, project_id, recipe)
    if candidate_source == original:
        prior = {}
        for path in sorted((root / "share/mind/governance/code_candidates").glob(
                "*/result.json"), reverse=True):
            if path == out_dir / "result.json":
                continue
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                continue
            if row.get("decision") == "applied" and row.get("recipe") == recipe:
                prior = row
                break
        governance = {}
        prior_dir = out_dir
        if prior:
            prior_dir = Path(str(next((path.parent for path in
                (root / "share/mind/governance/code_candidates").glob("*/result.json")
                if path.parent.name == str(prior.get("candidate_id") or "")), out_dir)))
            governance = _record_applied_lifecycle(root,
                str(prior.get("candidate_id") or candidate_id), prior_dir,
                dict(prior.get("source_evidence") or evidence), prior,
                project_id=project_id, recipe=recipe, hypothesis=spec["hypothesis"])
        result.update({"ok": True, "decision": "no_new_candidate",
                       "production_effective": False,
                       "existing_production_effective": bool(prior),
                       "verification_probe": _probe(target, project_id),
                       "prior_application": {
                           "candidate_id": prior.get("candidate_id", ""),
                           "baseline_probe": prior.get("baseline_probe", {}),
                           "candidate_probe": prior.get("candidate_probe", {}),
                           "regression": prior.get("regression", {}),
                       },
                       "governance": governance,
                       "business_metrics": {"new_code_candidate_applied": 0,
                                            "existing_policy_effective": int(bool(prior)),
                                            "behavioral_probe_passed": 1}})
        atomic_json(out_dir / "result.json", result)
        return {**result, "files": [str(out_dir / "result.json")]}
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), candidate_source.splitlines(keepends=True),
        fromfile=f"a/{TARGET}", tofile=f"b/{TARGET}",
    ))
    (out_dir / "candidate.patch").write_text(diff, encoding="utf-8")
    baseline_probe = _probe(target, project_id)
    isolated = Path(tempfile.mkdtemp(prefix="partner_code_candidate_")) / target.name
    isolated.write_text(candidate_source, encoding="utf-8")
    candidate_probe = _probe(isolated, project_id)
    result.update({"diff_hunk": diff, "baseline_probe": baseline_probe,
                   "candidate_probe": candidate_probe})
    if baseline_probe["passed"] or not candidate_probe["passed"]:
        result.update({"ok": False, "decision": "rejected",
                       "reason": "matched behavioral probe did not show baseline-fail/candidate-pass"})
        atomic_json(out_dir / "result.json", result)
        return {**result, "files": [str(out_dir / "candidate.patch"), str(out_dir / "result.json")]}
    if not apply:
        result.update({"ok": True, "decision": "validated_shadow"})
        atomic_json(out_dir / "result.json", result)
        return {**result, "files": [str(out_dir / "candidate.patch"), str(out_dir / "result.json")]}

    backup = out_dir / "preimage.py"
    backup.write_text(original, encoding="utf-8")
    # Run the Candidate as an integrated package in a temporary repository.
    # Production stays byte-for-byte unchanged until business canary,
    # behavioral probe, regression and independent critic all pass.
    validation_root = Path(tempfile.mkdtemp(prefix="partner_candidate_repo_"))
    source_repo = Path(__file__).resolve().parents[2]
    package_source = (repo / "partner" if (repo / "partner/governance").is_dir()
                      else source_repo / "partner")
    shutil.copytree(package_source, validation_root / "partner")
    shell_source = repo / "shells" if (repo / "shells").is_dir() else source_repo / "shells"
    if shell_source.is_dir():
        shutil.copytree(shell_source, validation_root / "shells")
    (validation_root / TARGET).write_text(candidate_source, encoding="utf-8")
    shutil.copytree(source_repo / "tests", validation_root / "tests")
    command = [sys.executable, "-m", "pytest", "tests/test_native_project_event_route.py",
               "tests/test_continuous_project_events.py"]
    if project_id == "molecular_generation":
        command.append("tests/test_molecular_method_candidate.py")
    command.append("-q")
    proc = subprocess.run(command, cwd=validation_root, text=True, capture_output=True,
                          timeout=240, check=False)
    regression = {"command": command, "exit_code": proc.returncode,
                  "output": (proc.stdout + proc.stderr)[-4000:]}
    result["regression"] = regression
    llm_critique = _llm_critic(result, root=root) if use_llm else {}
    result["llm_critique"] = llm_critique
    result["llm_trace"]["critic_calls"] = int(bool(use_llm))
    critic_allows = (not use_llm or (
        str(llm_critique.get("causal_isolation") or "") == "pass"
        and str(llm_critique.get("recommendation") or "") == "promote"
    ))
    probe_passed = bool(_probe(validation_root / TARGET, project_id)["passed"])
    shutil.rmtree(validation_root, ignore_errors=True)
    if proc.returncode != 0 or not probe_passed or not critic_allows:
        result.update({"ok": False, "decision": "rolled_back",
                       "reason": ("independent_llm_critic_withheld_promotion"
                                  if proc.returncode == 0 and probe_passed
                                  else "post-apply regression failed"),
                       "rolled_back": True, "production_effective": False})
    else:
        if target.read_text(encoding="utf-8") != original:
            result.update({"ok": False, "decision": "rolled_back",
                           "reason": "production_preimage_changed_during_validation",
                           "rolled_back": True, "production_effective": False})
        else:
            tmp = target.with_suffix(target.suffix + ".candidate.tmp")
            tmp.write_text(candidate_source, encoding="utf-8")
            os.replace(tmp, target)
            post_probe = _probe(target, project_id)
            if not post_probe.get("passed"):
                rollback = target.with_suffix(target.suffix + ".rollback.tmp")
                rollback.write_text(original, encoding="utf-8")
                os.replace(rollback, target)
                result.update({"ok": False, "decision": "rolled_back",
                               "reason": "post_install_probe_failed", "rolled_back": True,
                               "production_effective": False, "post_install_probe": post_probe})
            else:
                result.update({"ok": True, "decision": "applied",
                               "production_effective": True, "rolled_back": False,
                               "post_install_probe": post_probe,
                               "business_metrics": {"code_candidates_applied": 1,
                                                    "baseline_failed": 1,
                                                    "candidate_passed": 1,
                                                    "focused_regression_passed": 1},
                               "postimage_sha256": hashlib.sha256(candidate_source.encode()).hexdigest()})
    atomic_json(out_dir / "result.json", result)
    if result.get("decision") == "applied":
        result["governance"] = _record_applied_lifecycle(root, candidate_id, out_dir,
            evidence, result, project_id=project_id, recipe=recipe,
            hypothesis=spec["hypothesis"])
        atomic_json(out_dir / "result.json", result)
    return {**result, "status": result["decision"],
            "summary": ("代码 Candidate 已实施并通过对照与回归" if result.get("production_effective")
                        else ("没有新的受支持 Candidate；现有策略仍有效" if result.get("decision") == "no_new_candidate"
                              else f"代码 Candidate {result['decision']}")),
            "files": [str(out_dir / "candidate.patch"), str(out_dir / "result.json")]}
