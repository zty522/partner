"""Turn audited research evidence into a bounded context-selection Candidate.

The module deliberately implements one small adoption slice.  It combines:

* protected, budgeted project handoff context;
* relevant historical ``state/action/reward`` trajectories; and
* direct research excerpts with stable source identities.

It does not mutate the production selector and it never promotes itself.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .candidate_skills import register_candidate_skill
from .context_selector import select_context
from .models import now_iso
from .storage import atomic_json, latest_receipt, safe_id, workspace_root
from .research_learning import normalize_research_evidence_text


CANDIDATE_EVENT_TYPE = "research_adoption_context_shadow"
CANDIDATE_STRATEGY = "candidate_evidence_trajectory_context_v1"
BASELINE_STRATEGY = "baseline_governed_context_v1"


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tokens(value: Any) -> set[str]:
    raw = str(value or "").lower().replace("_", " ").replace("-", " ")
    latin = re.findall(r"[a-z][a-z0-9+]{1,}", raw)
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", raw)
    fragments: list[str] = []
    for token in chinese:
        fragments.append(token)
        fragments.extend(token[index:index + 3] for index in range(max(0, len(token) - 2)))
    return set(latin + fragments)


def _research_dir(workspace: str, project_id: str) -> Path:
    return (workspace_root(workspace) / "share/mind/governance/research_learning/projects"
            / safe_id(project_id))


def _current_verified_evidence(row: dict[str, Any]) -> dict[str, Any] | None:
    """Rebind a historical evidence record to the current canonical source text."""
    from .research_learning import read_research_source_text

    try:
        source = Path(str(row.get("source_path") or "")).resolve()
        raw = source.read_bytes()
        expected = str((row.get("source_identity") or {}).get("sha256") or "")
        if not expected or hashlib.sha256(raw).hexdigest() != expected:
            return None
        body = normalize_research_evidence_text(read_research_source_text(source))
    except (OSError, ValueError):
        return None
    old = normalize_research_evidence_text(str(row.get("evidence_quote") or ""))
    if old and old in body:
        quote = old
    else:
        # Old PDF extraction could inject a repeated page title into the
        # middle of an otherwise valid excerpt.  Anchor on a substantial
        # prefix and take a fresh contiguous window from today's canonical
        # extraction; never fuzzy-repair arbitrary model text.
        anchor = old[:120]
        index = body.find(anchor) if len(anchor) >= 40 else -1
        if index < 0:
            return None
        quote = body[index:index + min(520, max(120, len(old)))]
    return {**row, "evidence_quote": quote}


def _evidence_claims(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    claims = {"bounded_context": [], "handoff_continuity": [], "trajectory_memory": []}
    for row in rows:
        terms = set(str(value) for value in row.get("matched_terms") or [])
        question = str(row.get("question") or "").lower()
        ref = str(row.get("source_path") or "")
        if terms & {"budget", "context", "messages", "token", "summaries"}:
            claims["bounded_context"].append(ref)
        if terms & {"continuity", "preserve", "state", "summaries", "compaction"}:
            claims["handoff_continuity"].append(ref)
        if (terms & {"runtime", "feedback", "agent", "gradient", "updates", "improve"}
                and ("feedback" in question or "gradient" in question or "runtime" in question)):
            claims["trajectory_memory"].append(ref)
    return {key: sorted(set(values)) for key, values in claims.items()}


def compile_research_candidate(
    workspace: str,
    *,
    research_project_id: str,
    experiment_id: str,
    local_evidence_paths: list[str],
    candidate_id: str = CANDIDATE_STRATEGY,
) -> dict[str, Any]:
    """Compile a Candidate only when external and local evidence hard gates pass."""
    directory = _research_dir(workspace, research_project_id)
    manifest = _json(directory / "manifest.json")
    evidence = [row for row in _jsonl(directory / "investigations.jsonl")
                if row.get("evidence_found") is True]
    unique_sources = sorted({str(row.get("source_path") or "") for row in evidence
                             if str(row.get("source_path") or "")})
    source_kinds = {str((row.get("source_identity") or {}).get("kind") or "")
                    for row in evidence}
    claims = _evidence_claims(evidence)

    local_records: list[dict[str, Any]] = []
    repo = Path(__file__).resolve().parents[2]
    for raw in local_evidence_paths:
        path = Path(raw).resolve()
        if not path.is_file() or not (path == repo or repo in path.parents):
            return {"ok": False, "status": "local_evidence_invalid",
                    "error": f"local evidence must be a Partner repository file: {path}",
                    "production_effective": False}
        payload = path.read_bytes()
        local_records.append({"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
                              "bytes": len(payload)})

    gates = {
        "manifest_observed": manifest.get("status") == "observed",
        "two_independent_sources": len(unique_sources) >= 2,
        "code_and_paper_evidence": {"code", "paper"} <= source_kinds,
        "bounded_context_claim": bool(claims["bounded_context"]),
        "handoff_continuity_claim": bool(claims["handoff_continuity"]),
        "trajectory_memory_claim": bool(claims["trajectory_memory"]),
        "partner_local_implementation_evidence": len(local_records) >= 3,
    }
    if not all(gates.values()):
        return {"ok": False, "status": "research_adoption_gate_failed", "gates": gates,
                "claims": claims, "unique_sources": unique_sources,
                "production_effective": False}

    adoption = {
        "schema_version": 1, "created_at": now_iso(),
        "research_project_id": safe_id(research_project_id),
        "evidence_digest": _digest({"manifest": manifest.get("evidence_digest"),
                                    "evidence": evidence, "local": local_records}),
        "claims": claims, "external_evidence": [
            {"investigation_id": row.get("investigation_id"),
             "source_path": row.get("source_path"),
             "source_identity": row.get("source_identity"),
             "evidence_quote": row.get("evidence_quote"),
             "matched_terms": row.get("matched_terms")}
            for row in evidence
        ],
        "local_evidence": local_records, "gates": gates,
        "implementation_scope": [
            "reserve bounded space for the latest project Receipt",
            "retrieve same-project trajectories by task relevance and reward evidence",
            "include bounded direct research excerpts with source identity",
        ],
        "excluded_scope": ["model weight updates", "automatic production routing",
                           "automatic promotion", "Campaign or unbounded iteration"],
        "production_mutation": False, "production_effective": False,
    }
    adoption_path = directory / "adoption" / f"{safe_id(candidate_id)}.json"
    atomic_json(adoption_path, adoption)
    source_refs = [str(directory / "evidence" / f"{row['investigation_id']}.json")
                   for row in evidence]
    source_refs.extend(str(row["path"]) for row in local_records)
    registration = register_candidate_skill(workspace, {
        "candidate_id": candidate_id,
        "title": "多源证据与轨迹记忆辅助的项目上下文",
        "status": "candidate", "artifact_type": "event_context_policy",
        "project_id": "agent_self_evolution", "experiment_id": experiment_id,
        "strategy_id": CANDIDATE_STRATEGY, "source_episode_ids": source_refs,
        "failure_classes": ["context.continuity", "project.handoff_loss"],
        "applicability": ["bounded manual project task", "existing project Receipt",
                          "same-project trajectory evidence", "shadow evaluation"],
        "non_applicability": ["production activation", "cross-project memory",
                              "autonomous Campaign", "model weight training"],
        "counterexamples": ["no project Receipt", "no same-project trajectory",
                            "research evidence fails source diversity"],
        "baseline": {"strategy_id": BASELINE_STRATEGY,
                     "behavior": "catalog context without trajectory retrieval guarantee"},
        "intervention": "protect Receipt budget, retrieve bounded same-project trajectories, and attach audited research excerpts",
        "execution_contract": {"ready": True, "kind": "event",
                               "event_type": CANDIDATE_EVENT_TYPE,
                               "allowed_instances": ["04", "05"],
                               "default_params": {"research_project_id": safe_id(research_project_id),
                                                  "budget_chars": 9000}},
        "evaluation_contract": {"ready": False, "kind": "external_matched_runner",
                                "runner": "scripts/run_research_adoption_real_project_experiment.py",
                                "reason": "evaluation creates a fresh frozen Experiment and must not be invoked as the Candidate intervention itself"},
        "production_readiness_contract": {
            "required": True, "version": "learned_candidate_readiness_v1",
            "gates": ["two external model families",
                      "sustained delivered business artifacts",
                      "longitudinal experience-policy learning with negative samples, drift windows and rollback"],
        },
        "success_criteria": ["same frozen task and budget", "latest Receipt preserved",
                             "relevant same-project trajectory retrieved",
                             "no cross-project trajectory leakage", "truth and budget gates pass",
                             "full regression passes"],
        "shadow_evidence": {"adoption_path": str(adoption_path), "gates": gates},
        "rollback": "do not route production context selection to this Candidate",
    })
    return {"ok": True, "status": "candidate_compiled", "candidate_id": candidate_id,
            "adoption_path": str(adoption_path), "adoption": adoption,
            "registration": registration, "files": [str(adoption_path)],
            "production_effective": False}


def _rank_trajectories(workspace: str, *, query: str, project_id: str,
                       limit: int = 3) -> list[dict[str, Any]]:
    path = workspace_root(workspace) / "share/mind/governance/experience_guided_policy/trajectories.jsonl"
    query_tokens = _tokens(query)
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for index, row in enumerate(_jsonl(path)):
        if str(row.get("project_id") or "") != project_id:
            continue
        action = dict(row.get("action") or {})
        outcome = dict(row.get("outcome") or {})
        searchable = json.dumps({"action": action, "outcome": outcome}, ensure_ascii=False)
        overlap = len(query_tokens & _tokens(searchable))
        reward = float(row.get("reward") or 0.0)
        evidence_bonus = .5 if outcome.get("novel_evidence") else 0.0
        progress_bonus = .5 if (outcome.get("business_progress")
                                 or outcome.get("learning_progress")) else 0.0
        # Negative examples remain retrievable when their mechanism matches the query.
        failure = str(outcome.get("failure_mechanism") or "")
        failure_bonus = 2.0 if failure and (_tokens(failure) & query_tokens) else 0.0
        score = overlap * 3.0 + reward + evidence_bonus + progress_bonus + failure_bonus
        ranked.append((score, index, row))
    ranked.sort(key=lambda value: (-value[0], -value[1]))
    return [row for _, _, row in ranked[:max(0, int(limit))]]


def select_research_adoption_context(
    workspace: str,
    *,
    query: str,
    project_id: str,
    research_project_id: str,
    instance_id: str = "04",
    budget_chars: int = 9000,
) -> dict[str, Any]:
    """Execute the Candidate without changing production context selection."""
    budget_chars = max(3000, min(int(budget_chars), 50_000))
    receipt = latest_receipt(workspace, project_id)
    if receipt is None:
        return {"ok": False, "status": "candidate_missing_project_receipt",
                "project_id": project_id, "production_effective": False}

    # Keep the production loader for canonical docs, but constrain its share so
    # runtime state cannot be crowded out by long documents.
    canonical_budget = max(1400, int(budget_chars * .36))
    selection, canonical = select_context(
        workspace, query, instance_id=instance_id, project_id=project_id,
        budget_chars=canonical_budget, requested_ids=[], boosted_ids=[],
        reserve_receipt_chars=max(900, int(canonical_budget * .28)),
        semantic_selector=None,
    )
    protected_receipt = json.dumps({
        "receipt_id": receipt.receipt_id, "project_id": receipt.project_id,
        "iteration": receipt.iteration, "goal": receipt.goal,
        "actions_executed": receipt.actions_executed,
        "findings": [str(value)[:240] for value in receipt.findings[-4:]],
        "next_actions": [value.to_dict() for value in receipt.next_actions[:3]],
        "stop_reason": receipt.stop_reason, "delivery_confirmed": receipt.delivery_confirmed,
    }, ensure_ascii=False, sort_keys=False)
    receipt_chunk = (f"<!-- protected_project_handoff receipt:{receipt.receipt_id} -->\n"
                     f"{protected_receipt}\n")
    chunks = [receipt_chunk, canonical]
    used = len(receipt_chunk) + len(canonical)
    selected_refs = [str(row.get("document_id") or "") for row in selection.selected]

    evidence_rows = [verified for row in _jsonl(
        _research_dir(workspace, research_project_id) / "investigations.jsonl")
        if row.get("evidence_found") is True
        for verified in [_current_verified_evidence(row)] if verified is not None]
    query_tokens = _tokens(query)
    query_lower = query.lower()
    def evidence_score(row: dict[str, Any]) -> int:
        source = str(row.get("source_path") or "").lower()
        terms = {str(value).lower() for value in row.get("matched_terms") or []}
        score = len(query_tokens & _tokens(str(row.get("question") or "") + " "
                                           + " ".join(terms))) * 10
        if ("jitrl" in query_lower or "运行时反馈" in query_lower
                or "不更新模型" in query_lower or "不更新参数" in query_lower):
            if ("just-in-time" in source
                    or {"feedback", "gradient", "updates"} <= terms):
                score += 50
        if ("hermes" in query_lower or "handoff" in query_lower
                or "压缩" in query_lower or "连续性" in query_lower):
            if "hermes" in source or {"continuity", "preserve"} <= terms:
                score += 50
        return score
    evidence_rows.sort(key=lambda row: (
        -evidence_score(row), str(row.get("investigation_id") or ""),
    ))
    # Complementary evidence matters more than two excerpts from one file.
    selected_evidence: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for row in evidence_rows:
        source = str(row.get("source_path") or "")
        if source in seen_sources:
            continue
        selected_evidence.append(row)
        seen_sources.add(source)
        if len(selected_evidence) == 2:
            break
    evidence_refs: list[str] = []
    verified_source_evidence: list[dict[str, Any]] = []
    for row in selected_evidence:
        compact = {"investigation_id": row.get("investigation_id"),
                   "question": row.get("question"), "source_path": row.get("source_path"),
                   "source_sha256": (row.get("source_identity") or {}).get("sha256"),
                   "evidence_quote": normalize_research_evidence_text(
                       str(row.get("evidence_quote") or ""))[:520]}
        raw = json.dumps(compact, ensure_ascii=False, sort_keys=True)
        rendered = f"\n<!-- research_evidence:{row.get('investigation_id')} -->\n{raw}\n"
        if used + len(rendered) > budget_chars:
            break
        chunks.append(rendered)
        used += len(rendered)
        evidence_refs.append(str(row.get("investigation_id") or ""))
        verified_source_evidence.append(compact)

    # Trajectories are useful but lower priority than direct source evidence.
    # Keep each record complete JSON; never append then slice it away.
    trajectories = _rank_trajectories(workspace, query=query, project_id=project_id, limit=3)
    trajectory_refs: list[str] = []
    for row in trajectories:
        action = dict(row.get("action") or {})
        outcome = dict(row.get("outcome") or {})
        compact = {
            "trajectory_id": row.get("trajectory_id"), "project_id": row.get("project_id"),
            "kind": row.get("kind"),
            "action": {"action_key": action.get("action_key"),
                       "event_types": list(action.get("event_types") or [])[:6],
                       "strategy_id": action.get("strategy_id")},
            "outcome": {"status": outcome.get("status"),
                        "business_progress": outcome.get("business_progress"),
                        "learning_progress": outcome.get("learning_progress"),
                        "failure_mechanism": outcome.get("failure_mechanism"),
                        "evidence": [str(value)[:180] for value in
                                     list(outcome.get("evidence") or [])[:2]]},
            "reward": row.get("reward"),
        }
        raw = json.dumps(compact, ensure_ascii=False, sort_keys=True)
        rendered = f"\n<!-- trajectory:{row.get('trajectory_id')} project:{project_id} -->\n{raw}\n"
        if used + len(rendered) > budget_chars:
            break
        chunks.append(rendered)
        used += len(rendered)
        trajectory_refs.append(str(row.get("trajectory_id") or ""))

    context = "".join(chunks)
    next_actions = [value.to_dict() for value in receipt.next_actions]
    result = {
        "ok": True, "status": "research_adoption_context_selected",
        "strategy_id": CANDIDATE_STRATEGY, "project_id": project_id,
        "research_project_id": safe_id(research_project_id), "instance_id": instance_id,
        "selected_context_refs": selected_refs,
        "latest_receipt_id": receipt.receipt_id, "next_actions": next_actions,
        "trajectory_refs": trajectory_refs, "research_evidence_refs": evidence_refs,
        # This is the typed bridge into the manual claim-level truth gate.
        # Consumers must still reopen the file, verify its digest and check
        # quote membership; the Candidate output is evidence metadata, not a
        # trusted assertion.
        "verified_source_evidence": verified_source_evidence,
        "budget_chars": budget_chars, "budget_used": len(context),
        "context": context, "context_digest": hashlib.sha256(context.encode()).hexdigest(),
        "production_mutation": False, "production_effective": False, "promotion": False,
    }
    result["selection_digest"] = _digest({key: value for key, value in result.items()
                                          if key not in {"context", "selection_digest"}})
    return result


def write_candidate_context_artifact(result: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "research_adoption_context.json"
    atomic_json(path, result)
    return {**result, "path": str(path), "files": [str(path)],
            "summary": (f"项目 {result.get('project_id')} 上下文 Candidate：Receipt="
                        f"{result.get('latest_receipt_id')}，轨迹={len(result.get('trajectory_refs') or [])}，"
                        f"证据={len(result.get('research_evidence_refs') or [])}，"
                        "production_effective=false")}
