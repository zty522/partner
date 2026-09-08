"""Typed output reference and safe path resolution.

ADR 0043 / Hermes 04 manual-failure repair (P0):

The 2026-08-30 04 manual task ``d2cb657c-...`` proved that downstream events
must consume upstream-generated files via typed references
(``$step7.result.path`` / ``$step7.files[0]``) and that relative
``source_path`` / ``output_path`` must resolve only inside the
TaskInstance working directory.  The legacy Planner could leave a PDF
Event holding a bare relative path such as ``harness_comparison.md`` that
the PDF handler could not resolve, returning ``no content (provide content
or source_path)`` and being retried three times identically.

This module is the deterministic contract that gates downstream Events on:

* typed reference resolution from prior step results;
* relative-path sandboxing inside the current TaskInstance working
  directory, blocking ``..`` escape;
* explicit ``failure_owner`` classification so internal planning / event
  bugs are NOT mis-attributed to ``user_input``;
* deterministic short-circuiting of same-error + same-parameter retries
  before they enter the executor (one bounded structured recovery attempt
  is allowed when fresh upstream artifacts are materialised).

It never bypasses the Event runtime; every downstream Event still runs
through its deterministic handler with the resolved parameters.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

JsonDict = dict[str, Any]


# Failure-owner taxonomy used by manual-failure repair (P0-A.6).
FAILURE_OWNER_USER_INPUT = "user_input"
FAILURE_OWNER_PLANNER_CONTRACT = "planner_contract"
FAILURE_OWNER_OUTPUT_REFERENCE = "output_reference"
FAILURE_OWNER_EVENT_HANDLER = "event_handler"
FAILURE_OWNER_ENVIRONMENT = "environment"
FAILURE_OWNER_DELIVERY = "delivery"
FAILURE_OWNER_INPUT_STATE = "input_state"

# Mechanism-level failure signatures (P1-D.2) used so the active-learning
# selector can talk about repairable classes instead of one generic
# ``tool.failed`` bucket.
MECHANISM_TYPED_REFERENCE_UNRESOLVED = (
    "planning.output_reference_contract/typed_reference_unresolved"
)
MECHANISM_RELPATH_OUTSIDE_WORKDIR = (
    "planning.output_reference_contract/relative_path_outside_workdir"
)
MECHANISM_SAME_PARAMS_RETRY_LOOP = "planning.retry/same_parameters_retry_loop"
MECHANISM_PDF_MISSING_AFTER_RECOVERY = (
    "planning.artifact_contract/pdf_missing_after_recovery"
)
MECHANISM_USER_MESSAGE_OK_INTERNAL_FAIL = (
    "user_input/intake_ok_internal_reference_broken"
)
MECHANISM_EXPECTED_MISSING_INPUT = "observation/expected_missing_input"

_STEP_RESULT_FILE_PREFIX = "_step_"
_STEP_RESULT_FILE_SUFFIX = ".result.json"


def is_typed_reference(value: Any) -> bool:
    """Return True if value looks like a Partner typed reference.

    Accepts ``$stepN.result.path`` / ``$step_7.files[0]`` style strings
    and the explicit ``$step`` prefix.  Single-character ``$x`` markers
    are not treated as references so they remain free-form text.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text.startswith("$"):
        return False
    if text in {"$", "$$", "$0", "$1"}:
        return False
    return ("." in text) or ("[" in text) or text.startswith("$step")


def _strip_dollar(text: str) -> str:
    return text[1:].strip() if text.startswith("$") else text.strip()


def _normalize_ref(ref: str) -> str:
    ref = ref.strip()
    if ref.startswith("$"):
        ref = ref[1:]
    if not ref:
        return ""
    if re.fullmatch(r"[0-9]+", ref):
        return ""
    if ref.startswith("step_"):
        return ref
    if ref.startswith("step") and ref[4:].isdigit():
        return "step_" + ref[4:]
    return ref


