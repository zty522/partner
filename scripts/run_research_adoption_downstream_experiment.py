#!/usr/bin/env python3
"""Run three counterbalanced real downstream LLM task pairs for instance 04."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from partner.adapters.direct_api import _resolve_api_json, chat, select_model_and_tokens
from partner.governance.context_selector import select_context
from partner.governance.evolution_events import verify_evolution_ledger
from partner.governance.evolution_loop import decide_experiment, record_issue, start_experiment
from partner.governance.research_adoption import compile_research_candidate
from partner.governance.research_downstream import (
    evaluate_downstream_output,
    frozen_tasks,
    parse_json_response,
    sanitize_external_context,
    local_grounded_consumer,
)
from partner.governance.storage import atomic_json, latest_receipt, workspace_root
from partner.v2.candidate_events import atomic_execute_candidate


JITRL_PATH = ("/mnt/e/work/partner_workspace/external/literature/"
              "Just-In-Time Reinforcement Learning Continual Learning in LLM Agents Without Gradient Updates.pdf")
HERMES_PATH = "/mnt/e/work/partner_workspace/external/code/hermes-agent/agent/context_compressor.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _marker(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _model_call(prompt: str, *, provider: str, model: str, max_tokens: int, timeout: int) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    response = ""
    for attempt in (1, 2):
        started = time.monotonic()
        attempt_prompt = prompt if attempt == 1 else (
            "上一响应没有形成完整可解析 JSON。请省略全部思考过程，严格遵守字段字数上限，"
            "只返回一个短小且闭合的 JSON 对象。\n" + prompt
        )
        response = chat(attempt_prompt, max_tokens=max_tokens, temperature=0.0,
                        purpose="classify", timeout=timeout, provider=provider)
        parsed, parse_error = parse_json_response(response)
        attempts.append({"attempt": attempt, "elapsed_sec": round(time.monotonic() - started, 3),
                         "response_chars": len(response), "json_parseable": bool(parsed),
                         "parse_error": parse_error})
        if parsed:
            break
    return {"response": response, "attempts": attempts, "model": model,
            "temperature": 0.0, "max_tokens": max_tokens, "timeout": timeout,
            "prompt_chars": len(prompt)}


def run(workspace: Path, *, research_project_id: str, budget_chars: int = 9000,
        max_tokens: int = 2200, timeout: int = 90,
        external_redacted: bool = False, local_deterministic: bool = False,
        provider_name: str = "") -> dict[str, Any]:
    root = workspace_root(str(workspace.resolve()))
    receipt = latest_receipt(str(root), "literature_github_learning")
    if receipt is None:
        raise RuntimeError("instance 04 project has no latest Receipt")
    research_dir = (root / "share/mind/governance/research_learning/projects"
                    / research_project_id)
    prior_experiment = (root / "instances/05/state/tasks/research_adoption_20260831_185347"
                        / "real_project_matched_experiment.json")
    issue = record_issue(str(root), {
        "summary": "research adoption Candidate has context evidence gains but no downstream task-quality comparison",
        "category": "context", "severity": "medium", "instance_id": "04",
        "project_id": "agent_self_evolution",
        "evidence": [str(prior_experiment), str(research_dir / "manifest.json")],
    })
    if not issue.get("ok"):
        raise RuntimeError(f"issue registration failed: {issue}")
    experiment = start_experiment(str(root), {
        "issue_id": issue["issue"]["issue_id"],
        "hypothesis": "evidence/trajectory context improves grounded 04 downstream answers under the same model, task contract and context budget",
        "intervention": "candidate context only; identical counterbalanced model call and independent deterministic truth evaluator",
        "baseline": {"strategy_id": "baseline_governed_context_v1",
                     "model_calls": 3, "budget_chars": budget_chars,
                     "temperature": 0.0, "max_tokens": max_tokens},
        "success_criteria": ["three independent frozen downstream task pairs",
                             "identical model and decoding settings per arm",
                             "candidate truth/safety passes all tasks",
                             "candidate mean reward improves by at least 0.10",
                             "no task-level reward regression", "no project state or Receipt mutation",
                             "production policy remains untouched"],
        "project_id": "agent_self_evolution",
        "tests": [str(prior_experiment), str(research_dir / "manifest.json")],
    })
    if not experiment.get("ok"):
        raise RuntimeError(f"experiment registration failed: {experiment}")
    experiment_id = experiment["experiment"]["experiment_id"]
    candidate_id = f"candidate_research_downstream_{experiment_id[-12:]}"
    local_root = Path(__file__).resolve().parents[1] / "partner/governance"
    compiled = compile_research_candidate(
        str(root), research_project_id=research_project_id, experiment_id=experiment_id,
        candidate_id=candidate_id,
        local_evidence_paths=[str(local_root / "context_selector.py"),
                              str(local_root / "manual_runtime.py"),
                              str(local_root / "storage.py")],
    )
    if not compiled.get("ok"):
        raise RuntimeError(f"candidate compilation failed: {compiled}")

    receipt_ref = "receipt://current-04" if external_redacted else receipt.receipt_id
    jitrl_ref = "source://jitrl-paper" if external_redacted else JITRL_PATH
    hermes_ref = "source://hermes-context-compressor" if external_redacted else HERMES_PATH
    tasks = frozen_tasks(receipt_id=receipt_ref,
                         receipt_actions=list(receipt.actions_executed),
                         jitrl_path=jitrl_ref, hermes_path=hermes_ref)
    cfg = _resolve_api_json(provider_name)
    if provider_name and not cfg:
        raise RuntimeError(f"configured provider unavailable: {provider_name}")
    selected_model, selected_tokens = select_model_and_tokens(cfg, "classify", max_tokens)
    selected_tokens = int(selected_tokens or max_tokens)
    provider = str(cfg.get("_provider") or "deepseek")
    if local_deterministic:
        provider, selected_model = "local", "typed-evidence-consumer-v1"
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    working = root / "instances/05/state/tasks" / f"research_downstream_{stamp}"
    working.mkdir(parents=True, exist_ok=True)
    project_state = root / "share/projects/literature_github_learning/governance/project_state.json"
    receipt_path = next((root / "share/projects/literature_github_learning/governance/receipts").glob(
        f"*{receipt.receipt_id}*.json"))
    immutable_before = {str(project_state): _sha(project_state), str(receipt_path): _sha(receipt_path)}

    pairs: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, 1):
        query = str(task["question"])
        frozen = {"task_id": task["task_id"], "question": query,
                  "receipt_id": receipt.receipt_id, "budget_chars": budget_chars,
                  "provider": provider, "model": selected_model,
                  "temperature": 0.0, "max_tokens": selected_tokens}
        execution_marker = _marker(frozen)
        baseline_selection, baseline_context = select_context(
            str(root), query, instance_id="04", project_id="literature_github_learning",
            budget_chars=budget_chars, requested_ids=[], semantic_selector=None)
        task_dir = working / f"pair_{index}_{task['task_id']}"
        ctx_task = SimpleNamespace(workspace=str(root / "instances/05"), working_dir=str(task_dir),
                                   user_message=query)
        ctx = SimpleNamespace(workspace=str(root / "instances/05"), working_dir=str(task_dir),
                              task_instance=ctx_task)
        candidate_context_result = atomic_execute_candidate(ctx, {
            "candidate_id": candidate_id,
            "execution_id": f"exec_{experiment_id}_{task['task_id']}_{execution_marker[:10]}",
            "instance_id": "05", "mode": "shadow",
            "event_params": {"query": query, "project_id": "literature_github_learning",
                             "research_project_id": research_project_id,
                             "instance_id": "04", "budget_chars": budget_chars},
        })
        if not candidate_context_result.get("ok"):
            raise RuntimeError(f"candidate context failed for {task['task_id']}: {candidate_context_result}")
        candidate_context = str(candidate_context_result.get("context") or "")

        contexts = {"baseline": baseline_context, "candidate": candidate_context}
        payload_audits: dict[str, Any] = {}
        if external_redacted:
            aliases = {receipt.receipt_id: receipt_ref, JITRL_PATH: jitrl_ref,
                       HERMES_PATH: hermes_ref,
                       "/mnt/e/work/partner_workspace/external/code/openai-codex/codex-rs/core/src/compact.rs":
                           "source://codex-compact"}
            for arm in ("baseline", "candidate"):
                audit = sanitize_external_context(
                    contexts[arm], query=query, aliases=aliases, limit_chars=6500)
                if not audit["ok"]:
                    raise RuntimeError(f"external payload redaction failed for {task['task_id']}/{arm}: {audit}")
                contexts[arm] = str(audit["context"])
                payload_audits[arm] = {key: value for key, value in audit.items() if key != "context"}
        # Counterbalance order to avoid a fixed first/second-call confound.
        order = ["baseline", "candidate"] if index % 2 else ["candidate", "baseline"]
        arms: dict[str, Any] = {}
        for arm in order:
            prompt = ("You are executing a frozen Partner instance-04 research task.\n"
                      f"EXECUTION_MARKER={execution_marker}\n"
                      f"CONTEXT_BEGIN\n{contexts[arm]}\nCONTEXT_END\n"
                      f"TASK_BEGIN\n{query}\nTASK_END")
            if local_deterministic:
                started = time.monotonic()
                response = local_grounded_consumer(task, contexts[arm])
                call = {"response": response,
                        "attempts": [{"attempt": 1,
                                      "elapsed_sec": round(time.monotonic() - started, 6),
                                      "response_chars": len(response), "json_parseable": True,
                                      "parse_error": ""}],
                        "model": selected_model, "temperature": 0.0,
                        "max_tokens": selected_tokens, "timeout": timeout,
                        "prompt_chars": len(prompt)}
            else:
                call = _model_call(prompt, provider=provider, model=selected_model, max_tokens=selected_tokens,
                                   timeout=timeout)
            evaluation = evaluate_downstream_output(
                task, call["response"], context=contexts[arm], budget_chars=budget_chars)
            output_path = task_dir / f"{arm}_downstream_output.json"
            arm_record = {
                "arm": arm, "strategy_id": ("baseline_governed_context_v1" if arm == "baseline"
                                               else candidate_context_result.get("strategy_id")),
                "execution_marker": execution_marker,
                "context_digest": hashlib.sha256(contexts[arm].encode()).hexdigest(),
                "context_chars": len(contexts[arm]), "model_call": call,
                "evaluation": evaluation,
            }
            atomic_json(output_path, arm_record)
            arm_record["path"] = str(output_path)
            arms[arm] = arm_record
        pairs.append({"match_key": f"downstream-{index}", "task_id": task["task_id"],
                      "execution_marker": execution_marker, "call_order": order,
                      "baseline": arms["baseline"], "candidate": arms["candidate"],
                      "external_payload_audits": payload_audits,
                      "reward_delta": (arms["candidate"]["evaluation"]["reward"]
                                       - arms["baseline"]["evaluation"]["reward"]),
                      "candidate_context_execution_event_id": candidate_context_result.get("execution_event_id")})

    immutable_after = {path: _sha(Path(path)) for path in immutable_before}
    baseline_rewards = [row["baseline"]["evaluation"]["reward"] for row in pairs]
    candidate_rewards = [row["candidate"]["evaluation"]["reward"] for row in pairs]
    baseline_mean = sum(baseline_rewards) / len(baseline_rewards)
    candidate_mean = sum(candidate_rewards) / len(candidate_rewards)
    criteria = {
        "three_independent_pairs": len(pairs) == 3 and len({row["execution_marker"] for row in pairs}) == 3,
        "same_model_and_settings": all(
            row["baseline"]["model_call"][key] == row["candidate"]["model_call"][key]
            for row in pairs for key in ("model", "temperature", "max_tokens", "timeout")),
        "candidate_all_truth_safety_pass": all(row["candidate"]["evaluation"]["ok"] for row in pairs),
        "candidate_mean_reward_gain": candidate_mean >= baseline_mean + .10,
        "no_task_reward_regression": all(row["reward_delta"] >= 0 for row in pairs),
        "project_state_immutable": immutable_before == immutable_after,
        "production_untouched": all(
            row.get("candidate_context_execution_event_id") for row in pairs)
            and compiled.get("production_effective") is False,
    }
    result: dict[str, Any] = {
        "schema_version": 1, "experiment_id": experiment_id, "candidate_id": candidate_id,
        "mode": "real_downstream_llm_matched_shadow", "research_project_id": research_project_id,
        "model_contract": {"provider": provider, "model": selected_model, "temperature": 0.0,
                           "max_tokens": selected_tokens, "timeout": timeout,
                           "max_attempts_per_arm": 2,
                           "external_redacted": external_redacted,
                           "payload_mode": ("query_projected_irreversible_redaction_v1"
                                            if external_redacted else "local_full_context"),
                           "local_deterministic": local_deterministic},
        "compiled_candidate": compiled, "pairs": pairs,
        "metrics": {"baseline_rewards": baseline_rewards, "candidate_rewards": candidate_rewards,
                    "baseline_mean_reward": baseline_mean, "candidate_mean_reward": candidate_mean,
                    "mean_reward_delta": candidate_mean - baseline_mean,
                    "baseline_pass_rate": sum(row["baseline"]["evaluation"]["ok"] for row in pairs) / 3,
                    "candidate_pass_rate": sum(row["candidate"]["evaluation"]["ok"] for row in pairs) / 3,
                    "baseline_prompt_chars": sum(row["baseline"]["model_call"]["prompt_chars"] for row in pairs),
                    "candidate_prompt_chars": sum(row["candidate"]["model_call"]["prompt_chars"] for row in pairs)},
        "criteria": criteria, "immutable_evidence": immutable_after,
        "production_effective": False, "promotion": False,
        "business_progress": False, "policy_eligible": False,
        "limits": ["benchmark outputs are not user-delivered project artifacts",
                   "three tasks use one model and one project snapshot",
                   "longitudinal business reward remains unproven",
                   ("local deterministic consumer does not establish LLM answer quality"
                    if local_deterministic else "external model result remains provider-specific")],
    }
    result_path = working / "research_adoption_downstream_experiment.json"
    atomic_json(result_path, result)
    decision = decide_experiment(str(root), {
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "decision": "inconclusive", "criteria_results": criteria,
        "evidence": [str(result_path), compiled["adoption_path"],
                     *[arm["path"] for row in pairs for arm in (row["baseline"], row["candidate"]) ]],
        "regression_passed": False,
        "metrics_before": {"mean_reward": baseline_mean, "pass_rate": result["metrics"]["baseline_pass_rate"]},
        "metrics_after": {"mean_reward": candidate_mean, "pass_rate": result["metrics"]["candidate_pass_rate"]},
        "rollback_required": False,
        "reason": "downstream shadow evidence recorded; full regression and longitudinal/user-delivered business outcomes remain separate gates",
        "project_id": "agent_self_evolution",
    })
    result["policy_decision"] = decision
    result["evolution_ledger"] = verify_evolution_ledger(str(root))
    atomic_json(result_path, result)
    # Task workspaces may be rotated or reset when an instance restarts.  A
    # production-readiness input is governance evidence and therefore also
    # receives a stable, Partner-owned copy outside the instance directory.
    governed_path = (root / "share/mind/governance/experience_guided_policy/llm_experiments"
                     / f"{experiment_id}.json")
    atomic_json(governed_path, result)
    result["task_result_path"] = str(result_path)
    result["result_path"] = str(governed_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--research-project", required=True)
    parser.add_argument("--budget-chars", type=int, default=9000)
    parser.add_argument("--max-tokens", type=int, default=3200)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--external-redacted", action="store_true")
    parser.add_argument("--local-deterministic", action="store_true")
    parser.add_argument("--provider", choices=("minimax", "deepseek"), default="")
    args = parser.parse_args()
    result = run(Path(args.workspace), research_project_id=args.research_project,
                 budget_chars=args.budget_chars, max_tokens=args.max_tokens,
                 timeout=args.timeout, external_redacted=args.external_redacted,
                 local_deterministic=args.local_deterministic, provider_name=args.provider)
    print(json.dumps({"experiment_id": result["experiment_id"],
                      "candidate_id": result["candidate_id"],
                      "model_contract": result["model_contract"],
                      "metrics": result["metrics"], "criteria": result["criteria"],
                      "policy_status": result["policy_decision"].get("status"),
                      "ledger": result["evolution_ledger"],
                      "result_path": result["result_path"]}, ensure_ascii=False, indent=2))
    return 0 if all(result["criteria"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
