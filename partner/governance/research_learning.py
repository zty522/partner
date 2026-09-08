"""Evidence-grounded active learning for research projects.

This module connects Partner's source acquisition path (papers and GitHub
checkouts) to the same value-of-information discipline used by Agent failure
learning.  It deliberately stops at a shadow Candidate: reading a source is
knowledge acquisition, not proof that production behaviour improved.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from partner.cognition.active_learning import ActiveLearningOption, rank_active_learning_options

from .models import now_iso
from .storage import append_jsonl, atomic_json, safe_id, workspace_root


_ALLOWED_SOURCE_ROOTS = (
    Path("/mnt/e/work/partner_workspace/external/code"),
    Path("/mnt/e/work/partner_workspace/external/literature"),
    Path("/mnt/e/work/partner"),
)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "this", "to",
    "can", "does", "what", "when", "where", "which", "with", "under", "without",
    "是否", "如何", "什么", "以及",
    "实现", "机制", "代码", "论文", "系统", "项目",
}
_CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    # Canonical research-question concepts mapped to common paper/code terms.
    # Hits are still reported under the user's canonical term, keeping the
    # evidence decision explainable instead of replacing it with an embedding
    # score that cannot be audited.
    "runtime": ("runtime", "run-time", "test-time", "inference-time", "current state"),
    "feedback": ("feedback", "reward", "trajectory", "trajectories", "experience"),
    "improve": ("improve", "improves", "improved", "learn", "learns", "adapt", "adapts"),
    "continuity": ("continuity", "continue", "resume", "resumption", "handoff"),
    "preserve": ("preserve", "preserves", "retains", "retained", "keep", "keeps"),
    "summaries": ("summary", "summaries", "compressed", "compaction"),
}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _project_dir(workspace: str, project_id: str) -> Path:
    return (workspace_root(workspace) / "share/mind/governance/research_learning/projects"
            / safe_id(project_id))


def _resolved_source(raw: str) -> Path:
    path = Path(str(raw or "")).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"research source does not exist: {path}")
    if not any(path == root.resolve() or root.resolve() in path.parents
               for root in _ALLOWED_SOURCE_ROOTS):
        raise ValueError(f"research source is outside allowed roots: {path}")
    return path


def _source_identity(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    identity: dict[str, Any] = {
        "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw), "kind": "paper" if path.suffix.lower() == ".pdf" else "code",
    }
    for parent in path.parents:
        git = parent / ".git"
        if git.exists():
            identity["repository_root"] = str(parent)
            try:
                head = (git / "HEAD").read_text(encoding="utf-8").strip()
                if head.startswith("ref:"):
                    ref = head.split(":", 1)[1].strip()
                    identity["git_revision"] = (git / ref).read_text(encoding="utf-8").strip()
                else:
                    identity["git_revision"] = head
            except OSError:
                pass
            break
    return identity


def _read_source(path: Path, *, max_chars: int = 600_000) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            raw_pages = []
            for page in PdfReader(str(path)).pages[:40]:
                raw_pages.append(page.extract_text() or "")
                if sum(len(value) for value in raw_pages) >= max_chars:
                    break
            page_lines = [[line.strip() for line in value.splitlines()]
                          for value in raw_pages]
            page_frequency = Counter(line for lines in page_lines for line in set(lines)
                                     if len(line) >= 20)
            parts = []
            for page_index, lines in enumerate(page_lines):
                # Keep the first title occurrence but remove repeated running
                # headers from later pages.  Otherwise title words create
                # false matches in unrelated sections.
                parts.append("\n".join(
                    line for line in lines
                    if not (page_index > 0 and page_frequency.get(line, 0) >= 3)
                ))
            text = "\n\n".join(parts)[:max_chars]
            # PDF text is already an extraction, not a byte-for-byte quote.
            # Repair layout-only hyphenation/newlines so evidence windows are
            # readable while retaining the actual extracted words.
            text = re.sub(r"([A-Za-z])[-‐]\s*\n\s*([A-Za-z])", r"\1\2", text)
            text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
            return text
        except Exception as exc:  # a corrupt paper is evidence, not a crash loop
            raise ValueError(f"cannot extract PDF text from {path}: {exc}") from exc
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError as exc:
        raise ValueError(f"cannot read source {path}: {exc}") from exc


def read_research_source_text(path: str | Path, *, max_chars: int = 600_000) -> str:
    """Return the canonical text representation used for research evidence.

    PDF evidence is validated against extracted text rather than raw PDF
    bytes.  Keeping this transformation shared prevents acquisition and the
    downstream truth gate from disagreeing about quote membership.
    """
    return _read_source(Path(path).resolve(), max_chars=max_chars)


def normalize_research_evidence_text(value: str) -> str:
    """Canonicalize layout whitespace without changing extracted words."""
    typography = str(value or "").translate(str.maketrans({
        "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-",
    }))
    return re.sub(r"\s+", " ", typography).strip()


def _tokens(text: str) -> list[str]:
    values = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    return [value for value in values if value not in _STOPWORDS]


def _query_terms(question: str) -> list[str]:
    counts = Counter(_tokens(question))
    return [value for value, _ in counts.most_common(12)]


def _coverage(text_counts: Counter[str], terms: list[str]) -> float:
    if not terms:
        return 0.0
    return sum(min(1.0, math.log1p(text_counts.get(term, 0)) / math.log(3))
               for term in terms) / len(terms)


def _term_present(text: str, term: str) -> bool:
    if re.fullmatch(r"[a-z][a-z0-9_\-]*", term):
        return re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", text) is not None
    return term in text


def _concept_present(text: str, term: str) -> bool:
    return any(_term_present(text, alias) for alias in _CONCEPT_ALIASES.get(term, (term,)))


def observe_research_project(workspace: str, *, project_id: str, goal: str,
                             questions: list[str], source_paths: list[str]) -> dict[str, Any]:
    """Fingerprint real sources and persist a bounded research manifest."""
    if not str(project_id).strip() or not str(goal).strip():
        return {"ok": False, "status": "invalid_project_contract",
                "error": "project_id and goal are required", "production_effective": False}
    clean_questions = [str(value).strip() for value in questions if str(value).strip()]
    if not clean_questions:
        return {"ok": False, "status": "no_research_questions",
                "error": "at least one falsifiable research question is required",
                "production_effective": False}
    try:
        sources = [_resolved_source(value) for value in source_paths]
    except ValueError as exc:
        return {"ok": False, "status": "source_validation_failed", "error": str(exc),
                "production_effective": False}
    if len(sources) < 2:
        return {"ok": False, "status": "insufficient_source_diversity",
                "error": "at least two real sources are required", "production_effective": False}

    inventory = []
    for path in sources:
        text = _read_source(path)
        counts = Counter(_tokens(text))
        identity = _source_identity(path)
        identity.update({
            "extractable_chars": len(text),
            "term_counts": {term: counts.get(term, 0)
                            for question in clean_questions for term in _query_terms(question)
                            if counts.get(term, 0)},
        })
        inventory.append(identity)
    manifest = {
        "schema_version": 1, "project_id": safe_id(project_id), "goal": goal.strip(),
        "created_at": now_iso(), "questions": clean_questions, "sources": inventory,
        "status": "observed", "production_mutation": False, "production_effective": False,
    }
    manifest["evidence_digest"] = hashlib.sha256(json.dumps(
        {"questions": clean_questions, "sources": inventory}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    directory = _project_dir(workspace, project_id)
    path = directory / "manifest.json"
    atomic_json(path, manifest)
    append_jsonl(directory / "events.jsonl", {"event": "observe", **manifest})
    return {"ok": True, "status": "research_project_observed", "project_id": safe_id(project_id),
            "question_count": len(clean_questions), "source_count": len(inventory),
            "evidence_digest": manifest["evidence_digest"], "path": str(path),
            "files": [str(path)], "production_mutation": False, "production_effective": False,
            "content": f"已核验 {len(inventory)} 个真实来源并登记 {len(clean_questions)} 个知识问题。",
            "findings": [
                f"真实来源核验通过：{len(inventory)} 个；知识问题：{len(clean_questions)} 个",
                f"来源证据摘要：{manifest['evidence_digest'][:16]}",
            ]}


def select_research_query(workspace: str, *, project_id: str) -> dict[str, Any]:
    """Choose the next question/source pair by value of information."""
    directory = _project_dir(workspace, project_id)
    manifest = _json(directory / "manifest.json")
    if manifest.get("status") != "observed":
        return {"ok": False, "status": "research_manifest_missing",
                "production_effective": False}
    history = []
    try:
        history = [json.loads(line) for line in (directory / "investigations.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError, TypeError):
        history = []
    seen = {(str(row.get("question")), str(row.get("source_path"))) for row in history}
    options: list[ActiveLearningOption] = []
    meta: dict[str, dict[str, Any]] = {}
    belief = _json(directory / "latest_belief.json")
    stored_prior = belief.get("posterior") if isinstance(belief.get("posterior"), dict) else {}
    prior = {
        "mechanism_supported": float(stored_prior.get("mechanism_supported", .5)),
        "mechanism_not_supported": float(stored_prior.get("mechanism_not_supported", .5)),
    }
    for question_index, question in enumerate(manifest.get("questions") or []):
        terms = _query_terms(str(question))
        for source_index, source in enumerate(manifest.get("sources") or []):
            source_path = str(source.get("path") or "")
            counts = Counter({str(key): int(value) for key, value in
                              dict(source.get("term_counts") or {}).items()})
            relevance = _coverage(counts, terms)
            option_id = f"q{question_index + 1}_s{source_index + 1}"
            novelty = .05 if (str(question), source_path) in seen else 1.0
            signal = min(.95, .52 + .43 * relevance)
            options.append(ActiveLearningOption(
                option_id, "research_active_learning_investigate",
                {"mechanism_supported": {"evidence_found": signal, "not_found": 1.0 - signal},
                 "mechanism_not_supported": {"evidence_found": .08, "not_found": .92}},
                task_value=1.0, novelty=novelty,
                cost=min(.18, float(source.get("extractable_chars") or 0) / 4_000_000),
                risk=0.0, params={"project_id": safe_id(project_id),
                                  "question": str(question), "source_path": source_path},
                evidence_refs=(str(directory / "manifest.json"), source_path),
            ))
            meta[option_id] = {"lexical_relevance": relevance, "query_terms": terms}
    if not options:
        return {"ok": False, "status": "no_research_options", "production_effective": False}
    ranked = rank_active_learning_options(prior, options)
    for row in ranked:
        row.update(meta.get(str(row.get("option_id")), {}))
    selected = ranked[0]
    decision = {
        "schema_version": 1, "selection_id": "research_query_" + hashlib.sha256(
            json.dumps({"digest": manifest.get("evidence_digest"), "history": seen,
                        "ranked": ranked}, default=list, sort_keys=True).encode()).hexdigest()[:16],
        "created_at": now_iso(), "project_id": safe_id(project_id),
        "selector": "value_of_information_v1", "hypothesis_prior": prior,
        "selected": selected, "ranked_options": ranked,
        "production_mutation": False, "production_effective": False,
    }
    path = directory / "latest_selection.json"
    atomic_json(path, decision)
    append_jsonl(directory / "events.jsonl", {"event": "select", **decision})
    return {"ok": True, "status": "research_query_selected", "selection_id": decision["selection_id"],
            "selected": selected, "hypothesis_prior": prior,
            "path": str(path), "files": [str(path)],
            "production_mutation": False, "production_effective": False,
            "content": (f"主动学习已选择：{selected['params']['question']}；来源="
                        f"{selected['params']['source_path']}；信息价值分数="
                        f"{selected['acquisition_score']:.4f}。"),
            "findings": [
                f"主动选择问题：{selected['params']['question']}",
                f"选择来源：{selected['params']['source_path']}；VOI={selected['acquisition_score']:.4f}",
            ]}


def _best_evidence(text: str, terms: list[str]) -> tuple[str, list[str], float]:
    units = [value.strip() for value in re.split(r"\n\s*\n|(?<=[.!?。！？])\s+", text)
             if len(value.strip()) >= 30]
    # Two- and three-sentence windows preserve enough local semantics for a
    # claim while avoiding an entire PDF page masquerading as one quote.
    chunks = list(units)
    for width in (2, 3):
        chunks.extend(" ".join(units[index:index + width])
                      for index in range(max(0, len(units) - width + 1)))
    if not chunks:
        chunks = [line.strip() for line in text.splitlines() if line.strip()]
    scored = []
    for index, chunk in enumerate(chunks):
        lowered = chunk.lower()
        hits = [term for term in terms if _concept_present(lowered, term)]
        chars = max(1, len(chunk))
        prose_ratio = sum(value.isalnum() or value.isspace() or value in ",.;:()'-"
                          for value in chunk) / chars
        code_markers = sum(chunk.count(value) for value in ("←", "//", "{}", "=>", "=="))
        length_quality = (1.0 if 80 <= chars <= 1200 else
                          .75 if 40 <= chars <= 1800 else .55)
        # Referencing a figure inside a substantive paragraph is normal prose;
        # only a caption-like unit beginning with Figure/Table/Algorithm gets
        # the penalty.  The broad search caused a real JitRL methods paragraph
        # to miss the quality gate by 0.009.
        caption_penalty = .45 if re.match(
            r"^\s*(?:table|figure|algorithm)\s+\d", lowered,
        ) else 0.0
        quality = max(0.0, min(1.0, prose_ratio * length_quality
                               - .08 * code_markers - caption_penalty))
        primary_claim = bool(re.search(
            r"\b(?:we (?:introduce|propose|present|develop)|our (?:method|framework|approach))\b",
            lowered))
        score = len(set(hits)) * 10.0 + quality * 3.0 + (8.0 if primary_claim else 0.0)
        scored.append((score, len(set(hits)), quality, -index, chunk, hits))
    if not scored:
        return "", [], 0.0
    _, _, quality, _, chunk, hits = max(scored)
    return chunk[:1200], sorted(set(hits)), quality


def investigate_selected_query(workspace: str, *, project_id: str) -> dict[str, Any]:
    """Read the selected real source and persist a verbatim evidence record."""
    directory = _project_dir(workspace, project_id)
    selection = _json(directory / "latest_selection.json")
    selected = dict(selection.get("selected") or {})
    params = dict(selected.get("params") or {})
    if not params:
        return {"ok": False, "status": "research_selection_missing",
                "production_effective": False}
    try:
        source = _resolved_source(str(params.get("source_path") or ""))
        text = _read_source(source)
    except ValueError as exc:
        return {"ok": False, "status": "research_source_read_failed", "error": str(exc),
                "production_effective": False}
    question = str(params.get("question") or "")
    terms = _query_terms(question)
    quote, hits, evidence_quality = _best_evidence(text, terms)
    source_kind = "paper" if source.suffix.lower() == ".pdf" else "code"
    quality_threshold = .55 if source_kind == "paper" else .25
    evidence_found = (len(hits) >= min(2, max(1, len(terms))) and len(quote) >= 30
                      and evidence_quality >= quality_threshold)
    extractor_version = "semantic_alias_v2"
    record = {
        "schema_version": 1, "investigation_id": "research_evidence_" + hashlib.sha256(
            f"{selection.get('selection_id')}|{source}|{quote}|{extractor_version}".encode()
        ).hexdigest()[:16],
        "created_at": now_iso(), "project_id": safe_id(project_id),
        "selection_id": selection.get("selection_id"), "question": question,
        "source_path": str(source), "source_identity": _source_identity(source),
        "evidence_quote": quote, "matched_terms": hits,
        "evidence_quality": evidence_quality,
        "extractor_version": extractor_version,
        "evidence_mode": ("normalized_pdf_text" if source_kind == "paper"
                          else "source_code_excerpt"),
        "evidence_found": evidence_found,
        "support_type": "direct_source_excerpt" if evidence_found else "insufficient_lexical_evidence",
        "production_mutation": False, "production_effective": False,
    }
    likelihoods = dict(selected.get("outcome_likelihoods") or {})
    observation = "evidence_found" if evidence_found else "not_found"
    prior = dict(selection.get("hypothesis_prior") or {
        "mechanism_supported": .5, "mechanism_not_supported": .5,
    })
    unnormalized = {
        hypothesis: max(0.0, float(prior.get(hypothesis, 0.0)))
        * max(0.0, float(dict(likelihoods.get(hypothesis) or {}).get(observation, 0.0)))
        for hypothesis in ("mechanism_supported", "mechanism_not_supported")
    }
    mass = sum(unnormalized.values())
    posterior = ({key: value / mass for key, value in unnormalized.items()}
                 if mass > 0 else dict(prior))
    belief_update = {
        "schema_version": 1,
        "belief_update_id": "research_belief_" + hashlib.sha256(
            f"{record['investigation_id']}|{observation}".encode()).hexdigest()[:16],
        "created_at": now_iso(), "project_id": safe_id(project_id),
        "selection_id": selection.get("selection_id"), "observation": observation,
        "prior": prior, "likelihoods": likelihoods, "posterior": posterior,
        "production_mutation": False, "production_effective": False,
    }
    path = directory / "evidence" / f"{record['investigation_id']}.json"
    atomic_json(path, record)
    atomic_json(directory / "latest_belief.json", belief_update)
    append_jsonl(directory / "belief_updates.jsonl", belief_update)
    append_jsonl(directory / "investigations.jsonl", record)
    append_jsonl(directory / "events.jsonl", {"event": "investigate", **record})
    return {"ok": True, "status": "research_evidence_recorded", **record,
            "belief_update": belief_update,
            "path": str(path), "files": [str(path)],
            "content": (f"已真实读取 {source.name}；证据={'找到' if evidence_found else '不足'}；"
                        f"命中术语：{', '.join(hits) or '无'}；"
                        f"机制支持后验={posterior.get('mechanism_supported', 0.0):.3f}。"),
            "findings": [
                f"真实读取：{source.name}；证据={'找到' if evidence_found else '不足'}；质量={evidence_quality:.3f}；支持后验={posterior.get('mechanism_supported', 0.0):.3f}",
                f"证据摘录：{quote[:500]}",
            ]}


def run_research_selector_matched_experiment(workspace: str, *, project_id: str) -> dict[str, Any]:
    """Compare first-source baseline with the bounded information selector."""
    directory = _project_dir(workspace, project_id)
    manifest = _json(directory / "manifest.json")
    if manifest.get("status") != "observed":
        return {"ok": False, "status": "research_manifest_missing",
                "production_effective": False}
    sources = list(manifest.get("sources") or [])
    cases = []
    for question in manifest.get("questions") or []:
        terms = _query_terms(str(question))
        scores = []
        for source in sources:
            counts = Counter({str(key): int(value) for key, value in
                              dict(source.get("term_counts") or {}).items()})
            scores.append(_coverage(counts, terms))
        baseline_index = 0
        candidate_index = max(range(len(scores)), key=lambda index: (scores[index], -index))
        cases.append({
            "question": question, "query_terms": terms,
            "baseline_source": sources[baseline_index]["path"],
            "candidate_source": sources[candidate_index]["path"],
            "baseline_evidence_coverage": scores[baseline_index],
            "candidate_evidence_coverage": scores[candidate_index],
            "delta": scores[candidate_index] - scores[baseline_index],
        })
    baseline = sum(row["baseline_evidence_coverage"] for row in cases) / max(1, len(cases))
    candidate = sum(row["candidate_evidence_coverage"] for row in cases) / max(1, len(cases))
    no_regression = all(row["delta"] >= -1e-12 for row in cases)
    decision = ("accept_for_shadow" if candidate > baseline + .05 and no_regression
                else "inconclusive" if candidate >= baseline and no_regression else "reject")
    candidate_id = "candidate_research_source_selector_voi_v1"
    experiment = {
        "schema_version": 1, "experiment_id": "research_matched_" + hashlib.sha256(
            str(manifest.get("evidence_digest")).encode()).hexdigest()[:16],
        "created_at": now_iso(), "project_id": safe_id(project_id),
        "candidate_id": candidate_id, "intervention": "value_of_information_source_selection",
        "baseline": "first_unread_source", "cases": cases,
        "metrics": {"baseline_mean_evidence_coverage": baseline,
                    "candidate_mean_evidence_coverage": candidate,
                    "delta": candidate - baseline, "no_case_regression": no_regression},
        "decision": decision, "limits": [
            "lexical evidence coverage is not semantic correctness",
            "source-selection improvement is not production task improvement",
            "a real 04 project continuation canary is required before any production gate",
        ],
        "status": "candidate_shadow", "production_mutation": False,
        "production_effective": False, "promotion": False,
    }
    path = directory / "experiments" / f"{experiment['experiment_id']}.json"
    existing = _json(path)
    if existing.get("experiment_id") == experiment["experiment_id"]:
        return {"ok": True, **existing, "idempotent_replay": True,
                "path": str(path), "files": [str(path)],
                "content": (f"匹配实验已存在并复核：baseline={baseline:.3f}，candidate={candidate:.3f}，"
                            f"decision={decision}；production_effective=false。"),
                "findings": [
                    f"来源选择匹配实验复核：baseline={baseline:.3f}，candidate={candidate:.3f}，delta={candidate - baseline:.3f}",
                    f"Candidate 决策：{decision}；production_effective=false；promotion=false",
                ]}
    atomic_json(path, experiment)
    append_jsonl(directory / "events.jsonl", {"event": "matched_experiment", **experiment})
    return {"ok": True, "status": "research_selector_matched_complete", **experiment,
            "path": str(path), "files": [str(path)],
            "content": (f"匹配实验完成：baseline={baseline:.3f}，candidate={candidate:.3f}，"
                        f"decision={decision}；production_effective=false。"),
            "findings": [
                f"来源选择匹配实验：baseline={baseline:.3f}，candidate={candidate:.3f}，delta={candidate - baseline:.3f}",
                f"Candidate 决策：{decision}；production_effective=false；promotion=false",
            ]}