@dataclass
class TypedResolution:
    ok: bool
    value: Any = None
    step_id: str = ""
    failure_owner: str = ""
    mechanism: str = ""
    resolved_path: str = ""
    resolved_inside_workdir: bool = False
    error: str = ""

    def as_dict(self) -> JsonDict:
        return {
            "ok": self.ok,
            "value": self.value,
            "step_id": self.step_id,
            "failure_owner": self.failure_owner,
            "mechanism": self.mechanism,
            "resolved_path": self.resolved_path,
            "resolved_inside_workdir": self.resolved_inside_workdir,
            "error": self.error,
        }


def _resolve_step_value(step_id: str, results: JsonDict) -> Any:
    """Return the canonical value for a step id like ``step7``.

    ``results`` mirrors the harness ``step_results`` dict.  Each value
    is a per-step record carrying ``result.path`` (convention) or
    ``files`` lists.  Looks up both ``stepN`` and ``step_N`` forms.
    """
    if not step_id or not isinstance(results, dict):
        return None
    candidates = [step_id]
    if step_id.startswith("step_"):
        candidates.append("step" + step_id[len("step_"):])
    elif step_id.startswith("step") and step_id[4:].isdigit():
        candidates.append("step_" + step_id[4:])
    for cand in candidates:
        container = results.get(cand) or {}
        if isinstance(container, dict):
            for key in ("result", "parsed"):
                inner = container.get(key)
                if isinstance(inner, dict) and ("path" in inner or "files" in inner):
                    return inner
    return None


def resolve_typed_reference(
    reference: Any,
    *,
    results: JsonDict,
    working_dir: str = "",
) -> TypedResolution:
    """Resolve a typed reference like ``$step7.result.path``.

    Always returns a :class:`TypedResolution`; never raises.  When ``ok``
    is True the ``value`` carries whatever the upstream step produced; a
    string path is also normalised through :func:`resolve_relative_path`
    and the ``resolved_inside_workdir`` flag surfaces the safety result.
    """
    if not is_typed_reference(reference):
        return TypedResolution(
            ok=False,
            error="not a typed reference",
            failure_owner=FAILURE_OWNER_PLANNER_CONTRACT,
            mechanism=MECHANISM_TYPED_REFERENCE_UNRESOLVED,
        )
    ref_body = _strip_dollar(str(reference))
    head, _, tail = ref_body.partition(".")
    step_id = _normalize_ref(head)
    if not step_id:
        return TypedResolution(
            ok=False,
            error=f"unsupported typed reference: {reference!r}",
            failure_owner=FAILURE_OWNER_PLANNER_CONTRACT,
            mechanism=MECHANISM_TYPED_REFERENCE_UNRESOLVED,
        )

    # _resolve_step_value already returned the inner dict (e.g.
    # {"path": "...", "files": [...]}).  For ``$step7.result.path`` the tail
    # is ``result.path``; we transparently strip a leading ``result`` /
    # ``parsed`` so the user can write it either way.
    parts = [p for p in tail.split(".") if p] if tail else []
    if parts and parts[0] in ("result", "parsed"):
        parts = parts[1:]
    inner = _resolve_step_value(step_id, results)
    if inner is None:
        return TypedResolution(
            ok=False,
            step_id=step_id,
            error=f"step {step_id!r} has no usable result",
            failure_owner=FAILURE_OWNER_OUTPUT_REFERENCE,
            mechanism=MECHANISM_TYPED_REFERENCE_UNRESOLVED,
        )
    value: Any = inner
    for part in parts:
        if value is None:
            break
        if isinstance(value, dict):
            name, idx = _parse_index(part)
            if name and name in value:
                value = value.get(name)
            else:
                return TypedResolution(
                    ok=False,
                    step_id=step_id,
                    error=f"step {step_id!r} has no field {part!r}",
                    failure_owner=FAILURE_OWNER_OUTPUT_REFERENCE,
                    mechanism=MECHANISM_TYPED_REFERENCE_UNRESOLVED,
                )
            if idx is not None and isinstance(value, list):
                if idx >= len(value):
                    return TypedResolution(
                        ok=False,
                        step_id=step_id,
                        error=f"index {idx} out of range for {part}",
                        failure_owner=FAILURE_OWNER_OUTPUT_REFERENCE,
                        mechanism=MECHANISM_TYPED_REFERENCE_UNRESOLVED,
                    )
                value = value[idx]
        elif isinstance(value, list):
            name, idx = _parse_index(part)
            if idx is None and name.isdigit():
                idx = int(name)
            if idx is None or idx >= len(value):
                return TypedResolution(
                    ok=False,
                    step_id=step_id,
                    error=f"index {part} out of range",
                    failure_owner=FAILURE_OWNER_OUTPUT_REFERENCE,
                    mechanism=MECHANISM_TYPED_REFERENCE_UNRESOLVED,
                )
            value = value[idx]
        else:
            return TypedResolution(
                ok=False,
                step_id=step_id,
                error=f"cannot index into {type(value).__name__}",
                failure_owner=FAILURE_OWNER_OUTPUT_REFERENCE,
                mechanism=MECHANISM_TYPED_REFERENCE_UNRESOLVED,
            )

    if value is None:
        return TypedResolution(
            ok=False,
            step_id=step_id,
            error=f"step {step_id!r} returned no typed value",
            failure_owner=FAILURE_OWNER_OUTPUT_REFERENCE,
            mechanism=MECHANISM_TYPED_REFERENCE_UNRESOLVED,
        )

    resolved_path = value if isinstance(value, str) else ""
    inside = False
    if resolved_path and working_dir:
        resolved_path, inside = resolve_relative_path(
            resolved_path, working_dir=working_dir, must_exist=False
        )
    return TypedResolution(
        ok=True,
        value=value,
        step_id=step_id,
        resolved_path=resolved_path,
        resolved_inside_workdir=inside,
    )


