"""Frozen, reproducible protocol helpers for the Partner v5 research study."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import csv
import json
import math
import random
import statistics

ARMS = [
    "single_turn_llm", "partner_no_memory", "read_not_consumed",
    "memory_consumed", "learning_without_evolution", "full_partner",
    "full_partner_jev", "full_partner_world_model_shadow",
]


def digest(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(body.encode()).hexdigest()


def validate_study(value: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(value.get("benchmark_version")) != "5.0":
        errors.append("benchmark_version must be 5.0")
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or len(tasks) < 4:
        return errors + ["v5 requires at least four frozen project tasks"]
    ids = [str(row.get("task_id") or "") for row in tasks if isinstance(row, Mapping)]
    domains = {str(row.get("domain") or "") for row in tasks if isinstance(row, Mapping)}
    if len(ids) != len(tasks) or not all(ids) or len(set(ids)) != len(ids):
        errors.append("task_id must be present and unique")
    if len(domains - {""}) < 4:
        errors.append("v5 requires at least four independent project domains")
    development = set(map(str, (value.get("holdout_policy") or {}).get("development_ids") or []))
    holdout = {str(row.get("holdout_id") or "") for row in tasks}
    if "" in holdout or development & holdout:
        errors.append("holdout ids must exist and be disjoint from development ids")
    for row in tasks:
        if list(row.get("arms") or []) != ARMS:
            errors.append(f"{row.get('task_id')} arm contract mismatch")
        for key in ("source_path", "artifact_path", "baseline_candidate", "learned_candidate"):
            if not row.get(key):
                errors.append(f"{row.get('task_id')} missing {key}")
        candidates = row.get("candidates") or {}
        if row.get("baseline_candidate") not in candidates or row.get("learned_candidate") not in candidates:
            errors.append(f"{row.get('task_id')} candidate references are invalid")
        direction = str(row.get("direction") or "")
        if direction not in {"higher_is_better", "lower_is_better"}:
            errors.append(f"{row.get('task_id')} invalid direction")
    hypotheses = value.get("hypotheses") or {}
    if set(hypotheses) != {"H1", "H2", "H3", "H4"}:
        errors.append("v5 must preregister H1-H4")
    review = value.get("human_review") or {}
    if int(review.get("reviewer_count") or 0) != 2 or not review.get("blind"):
        errors.append("v5 requires a frozen blinded two-human protocol")
    registry_path = Path(str(value.get("corpus_registry_path") or ""))
    if not registry_path.is_file():
        errors.append("v5 requires a frozen corpus_registry_path")
    return errors


def load_and_validate(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_study(value)
    if errors:
        raise ValueError("; ".join(errors))
    return value


def _json_pointer(value: Any, pointer: str) -> Any:
    current = value
    for part in pointer.strip("/").split("/") if pointer.strip("/") else []:
        token = part.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def resolve_metric(artifact_path: str | Path, selector: Mapping[str, Any]) -> float:
    path = Path(artifact_path)
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        return float(_json_pointer(value, str(selector.get("json_pointer") or "")))
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        where = dict(selector.get("where") or {})
        match = next(row for row in rows if all(str(row.get(k)) == str(v) for k, v in where.items()))
        return float(match[str(selector["column"])])
    raise ValueError(f"unsupported artifact type: {path.suffix}")


def effect(baseline: float, candidate: float, direction: str) -> float:
    return candidate - baseline if direction == "higher_is_better" else baseline - candidate


def bootstrap_mean(values: list[float], *, seed: int, samples: int = 2000) -> dict[str, Any]:
    if not values:
        return {"mean": None, "lower_95": None, "upper_95": None, "n": 0}
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(samples))
    return {"mean": statistics.mean(values), "lower_95": means[int(.025*samples)],
            "upper_95": means[min(samples-1, int(.975*samples))], "n": len(values),
            "seed": seed, "samples": samples}


def cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float | None:
    if not labels_a or len(labels_a) != len(labels_b):
        return None
    labels = sorted(set(labels_a) | set(labels_b))
    observed = sum(a == b for a, b in zip(labels_a, labels_b)) / len(labels_a)
    expected = sum((labels_a.count(label)/len(labels_a)) *
                   (labels_b.count(label)/len(labels_b)) for label in labels)
    return 1.0 if math.isclose(expected, 1.0) and math.isclose(observed, 1.0) else (
        (observed - expected) / (1 - expected) if not math.isclose(expected, 1.0) else 0.0)


def score_annotation_files(packet: Mapping[str, Any], paths: list[str]) -> dict[str, Any]:
    if len(paths) != 2 or any(not Path(path).is_file() for path in paths):
        return {"status": "awaiting_two_humans", "complete": False, "cohen_kappa": None,
                "reviewer_count": sum(Path(path).is_file() for path in paths)}
    rows = [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
    ids = [str(item["item_id"]) for item in packet.get("items") or []]
    mappings = [{str(item["item_id"]): str(item["label"]) for item in row.get("labels") or []}
                for row in rows]
    if any(set(mapping) != set(ids) for mapping in mappings):
        return {"status": "invalid_annotation_set", "complete": False, "cohen_kappa": None,
                "reviewer_count": 2}
    a, b = ([mapping[item_id] for item_id in ids] for mapping in mappings)
    return {"status": "complete", "complete": True, "cohen_kappa": cohen_kappa(a, b),
            "agreement_rate": sum(x == y for x, y in zip(a, b))/len(ids), "reviewer_count": 2}
