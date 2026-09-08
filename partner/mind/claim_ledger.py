"""Claim ledger and claim-level truth gate (ADR 0043 P1-B).

The 04 manual failure ``traj_manual_4d3f78f10ae387af`` showed that
``evidence_quote`` membership alone is not "semantic truth" because the
planner can pick the first paragraph / heading / banner / frontmatter and
still satisfy literal substring checks.  This module introduces a
deliberate claim ledger:

    claim_id, claim_text, source_path, source_identity, evidence_quote,
    support_type ∈ {direct, inference, proposed, not_found},
    match_window (line numbers or section title), claim_axes
    (event_recording / task_lifecycle / context_management /
     tool_execution / failure_recovery when relevant).

It also adds the hard truth gate that runs after the artifact is written:

* source_path must equal one of the named user inputs,
* evidence_quote must be a contiguous substring of that file,
* cross-source swap detection (DeepSeek text under ``openclaw`` claim
  refuses the report),
* boilerplate rejection (# heading / hr / frontmatter / banner / link
  only -- the four real ``source_quote`` failures were all headings or
  language tags per the ADR 0043 failure),
* inference must be labelled and is scored at ``0.5`` of direct,
* claim without quote hard-fails the report rather than silently
  becoming ``truth=1``.

This module never bypasses Event-first -- it's just data + functions.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

JsonDict = dict[str, Any]

# Five axes the 04 task asked the report to compare across the four Harness
# projects.  An evidence selection that picks titles for these axes will
# always look "successful" but is boilerplate, not semantic.
FIVE_AXES = (
    "event_recording",
    "task_lifecycle",
    "context_management",
    "tool_execution",
    "failure_recovery",
    "runtime_learning",
    "parameter_update",
)

SUPPORT_DIRECT = "direct"
SUPPORT_INFERENCE = "inference"
SUPPORT_PROPOSED = "proposed"
SUPPORT_NOT_FOUND = "not_found"

# Boilerplate patterns.  Rejecting these is the answer to the 04 failure:
# the pre-existing preflight selected them as evidence for the report.
_BOILERPLATE_PATTERNS = (
    re.compile(r"^\s*#\s+"),
    re.compile(r"^---+$"),
    re.compile(r"^\s*<!--"),
    re.compile(r"^\s*<!\["),
    re.compile(r"^\s*language:\s*(zh|en)", re.I),
    re.compile(r"^\s*title:", re.I),
    re.compile(r"^\s*#\s+\w+\s+(README|ARCHITECTURE)", re.I),
    re.compile(r"^\s*\[(.+)\]\((.+)\)"),
    re.compile(r"^\s*\*\[!?\["),
    re.compile(r"^\s*\*\*[A-Za-z]+\*\*:\s*$"),
    re.compile(r"^\s*[<>]\w"),
    re.compile(r"^\s*$"),
)


def is_boilerplate(line: str) -> bool:
    """Return True if a line is plain markdown heading / banner / link
    / frontmatter / empty.  ADR 0043 P1-B boilerplate rejection.
    """
    if not isinstance(line, str):
        return True
    s = line.strip()
    if not s:
        return True
    return any(p.match(s) for p in _BOILERPLATE_PATTERNS)


@dataclass
class Claim:
    claim_id: str
    claim_text: str
    claim_axes: tuple[str, ...] = field(default_factory=tuple)
    source_path: str = ""
    source_identity: str = ""
    evidence_quote: str = ""
    support_type: str = SUPPORT_PROPOSED
    match_window: tuple[int, int] = (-1, -1)  # (start_line, end_line)
    rationale: str = ""
    # Populated only for claims parsed from an explicit ledger.  Keeping the
    # field names lets the gate distinguish an intentionally empty value
    # (for example a not_found source) from a response truncated halfway
    # through a Claim block.
    ledger_fields: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def as_dict(self) -> JsonDict:
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "claim_axes": list(self.claim_axes),
            "source_path": self.source_path,
            "source_identity": self.source_identity,
            "evidence_quote": self.evidence_quote,
            "support_type": self.support_type,
            "match_window": list(self.match_window),
            "rationale": self.rationale,
        }


def _file_lines(path: str) -> list[str]:
    try:
        if str(path).lower().endswith(".pdf"):
            from partner.governance.research_learning import read_research_source_text
            return read_research_source_text(path).splitlines(keepends=True)
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception:
        return []


def _find_quote_window(quote: str, lines: Sequence[str]) -> tuple[int, int]:
    """Return ``(start_line, end_line)`` of the first contiguous run that
    contains ``quote``.  Returns ``(-1, -1)`` if not found.
    """
    if not quote:
        return (-1, -1)
    for i in range(len(lines)):
        j = i
        running = []
        while j < len(lines):
            running.append(lines[j].rstrip("\n"))
            joined = "\n".join(running)
            if quote in joined:
                return (i + 1, j + 1)  # 1-indexed
            j += 1
            if len(joined) > len(quote) * 4 + 200:
                break
    return (-1, -1)


def build_claim(
    *,
    claim_id: str,
    claim_text: str,
    source_path: str,
    evidence_quote: str,
    support_type: str,
    claim_axes: tuple[str, ...] = (),
    rationale: str = "",
) -> Claim:
    """Create a :class:`Claim` and validate the basic membership rule.

    ``source_identity`` is the basename of ``source_path`` (or hash for
    absolute paths).  Membership + boilerplate checks are NOT destructive;
    a Claim with bad evidence is still returned so the gate can decide.
    """
    base = os.path.basename(source_path) if source_path else ""
    return Claim(
        claim_id=claim_id,
        claim_text=claim_text,
        claim_axes=claim_axes,
        source_path=source_path,
        source_identity=base,
        evidence_quote=evidence_quote,
        support_type=support_type,
        match_window=_find_quote_window(
            evidence_quote, _file_lines(source_path) if source_path else []
        ),
        rationale=rationale,
    )


@dataclass
class TruthGateResult:
    ok: bool
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    cross_source_swaps: tuple[str, ...]
    boilerplate_evidence: tuple[str, ...]
    missing_quotes: tuple[str, ...]
    semantically_unsupported: tuple[str, ...] = ()
    missing_axes: tuple[str, ...] = ()
    incomplete_claims: tuple[str, ...] = ()
    invalid_support_types: tuple[str, ...] = ()
    explanation: str = ""

    def summary(self) -> JsonDict:
        return {
            "ok": self.ok,
            "passed_claims": list(self.passed),
            "failed_claims": list(self.failed),
            "cross_source_swaps": list(self.cross_source_swaps),
            "boilerplate_evidence": list(self.boilerplate_evidence),
            "missing_quotes": list(self.missing_quotes),
            "semantically_unsupported": list(self.semantically_unsupported),
            "missing_axes": list(self.missing_axes),
            "incomplete_claims": list(self.incomplete_claims),
            "invalid_support_types": list(self.invalid_support_types),
            "explanation": self.explanation,
        }


def validate_claims(
    claims: Sequence[Claim],
    *,
    named_input_sources: Iterable[str],
) -> TruthGateResult:
    """Run the claim-level truth gate.

    Parameters
    ----------
    claims:
        Output from :func:`build_claim`.  Every claim participates.
    named_input_sources:
        The four paths the 04 user message listed as inputs.  A claim
        whose ``source_path`` is not in this set is treated as a
        cross-source swap and fails the gate -- this catches "DeepSeek
        content attributed to OpenClaw" type errors.

    Rules (any failure -> ``ok=False``):

    1. ``source_path`` is in ``named_input_sources``.
    2. ``evidence_quote`` is a contiguous substring of that source.
    3. ``evidence_quote`` is not boilerplate (heading / hr / banner /
       frontmatter / link / empty / language tag).
    4. ``support_type`` is one of {direct, inference, proposed,
       not_found}; ``direct`` without quote membership hard-fails.
    5. When ``claim_axes`` mentions any of the five architecture axes,
       it must point to a non-boilerplate quote from the named source.
    """
    named = {os.path.realpath(p) for p in named_input_sources if p}
    named_names = {os.path.basename(p) for p in named_input_sources if p}
    named.add("")  # anything missing source_path is also bad
    passed: list[str] = []
    failed: list[str] = []
    cross_source_swaps: list[str] = []
    boilerplate_ev: list[str] = []
    missing_quotes: list[str] = []
    semantically_unsupported: list[str] = []
    missing_axes: list[str] = []
    incomplete_claims: list[str] = []
    invalid_support_types: list[str] = []
    required_ledger_fields = {
        "claim_id", "claim_text", "claim_axes", "source_path",
        "source_identity", "evidence_quote", "support_type", "rationale",
    }
    allowed_support_types = {
        SUPPORT_DIRECT, SUPPORT_INFERENCE, SUPPORT_PROPOSED, SUPPORT_NOT_FOUND,
    }
    axis_terms = {
        "event_recording": {"event", "trace", "record", "log", "history", "事件", "记录", "轨迹", "日志"},
        "task_lifecycle": {"task", "turn", "session", "state", "lifecycle", "任务", "会话", "状态", "生命周期"},
        "context_management": {"context", "memory", "prompt", "compact", "document", "knowledge",
                               "prune", "protect", "head", "tail", "token", "budget",
                               "上下文", "记忆", "提示", "压缩", "文档", "知识", "认知", "自进化"},
        "tool_execution": {"tool", "command", "sandbox", "execute", "call", "llm", "prune",
                           "工具", "命令", "沙箱", "执行", "调用", "剪枝"},
        "failure_recovery": {"failure", "error", "retry", "recover", "resume", "失败", "错误", "重试", "恢复"},
        "runtime_learning": {"runtime", "test-time", "feedback", "reward", "reinforcement", "learn", "learning",
                             "curriculum", "evolver", "critic", "training", "attempts", "proficiency",
                             "运行时", "反馈", "奖励", "强化", "学习", "课程"},
        "parameter_update": {"gradient", "parameter", "update", "weight", "梯度", "参数", "更新", "权重"},
    }

    def semantic_tokens(value: str) -> set[str]:
        lowered = value.lower()
        latin = set(re.findall(r"[a-z][a-z0-9_-]{2,}", lowered))
        chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", value)
        chinese = {
            run[index:index + 2]
            for run in chinese_runs
            for index in range(max(1, len(run) - 1))
        }
        stop = {"this", "that", "with", "from", "have", "will", "into", "以及", "一个", "进行", "可以", "使用"}
        tokens = (latin | chinese) - stop
        bilingual = {
            "prune": ("prune", "剪枝", "剔除", "修剪"),
            "protect": ("protect", "保护"),
            "head": ("head", "头部"),
            "prompt": ("prompt", "提示词", "系统提示"),
            "exchange": ("exchange", "对话", "交互"),
            "tool": ("tool", "工具"),
            "call": ("call", "调用"),
            "context": ("context", "上下文"),
            "budget": ("budget", "预算"),
            "tail": ("tail", "尾部"),
            "recent": ("recent", "最近", "近端"),
        }
        for canonical, aliases in bilingual.items():
            if any(alias in lowered for alias in aliases):
                tokens.add(canonical)
        return tokens

    for claim in claims:
        c_id = claim.claim_id
        if claim.ledger_fields and not required_ledger_fields.issubset(claim.ledger_fields):
            incomplete_claims.append(c_id)
            failed.append(c_id)
            continue
        if claim.support_type not in allowed_support_types:
            invalid_support_types.append(c_id)
            failed.append(c_id)
            continue
        if claim.ledger_fields and not claim.claim_text.strip():
            incomplete_claims.append(c_id)
            failed.append(c_id)
            continue
        src_real = os.path.realpath(claim.source_path) if claim.source_path else ""
        in_named = (
            claim.source_path in {p for p in named_input_sources}
            or src_real in named
            or claim.source_identity in named_names
        )
        if not in_named and claim.support_type == SUPPORT_DIRECT:
            cross_source_swaps.append(c_id)
            failed.append(c_id)
            continue
        quote = (claim.evidence_quote or "").strip()
        if not quote and claim.support_type == SUPPORT_DIRECT:
            missing_quotes.append(c_id)
            failed.append(c_id)
            continue
        if quote:
            line0 = quote.splitlines()[0] if quote else ""
            if is_boilerplate(line0) and claim.support_type == SUPPORT_DIRECT:
                # Boilerplate quotes are explicitly rejected for direct.
                boilerplate_ev.append(c_id)
                failed.append(c_id)
                continue
            # Bug #62 (ADR 0067): support_type=proposed means the claim
            # is a forward-looking recommendation, not a quote from a
            # real source.  Skip the source-membership check so first-
            # iteration plans (where atomic_inspect_file returns
            # SKIP-not-raise, see Bug #59 P2) can record proposed
            # claims without fabricating evidence.  Without this fix
            # every first-iteration task fails the candidate-truth
            # gate with missing_quotes.
            if claim.support_type == SUPPORT_PROPOSED:
                pass
            elif str(claim.source_path).lower().endswith(".pdf"):
                from partner.governance.research_learning import (
                    normalize_research_evidence_text, read_research_source_text,
                )
                body = read_research_source_text(claim.source_path)
                if normalize_research_evidence_text(quote) not in normalize_research_evidence_text(body):
                    missing_quotes.append(c_id)
                    failed.append(c_id)
                    continue
            else:
                with open(claim.source_path, encoding="utf-8", errors="replace") as f:
                    body = f.read()
                from partner.governance.research_learning import normalize_research_evidence_text
                if normalize_research_evidence_text(quote) not in normalize_research_evidence_text(body):
                    missing_quotes.append(c_id)
                    failed.append(c_id)
                    continue
        axes = tuple(axis for axis in claim.claim_axes if axis in FIVE_AXES)
        if claim.support_type == SUPPORT_DIRECT and not axes:
            missing_axes.append(c_id)
            failed.append(c_id)
            continue
        if claim.support_type == SUPPORT_DIRECT:
            claim_tokens = semantic_tokens(claim.claim_text)
            quote_tokens = semantic_tokens(quote)
            overlap = claim_tokens & quote_tokens
            cross_language = bool(re.search(r"[\u4e00-\u9fff]", claim.claim_text)) \
                and bool(re.search(r"[A-Za-z]", quote))
            lexical_overlap = (
                (cross_language and len(overlap) >= 1)
                or (len(overlap) >= 2
                    and len(overlap) / max(1, len(claim_tokens)) >= 0.35)
            )
            # The evidence—not merely the claim prose—must mention the
            # claimed comparison axis.  Otherwise a generic event-log quote
            # could be attached to an unsupported failure-recovery claim.
            axis_overlap = all(
                bool(axis_terms.get(axis, set()) & quote_tokens) for axis in axes
            )
            if not (lexical_overlap and axis_overlap):
                semantically_unsupported.append(c_id)
                failed.append(c_id)
                continue
        passed.append(c_id)
    explanation = (
        f"{len(passed)} passed, {len(failed)} failed; "
        f"{len(cross_source_swaps)} cross-source swaps, "
        f"{len(boilerplate_ev)} boilerplate, {len(missing_quotes)} missing quotes"
    )
    return TruthGateResult(
        ok=not failed,
        passed=tuple(passed),
        failed=tuple(failed),
        cross_source_swaps=tuple(cross_source_swaps),
        boilerplate_evidence=tuple(boilerplate_ev),
        missing_quotes=tuple(missing_quotes),
        semantically_unsupported=tuple(semantically_unsupported),
        missing_axes=tuple(missing_axes),
        incomplete_claims=tuple(incomplete_claims),
        invalid_support_types=tuple(invalid_support_types),
        explanation=explanation,
    )


_FIELD = re.compile(
    r"^[ \t]*(?:[-*>][ \t]*)?`?(claim_id|claim|claim_text|claim_axes|source_path|"
    r"source_identity|evidence_quote|support_type|rationale)`?\s*[:：]\s*(.*?)\s*$",
    re.I | re.M,
)


def extract_claims_from_text(text: str) -> list[Claim]:
    """Parse explicit claim-ledger blocks from a Markdown/TXT artifact."""
    matches = list(_FIELD.finditer(str(text or "")))
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for match in matches:
        key = match.group(1).lower()
        value = match.group(2).strip().strip("`\"'“”")
        if key == "claim_id" and current:
            blocks.append(current)
            current = {}
        current[key] = value
    if current:
        blocks.append(current)
    claims: list[Claim] = []
    for index, block in enumerate(blocks, 1):
        claim_text = block.get("claim_text") or block.get("claim") or ""
        source_path = block.get("source_path") or ""
        quote = block.get("evidence_quote") or ""
        if not (claim_text or source_path or quote):
            continue
        raw_axes = block.get("claim_axes") or ""
        axes = tuple(value for value in re.split(r"[,，;；|\s]+", raw_axes) if value in FIVE_AXES)
        explicit_claim_ledger = "claim_id" in block or "claim_text" in block or "claim" in block
        claims.append(build_claim(
            claim_id=block.get("claim_id") or f"claim_{index:03d}",
            claim_text=claim_text,
            source_path=source_path,
            evidence_quote=quote,
            # Legacy source_path/evidence_quote provenance pairs are not
            # Claim Ledgers and retain their compatible not_found default.
            # An explicit Claim block missing support_type is malformed.
            support_type=(block.get("support_type") or (
                "" if explicit_claim_ledger else SUPPORT_NOT_FOUND
            )).lower(),
            claim_axes=axes,
            rationale=block.get("rationale") or "",
        ))
        claims[-1].ledger_fields = tuple(block.keys()) if explicit_claim_ledger else ()
    return claims


def audit_claim_artifacts(
    artifacts: Iterable[str], *, named_input_sources: Iterable[str],
    require_claims: bool = True,
) -> JsonDict:
    """Extract, validate and return a serializable claim-level audit."""
    claims: list[Claim] = []
    artifact_paths: list[str] = []
    for raw in artifacts:
        path = str(raw)
        if not os.path.isfile(path) or os.path.splitext(path)[1].lower() not in {".md", ".txt"}:
            continue
        artifact_paths.append(path)
        try:
            claims.extend(extract_claims_from_text(
                Path(path).read_text(encoding="utf-8", errors="replace")
            ))
        except OSError:
            continue
    if require_claims and not claims:
        return {
            "passed": False, "claim_count": 0, "claims": [],
            "failed_claims": ["claim_ledger_missing"],
            "explanation": "claim-level audit requires explicit claim ledger blocks",
            "artifact_paths": artifact_paths,
        }
    score, gate = score_claim_truth(claims, named_input_sources=named_input_sources)
    return {
        "passed": bool(claims) and gate.ok,
        "claim_count": len(claims),
        "truth_score": score,
        "claims": [claim.as_dict() for claim in claims],
        **gate.summary(),
        "artifact_paths": artifact_paths,
    }


def score_claim_truth(
    claims: Sequence[Claim],
    *,
    named_input_sources: Iterable[str],
) -> tuple[float, TruthGateResult]:
    """Return ``(truth_score, gate_result)``.

    ``truth_score`` is in [0, 1].  Direct+supported claims count 1.0,
    inference claims count 0.5 (per ADR 0043 P1-B.10), proposed or
    failed claims count 0.0.  A failed gate collapses the score.
    """
    gate = validate_claims(claims, named_input_sources=named_input_sources)
    if not gate.ok:
        return 0.0, gate
    total = 0.0
    for claim in claims:
        if claim.support_type == SUPPORT_DIRECT and claim.claim_id in gate.passed:
            total += 1.0
        elif claim.support_type == SUPPORT_INFERENCE and claim.claim_id in gate.passed:
            total += 0.5
        elif claim.support_type in (SUPPORT_PROPOSED, SUPPORT_NOT_FOUND):
            total += 0.0
    return min(1.0, total / max(1, len(claims))), gate


__all__ = [
    "Claim",
    "FIVE_AXES",
    "SUPPORT_DIRECT",
    "SUPPORT_INFERENCE",
    "SUPPORT_PROPOSED",
    "SUPPORT_NOT_FOUND",
    "TruthGateResult",
    "build_claim",
    "is_boilerplate",
    "score_claim_truth",
    "validate_claims",
    "extract_claims_from_text",
    "audit_claim_artifacts",
]