def _parse_index(tail: str) -> tuple[str, int | None]:
    match = re.search(r"\[(\d+)\]\s*$", tail)
    if match:
        return tail[: match.start()].rstrip("."), int(match.group(1))
    return tail, None


def resolve_relative_path(
    path: str,
    *,
    working_dir: str,
    must_exist: bool = False,
) -> tuple[str, bool]:
    """Resolve ``path`` strictly inside ``working_dir``.

    Returns ``(absolute_path, inside)``.  Absolute paths outside the working
    directory are returned unchanged but flagged ``inside=False`` so the
    caller can decide.  Relative paths that escape with ``..`` are blocked
    and flagged ``inside=False`` *without* resolving to an absolute form
    outside the working directory.  When ``must_exist=True``, a non-existent
    target returns ``(path, False)``.
    """
    if not isinstance(path, str) or not path.strip():
        return ("", False)
    path = path.strip()
    if not working_dir:
        return (path, False)
    workdir_abs = os.path.abspath(working_dir)
    if os.path.isabs(path):
        normalized = os.path.normpath(path)
        inside = normalized == workdir_abs or normalized.startswith(
            workdir_abs + os.sep
        )
        if must_exist and not os.path.exists(normalized):
            return (path, False)
        return (normalized, inside)
    if path.startswith("/"):
        return (path, False)
    if path.split("/", 1)[0] == ".." or path.startswith(".."):
        return (path, False)
    candidate = os.path.normpath(os.path.join(workdir_abs, path))
    inside = candidate == workdir_abs or candidate.startswith(workdir_abs + os.sep)
    if must_exist and not os.path.exists(candidate):
        return (path, False)
    return (candidate, inside)


