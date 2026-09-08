"""Tiered, provenance-preserving context selection."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from .models import ContextSelection
from .storage import append_jsonl, governance_log, latest_receipt


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = REPO_ROOT / "docs" / "catalog.yaml"
MANDATORY_IDS = ("current_status", "self_awareness", "verification_rules")
AUTHORITY_SCORE = {"canonical": 40, "current": 30, "reference": 10, "historical": 0}


def load_catalog(path: str | Path = DEFAULT_CATALOG) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data.get("documents"), list):
        raise ValueError("catalog documents must be a list")
    seen: set[str] = set()
    for item in data["documents"]:
        doc_id = str(item.get("id") or "")
        if not doc_id or doc_id in seen:
            raise ValueError(f"invalid or duplicate catalog id: {doc_id}")
        if item.get("tier") not in {"L1", "L2", "L3", "L4"}:
            raise ValueError(f"invalid tier for {doc_id}")
        seen.add(doc_id)
    missing = set(MANDATORY_IDS) - seen
    if missing:
        raise ValueError(f"catalog missing mandatory documents: {sorted(missing)}")
    return data


def _tokens(text: str) -> set[str]:
    raw = str(text or "").lower()
    latin = re.findall(r"[a-z][a-z0-9_+-]{1,}", raw)
    chinese = re.findall(r"[一-鿿]{2,}", raw)
    # Long Chinese runs need overlapping fragments to match labels such as 小红书.
    fragments: list[str] = []
    for token in chinese:
        fragments.append(token)
        fragments.extend(token[i:i + 3] for i in range(max(0, len(token) - 2)))
    return set(latin + fragments)


def _eligible(item: dict[str, Any], instance: str, allow_history: bool) -> bool:
    if item.get("tier") == "L4" and not allow_history:
        return False
    instances = {str(value) for value in item.get("instances") or ["all"]}
    return "all" in instances or not instance or instance in instances


def _rank(item: dict[str, Any], query_tokens: set[str], requested: set[str]) -> tuple[int, str]:
    item_tokens = _tokens(" ".join(str(value) for value in item.get("tags") or []))
    overlap = len(query_tokens & item_tokens)
    score = AUTHORITY_SCORE.get(str(item.get("authority")), 0) + overlap * 20
    if item.get("id") in MANDATORY_IDS:
        score += 1000
    if item.get("id") in requested:
        score += 500
    if item.get("default_load") is False:
        score -= 25
    return score, str(item.get("id"))


def _parse_llm_ids(raw: str, allowed: set[str]) -> list[str]:
    match = re.search(r"\[[\s\S]*?\]", raw or "")
    if not match:
        return []
    try:
        values = json.loads(match.group(0))
    except ValueError:
        return []
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        value = str(value)
        if value in allowed and value not in result:
            result.append(value)
    return result


def select_context(
    workspace: str,
    query: str,
    *,
    instance_id: str = "",
    project_id: str = "",
    budget_chars: int = 16000,
    requested_ids: list[str] | None = None,
    boosted_ids: list[str] | None = None,
    reserve_receipt_chars: int = 0,
    allow_history: bool = False,
    semantic_selector: Callable[[str], str] | None = None,
    catalog_path: str | Path = DEFAULT_CATALOG,
) -> tuple[ContextSelection, str]:
    """Select context and return a trace record plus a provenance-marked bundle."""
    budget_chars = max(1000, min(int(budget_chars), 100_000))
    catalog = load_catalog(catalog_path)
    requested = {str(value) for value in requested_ids or []}
    boosted = {str(value) for value in boosted_ids or []}
    query_tokens = _tokens(query)
    eligible = [item for item in catalog["documents"] if _eligible(item, instance_id, allow_history)]
    # A bounded boost changes ranking without giving cognition-derived IDs the
    # hard priority of an explicit user request.  Default callers are exactly
    # unchanged; Gate C uses this only in its isolated candidate route.
    eligible.sort(
        key=lambda item: (_rank(item, query_tokens, requested)[0]
                          + (10 if str(item.get("id")) in boosted else 0),
                          _rank(item, query_tokens, requested)[1]),
        reverse=True,
    )

    llm_ids: list[str] = []
    if semantic_selector and eligible:
        summary = [
            {"id": item["id"], "tier": item["tier"], "tags": item.get("tags", []),
             "authority": item.get("authority", "reference")}
            for item in eligible if item.get("tier") != "L4" or allow_history
        ]
        prompt = (
            "Select only document IDs needed for the task. Mandatory L1 documents are added separately. "
            "Return a JSON array of IDs, with no explanation.\n"
            f"instance={instance_id} project={project_id} task={query[:1500]}\n"
            f"catalog={json.dumps(summary, ensure_ascii=False)}"
        )
        llm_ids = _parse_llm_ids(semantic_selector(prompt), {str(item["id"]) for item in eligible})

    priority = list(MANDATORY_IDS) + list(requested) + llm_ids + [str(item["id"]) for item in eligible]
    ordered_ids: list[str] = []
    for doc_id in priority:
        if doc_id not in ordered_ids:
            ordered_ids.append(doc_id)
    by_id = {str(item["id"]): item for item in eligible}
    has_targeted_context = bool(requested or boosted or llm_ids or any(_rank(item, query_tokens, requested)[0] > 40 for item in eligible if item.get("id") not in MANDATORY_IDS))
    mandatory_cap = max(800, int(budget_chars * (0.65 if has_targeted_context else 1.0) / len(MANDATORY_IDS)))

    receipt = latest_receipt(workspace, project_id) if project_id else None
    reserved = max(0, min(int(reserve_receipt_chars), budget_chars)) if receipt else 0
    document_budget = budget_chars - reserved
    selected: list[dict[str, Any]] = []
    chunks: list[str] = []
    used = 0
    for doc_id in ordered_ids:
        item = by_id.get(doc_id)
        if not item:
            continue
        # Non-mandatory unrelated documents do not consume the remaining budget.
        score, _ = _rank(item, query_tokens, requested)
        if (doc_id not in MANDATORY_IDS and doc_id not in requested and doc_id not in boosted
                and doc_id not in llm_ids and score <= 40):
            continue
        path = (REPO_ROOT / str(item["path"])).resolve()
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            if doc_id in MANDATORY_IDS:
                raise ValueError(f"mandatory context missing: {path}")
            continue
        prefix = f"\n<!-- context:{doc_id} source:{item['path']} tier:{item['tier']} -->\n"
        suffix = "\n"
        remaining = document_budget - used - len(prefix) - len(suffix)
        if remaining <= 0:
            break
        per_doc_limit = int(item.get("max_chars") or remaining)
        if doc_id in MANDATORY_IDS:
            per_doc_limit = min(per_doc_limit, mandatory_cap)
        limit = min(per_doc_limit, remaining)
        clipped = content[:limit]
        reason = "mandatory L1" if doc_id in MANDATORY_IDS else (
            "explicit request" if doc_id in requested else "semantic selection" if doc_id in llm_ids
            else "cognition soft boost" if doc_id in boosted else "tag/rule match"
        )
        selected.append({
            "document_id": doc_id,
            "path": str(item["path"]),
            "tier": str(item["tier"]),
            "reason": reason,
            "chars": len(clipped),
        })
        rendered = f"{prefix}{clipped}{suffix}"
        chunks.append(rendered)
        # Budget the exact serialized bundle, including provenance markers.
        # Previously only body text was counted, so every bundle exceeded its
        # declared cap by the wrapper length.
        used += len(rendered)

    if receipt and used < budget_chars:
        raw = json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2)
        prefix = "\n<!-- context:latest_receipt tier:L3 -->\n```json\n"
        suffix = "\n```\n"
        receipt_limit = min(max(0, budget_chars - used - len(prefix) - len(suffix)),
                            reserved or max(0, budget_chars - used - len(prefix) - len(suffix)))
        clipped = raw[:receipt_limit]
        if clipped:
            selected.append({
                "document_id": f"latest_receipt:{receipt.receipt_id}",
                "path": f"runtime://projects/{project_id}/latest_receipt",
                "tier": "L3",
                "reason": "latest project handoff",
                "chars": len(clipped),
            })
            rendered = f"{prefix}{clipped}{suffix}"
            chunks.append(rendered)
            used += len(rendered)

    selection = ContextSelection(
        query=query,
        instance_id=instance_id,
        project_id=project_id,
        selected=selected,
        budget_chars=budget_chars,
        used_chars=used,
    )
    append_jsonl(governance_log(workspace, "context_selections"), selection.to_dict())
    return selection, "".join(chunks).strip()

# 03_instance_selfdrive_marker 2026-09-05T20:00:06.588569+08:00