def classify_failure_owner(
    *,
    error: str,
    event_type: str,
    has_user_provided_inputs: bool,
    typed_reference_resolved: bool,
) -> tuple[str, str]:
    """Return ``(failure_owner, mechanism)`` for a downstream failure.

    Deliberately narrow so we never widen the contract in a way that
    mis-attributes bugs to the user.  The PDF ``no content`` error is the
    only path where we use ``output_reference``; everything else falls
    through to ``event_handler``.
    """
    err_lower = (error or "").lower()
    if event_type in {"atomic_inspect_file", "read_file"} and any(
        token in err_lower for token in ("file_not_found", "file not found", "no such file", "预期不存在")
    ):
        return (FAILURE_OWNER_USER_INPUT, MECHANISM_EXPECTED_MISSING_INPUT)
    pdf_failure = event_type in {
        "generate_detailed_pdf",
        "generate_pdf",
        "atomic_generate_pdf",
        "atomic_convert_md_to_pdf",
    } and "no content" in err_lower
    if pdf_failure:
        if not has_user_provided_inputs:
            return (
                FAILURE_OWNER_PLANNER_CONTRACT,
                MECHANISM_TYPED_REFERENCE_UNRESOLVED,
            )
        # Even when source_path is set, if it does not actually resolve to
        # a real upstream artifact the root cause is the typed reference /
        # planner contract; never blame the user.
        if not typed_reference_resolved:
            return (
                FAILURE_OWNER_OUTPUT_REFERENCE,
                MECHANISM_TYPED_REFERENCE_UNRESOLVED,
            )
        # source_path present, event_type is PDF, content still empty
        # means the resolver path failed somewhere; still output_reference.
        return (
            FAILURE_OWNER_OUTPUT_REFERENCE,
            MECHANISM_TYPED_REFERENCE_UNRESOLVED,
        )
    if "outside" in err_lower or "sandboxed" in err_lower:
        return (
            FAILURE_OWNER_OUTPUT_REFERENCE,
            MECHANISM_RELPATH_OUTSIDE_WORKDIR,
        )
    return (FAILURE_OWNER_EVENT_HANDLER, "")


def normalize_params_for_retry_check(
    event_type: str, params: JsonDict
) -> str:
    """Return a normalized fingerprint of retry-affecting params.

    Used by the deterministic-retry short-circuit.  We keep only the keys
    that influence step outcome and stringify leaves so identity across
    attempts implies "same parameters, same code path" -- which means
    identical outcome.
    """
    if not isinstance(params, dict):
        return json.dumps(params, sort_keys=True, default=str)
    keep_keys = (
        "source_path",
        "input_path",
        "content",
        "path",
        "filename",
        "output_path",
        "title",
        "quality_profile",
    )
    bits = []
    for key in keep_keys:
        if key in params:
            value = params[key]
            if isinstance(value, str):
                bits.append(f"{key}={value.strip()}")
            else:
                bits.append(f"{key}={value!r}")
    bits.append(f"event={event_type}")
    return "|".join(bits)


@dataclass
class RetryDecision:
    allow: bool
    reason: str
    failure_owner: str = ""
    mechanism: str = ""
    error_signature: str = ""


def decide_step_retry(
    *,
    event_type: str,
    params: JsonDict,
    error: str,
    previous_attempts: Iterable[JsonDict],
) -> RetryDecision:
    """Decide whether to retry a failed step.

    Refuses retry when a prior attempt with the same normalized fingerprint
    and same error signature is present -- there is no new evidence or
    strategy so the same outcome will repeat.  Pass-through otherwise.
    """
    fingerprint = normalize_params_for_retry_check(event_type, params)
    err_signature = (error or "").strip()
    prev = list(previous_attempts or [])
    same_loop = any(
        str(att.get("_fingerprint", "")) == fingerprint
        and str(att.get("_error_signature", "")) == err_signature
        for att in prev
    )
    if same_loop:
        owner, mech = classify_failure_owner(
            error=error,
            event_type=event_type,
            has_user_provided_inputs=True,
            typed_reference_resolved=bool(
                params.get("source_path") or params.get("input_path")
            ),
        )
        return RetryDecision(
            allow=False,
            reason="same parameters + same error signature; deterministic short-circuit",
            failure_owner=owner,
            mechanism=mech or MECHANISM_SAME_PARAMS_RETRY_LOOP,
            error_signature=err_signature,
        )
    return RetryDecision(
        allow=True,
        reason="",
        error_signature=err_signature,
    )


def resolve_pdf_source(
    *,
    params: JsonDict,
    step_id: str,
    results: JsonDict,
    working_dir: str,
    event_type: str = "",
) -> tuple[JsonDict, JsonDict]:
    """Fill ``params['source_path']`` for PDF-producing events.

    Priority order:

    1. Absolute / inside-workdir ``params['source_path']`` already set.
    2. Typed reference ``$stepN.result.path`` / ``$stepN.files[0]``.
    3. ``dependencies`` scan + on-disk fallback for ``_step_*.result.json``.

    Returns ``(updated_params, audit)`` where ``audit`` carries the
    ``failure_owner`` / mechanism when the source could not be filled.
    """
    audit: JsonDict = {
        "step_id": step_id,
        "original_source_path": params.get("source_path"),
        "fills": [],
        "resolved": False,
    }
    if not isinstance(params, dict):
        return params, audit

    workdir_abs = os.path.abspath(working_dir) if working_dir else ""

    # 1. Honor existing absolute or workingdir-relative path that exists.
    current = params.get("source_path") or params.get("input_path") or ""
    if isinstance(current, str) and current.strip():
        candidate, inside = resolve_relative_path(
            current, working_dir=working_dir, must_exist=False
        )
        if current.startswith("$"):
            pass  # typed reference; fall through to typed branch
        elif os.path.exists(candidate):
            updated = dict(params)
            updated["source_path"] = candidate
            audit["resolved"] = True
            audit["fill_source"] = "existing_source_path"
            return updated, audit
        elif inside:
            audit["fills"].append(
                {
                    "source": "existing_source_path",
                    "path": candidate,
                    "exists": False,
                }
            )
    elif not current:
        # Empty source: try typed references inside params.
        for key in ("source_path", "input_path", "path"):
            value = params.get(key)
            if is_typed_reference(value):
                res = resolve_typed_reference(
                    value, results=results, working_dir=working_dir
                )
                if res.ok and isinstance(res.value, str):
                    updated = dict(params)
                    updated[key] = res.resolved_path or res.value
                    audit["resolved"] = True
                    audit["fill_source"] = f"typed_ref:{res.step_id}"
                    return updated, audit

    # 2. Try typed reference regardless (including when source_path empty).
    for key in ("source_path", "input_path", "path"):
        value = params.get(key)
        if is_typed_reference(value):
            res = resolve_typed_reference(
                value, results=results, working_dir=working_dir
            )
            if res.ok and isinstance(res.value, str):
                updated = dict(params)
                updated[key] = res.resolved_path or res.value
                audit["resolved"] = True
                audit["fill_source"] = f"typed_ref:{res.step_id}"
                return updated, audit

    # 3. Walk dependencies for any *.md produce path.  This mirrors the
    # legacy pdf_source_filled_from_dependency behavior for ALL pdf-like
    # events, not just atomic_convert_md_to_pdf.
    candidates: list[str] = []
    step_results = results or {}
    if isinstance(step_id, str) and step_results:
        container = step_results.get(step_id) or {}
        deps = []
        if isinstance(container, dict):
            raw_deps = container.get("depends_on") or []
            if isinstance(raw_deps, list):
                deps = [str(d) for d in raw_deps if isinstance(d, str)]
        for dep in deps:
            dep_result = step_results.get(dep) or {}
            if not isinstance(dep_result, dict):
                continue
            parsed = (
                dep_result.get("parsed")
                if isinstance(dep_result.get("parsed"), dict)
                else {}
            )
            for key in ("files", "path"):
                dep_files = parsed.get(key) or []
                if isinstance(dep_files, str):
                    dep_files = [dep_files]
                for f in dep_files:
                    if isinstance(f, str):
                        candidates.append(f)
            result_value = dep_result.get("result")
            if isinstance(result_value, dict):
                for key in ("files", "path"):
                    dep_files = result_value.get(key) or []
                    if isinstance(dep_files, str):
                        dep_files = [dep_files]
                    for f in dep_files:
                        if isinstance(f, str):
                            candidates.append(f)

    for f in candidates:
        if not isinstance(f, str):
            continue
        normalized, inside = resolve_relative_path(
            f, working_dir=working_dir, must_exist=True
        )
        if inside and os.path.isfile(normalized) and normalized.lower().endswith(".md"):
            updated = dict(params)
            updated["source_path"] = normalized
            audit["resolved"] = True
            audit["fill_source"] = f"dependency:{os.path.basename(normalized)}"
            return updated, audit

    # 4. Last resort: scan _step_*.result.json on disk for any *.md path.
    if workdir_abs and os.path.isdir(workdir_abs):
        for fname in sorted(
            os.listdir(workdir_abs), reverse=True
        ):
            if not (
                fname.startswith(_STEP_RESULT_FILE_PREFIX)
                and fname.endswith(_STEP_RESULT_FILE_SUFFIX)
            ):
                continue
            full = os.path.join(workdir_abs, fname)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, encoding="utf-8", errors="replace") as fobj:
                    payload = json.load(fobj)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            result_block = payload.get("result") or {}
            if not isinstance(result_block, dict):
                continue
            for key in ("files", "path"):
                dep_files = result_block.get(key) or []
                if isinstance(dep_files, str):
                    dep_files = [dep_files]
                for f in dep_files:
                    if (
                        isinstance(f, str)
                        and f.lower().endswith(".md")
                        and os.path.isfile(f)
                    ):
                        normalized, inside = resolve_relative_path(
                            f, working_dir=working_dir, must_exist=True
                        )
                        if inside:
                            updated = dict(params)
                            updated["source_path"] = normalized
                            audit["resolved"] = True
                            audit["fill_source"] = (
                                f"on_disk:{os.path.basename(full)}"
                            )
                            return updated, audit

    owner, mech = classify_failure_owner(
        error="no content (provide content or source_path)",
        event_type=event_type or params.get("_harness_event_type", ""),
        has_user_provided_inputs=True,
        typed_reference_resolved=False,
    )
    audit["failure_owner"] = owner
    audit["mechanism"] = mech
    return params, audit


__all__ = [
    "FAILURE_OWNER_DELIVERY",
    "FAILURE_OWNER_ENVIRONMENT",
    "FAILURE_OWNER_EVENT_HANDLER",
    "FAILURE_OWNER_OUTPUT_REFERENCE",
    "FAILURE_OWNER_PLANNER_CONTRACT",
    "FAILURE_OWNER_USER_INPUT",
    "MECHANISM_PDF_MISSING_AFTER_RECOVERY",
    "MECHANISM_RELPATH_OUTSIDE_WORKDIR",
    "MECHANISM_SAME_PARAMS_RETRY_LOOP",
    "MECHANISM_TYPED_REFERENCE_UNRESOLVED",
    "MECHANISM_USER_MESSAGE_OK_INTERNAL_FAIL",
    "MECHANISM_EXPECTED_MISSING_INPUT",
    "RetryDecision",
    "TypedResolution",
    "classify_failure_owner",
    "decide_step_retry",
    "is_typed_reference",
    "normalize_params_for_retry_check",
    "resolve_pdf_source",
    "resolve_relative_path",
    "resolve_typed_reference",
]
