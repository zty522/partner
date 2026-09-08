"""Partner 04 实例主动学习 hermes_external_learning。

This is the Partner side of the BDK integration.  It is NOT a Hermes-side
script: when invoked, it acts as Partner, scanning an external research
directory, extracting structured claims, and registering them as governed
candidate skills via Partner's existing registry.

Architecture:
    1. Use Partner-owned BDK compatibility modules; never import the incubator.
    2. Walk the configured external-learning notes and parse each note's
       frontmatter-style metadata block (source / 抓取时间 / 主题).
    3. For each note, build a governed knowledge-draft Candidate payload with:
         - source_episode_ids: ["hermes_external_learning:<relative_path>"]
         - intervention: BDK FunctionPool kernel configuration JSON
         - ocamms_constraint: validator output on the kernel logits
    4. Call the legacy candidate_skills registry for storage compatibility,
       while marking each record artifact_type=knowledge_draft and
       execution_ready=false. A note is not an executable Skill.
    5. Print a terminal summary listing every registered skill.

Design honesty:
    - The script does NOT call any LLM.  Parsing is regex + line scan.
    - The BDK validators used here (`ocamms_validator`) only require numpy.
      We do NOT import torch-dependent modules in this stage.
    - All registered knowledge drafts carry `status="candidate"` and
      `production_effective=false`.  Promotion requires a separate
      `decide_manual_canary` step with user approval (out of scope here).
    - The script is idempotent: re-running it produces new revisions of
      the same candidate_id (register_candidate_skill increments version).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from partner.learn.bdk_constraints import (
    apply_ocamms_constraint,
    apply_ocamms_constraint_with_advice,
)
from partner.learn.bdk_function_pool import FunctionPool

from partner.governance.candidate_skills import register_candidate_skill  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HERMES_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "docs/knowledge/incubated_external_learning"
KERNEL_NAMES = ["linear", "quadratic", "fourier", "expdecay"]

# Markdown section / metadata patterns (no LLM involved).
_META_SOURCE = re.compile(r"\*\*来源\*\*\s*[:：]\s*(?P<src>.+)")
_META_TIME = re.compile(r"\*\*(?:抓取时间|时[间间]|日期)\*\*\s*[:：]\s*(?P<t>.+)")
_META_TOPIC = re.compile(r"\*\*主题\*\*\s*[:：]\s*(?P<t>.+)")
_META_TITLE = re.compile(r"^#\s+(?P<t>.+)")
_HEADING = re.compile(r"^##\s+(?P<h>.+)")

# Map topic keywords to a candidate skill category.  The mapping is explicit
# so we can audit it; if a note's topic doesn't match any, it falls back to
# "agent_general" with reduced confidence (intervention only contains the
# kernel config, not speculative reasoning).
TOPIC_TO_CATEGORY = [
    # Use \b word boundaries so short tokens like "dom", "web", "rl", "kg"
    # do not accidentally match substrings in unrelated words ("random"->"dom").
    # Order matters: more specific patterns first; agent_framework is broad and
    # intentionally late so multi-agent/SOP/code-agent/etc. still match.
    (re.compile(r"\bbayesian\b|\bprior[\s-]?fitted\b|\bin[\s-]?context\b"), "bayesian_function_learning"),
    (re.compile(r"\bcontinual\b|\blifelong[\s-]?learning\b|\btest[\s-]?time\b"), "continual_learning"),
    (re.compile(r"\brl\b|\breward\b|\bdebugging\b|\bresample\b|\bself[\s-]?evolving\b"), "rl_alignment"),
    (re.compile(r"\bworld[\s-]?model\b|\bworld[\s-]?action\b|\brobot\b"), "world_model"),
    (re.compile(r"\bsecurity\b|\bguard\b|\bprompt[\s-]?injection\b|\btool\b|\bsafety\b"), "agent_security"),
    (re.compile(r"\bknowledge[\s-]?graph\b|\bspatial\b|\btrace\b"), "knowledge_graph"),
    (re.compile(r"\bbrowser\b|\bdom\b|\bvision\b|\bweb\b"), "browser_automation"),
    (re.compile(r"\bmultimodal\b|\brag\b|\bvisual\b"), "multimodal_rag"),
    # agent_framework is intentionally the broadest: matches multi-agent / SOP /
    # code-agent / stigmergy / data agents / swarm / metagpt / openhands / langchain.
    (re.compile(r"\bagent[\s-]?framework\b|\bswarm\b|\bmetagpt\b|\bopenhands\b|\blangchain\b|\bmulti[\s-]?agent\b|\bsop\b|\bcode[\s-]?agent\b|\bstigmergy\b|\bdata[\s-]?agents?\b"), "agent_framework"),
]


def _parse_note(path: Path) -> dict[str, Any] | None:
    """Extract metadata from a single markdown note.

    Returns None if the file is unreadable or missing required fields.
    The parser is intentionally narrow: only files matching the established
    note format (with `**来源**:` and `**主题**:` blocks) produce skills.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    source = ""
    topic = ""
    fetched_at = ""
    title = ""
    headings: list[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not source:
            m = _META_SOURCE.match(line)
            if m:
                source = m.group("src").strip()
                continue
        if not topic:
            m = _META_TOPIC.match(line)
            if m:
                topic = m.group("t").strip()
                continue
        if not fetched_at:
            m = _META_TIME.match(line)
            if m:
                fetched_at = m.group("t").strip()
                continue
        if not title:
            m = _META_TITLE.match(line)
            if m:
                title = m.group("t").strip()
                continue
        m = _HEADING.match(line)
        if m:
            headings.append(m.group("h").strip())

    if not source and not title:
        return None

    return {
        "source": source,
        "topic": topic,
        "fetched_at": fetched_at,
        "title": title or path.stem,
        "headings": headings[:8],  # cap to top 8 section titles
        "path": path,
    }


def _category_for_topic(topic: str) -> tuple[str, float]:
    """Map a topic string to a candidate skill category and confidence.

    Confidence drops to 0.5 if no keyword matches.  Confidence is recorded
    in the skill payload so 05 (evolution review) can weight candidates.
    """
    if not topic:
        return "agent_general", 0.5
    topic_l = topic.lower()
    for pattern, category in TOPIC_TO_CATEGORY:
        if pattern.search(topic_l):
            return category, 0.9
    return "agent_general", 0.5


def _kernel_logits_for(category: str) -> list[float]:
    """Map a candidate skill category to BDK FunctionPool logits.

    The mapping is declared, not learned.  Each category biases the gate
    toward specific kernels.  Every entry is verified to pass
    `apply_ocamms_constraint` (margin=0.20, max_activated=2).

    Note: "agent_general" is intentionally sparse — only 1 kernel activated.
    A truly unknown topic must default to a deliberately simple profile so
    we never accidentally fabricate a multi-kernel "active" skill.
    """
    # Order: linear, quadratic, fourier, expdecay (must match KERNEL_NAMES)
    # Logits use negative values for suppressed kernels so that softmax
    # produces a clearly peaked distribution (ocamms checks softmax output
    # against margin=0.20 and max_activated=2).
    base = [0.9, -1.0, -1.0, -1.0]  # sparse default = 1 activated kernel
    bias = {
        "bayesian_function_learning": [0.6, 0.4, -1.0, -1.0],    # 2 activated
        "continual_learning":         [0.7, -0.5, -1.0, -1.0],    # 1 activated
        "rl_alignment":               [0.6, 0.5, -0.5, -1.0],    # 2 activated
        "world_model":                [-1.0, 0.3, 0.6, -1.0],    # 2 activated
        "agent_security":             [0.8, -0.5, -1.0, -1.0],    # 1 activated
        "knowledge_graph":            [0.5, 0.6, -0.5, -1.0],    # 2 activated
        "browser_automation":         [0.6, -0.5, -0.5, -1.0],    # 1 activated
        "agent_framework":            [0.6, 0.5, -0.5, -1.0],    # 2 activated
        "multimodal_rag":             [0.7, -0.5, -1.0, -1.0],    # 1 activated
        "agent_general":              [0.9, -1.0, -1.0, -1.0],    # 1 activated
    }
    return bias.get(category, base)


def _kernel_config(logits: list[float], category: str) -> dict[str, Any]:
    """Build BDK FunctionPool config + intervention description.

    The config is the JSON form Partner can pass to BDK at runtime via
    `FunctionPool(in_dim=N, num_kernels=4)` + `kernel_mask`.  We also
    serialize the raw logits so downstream consumers can replay ocamms.
    """
    # softmax for display purposes
    import math
    m = max(logits)
    exps = [math.exp(x - m) for x in logits]
    s = sum(exps)
    probs = [e / s for e in exps]

    return {
        "bdk_module": "partner.learn.bdk_function_pool.FunctionPool",
        "kernel_names": KERNEL_NAMES,
        "kernel_logits": logits,
        "kernel_probs": [round(p, 4) for p in probs],
        "kernel_mask_suggestion": {
            name: prob > 0.20 for name, prob in zip(KERNEL_NAMES, probs)
        },
        "category": category,
        "rationale": "logits chosen by declared TOPIC_TO_CATEGORY mapping (auditable in code)",
    }


def build_skill_payload(meta: dict[str, Any], workspace: str,
                        source_root: Path = HERMES_DEFAULT_ROOT) -> dict[str, Any]:
    """Compose one candidate skill payload from a parsed note."""
    rel = meta["path"].relative_to(source_root)
    category, confidence = _category_for_topic(meta.get("topic", ""))
    logits = _kernel_logits_for(category)
    kernel_config = _kernel_config(logits, category)

    # Run BDK ocamms validator on the logits.  Required by ADR 0023 and
    # by the integration plan §3.3 (only numpy modules).
    passed, suggested_probs, message = apply_ocamms_constraint_with_advice(logits)
    kernel_config["ocamms_passed"] = bool(passed)
    kernel_config["ocamms_message"] = message
    if suggested_probs is not None:
        kernel_config["ocamms_suggested_probs"] = [round(float(p), 4) for p in suggested_probs]

    # Construct source_episode_ids as a Partner-recognized identifier.  We
    # use the format `hermes_external_learning:<rel_path>` so consumers can
    # trace the file.  Per ADR 0023 / candidate skill schema, episode ids
    # must be strings; we deliberately do not fabricate Partner episode ids.
    source_id = f"hermes_external_learning:{rel}"

    # candidate_id is stable per file so re-runs version-up rather than
    # produce duplicates.  Derived from the rel path sha256.
    import hashlib
    cid_hash = hashlib.sha256(str(rel).encode("utf-8")).hexdigest()[:12]
    candidate_id = f"candidate_hermes_learn_{cid_hash}"

    return {
        "candidate_id": candidate_id,
        "title": f"从 hermes_external_learning 提取的知识候选：{meta['title'][:60]}",
        "status": "candidate",  # always candidate; never auto-promote
        "artifact_type": "knowledge_draft",
        "project_id": "literature_github_learning",
        "experiment_id": f"hermes_learn_{cid_hash}",
        "strategy_id": candidate_id,
        "source_episode_ids": [source_id],
        "failure_classes": [],
        "applicability": [
            "04 instance",
            "literature/GitHub evidence synthesis",
            "manual_stable",
            f"category:{category}",
        ],
        "non_applicability": [
            "production planner",
            "automatic iteration",
            "memory write",
            "cross-arm output",
            "01/02/03/05 default flows",
        ],
        "counterexamples": [],
        "baseline": {
            "method": "no_skill_baseline",
            "note": "Baseline is Partner's existing context_selector without this candidate.",
            "category_confidence": confidence,
        },
        "intervention": json.dumps(kernel_config, ensure_ascii=False),
        "execution_contract": {
            "ready": False,
            "kind": "event",
            "event_type": "",
            "allowed_instances": ["04"],
            "default_params": {},
            "reason": "source note is evidence for a future Candidate; it is not an executable Skill",
        },
        "success_criteria": [
            "BDK ocamms_constraint passed on kernel_logits",
            "source_episode_ids traces to hermes_external_learning/ specific file",
            "candidate_id stable across re-runs (version increments only)",
        ],
        "shadow_evidence": {
            "source_note_path": str(rel),
            "source_note_title": meta["title"],
            "source_note_topic": meta.get("topic", ""),
            "source_note_fetched_at": meta.get("fetched_at", ""),
            "source_note_headings": meta.get("headings", []),
            "extraction_method": "regex_metadata_parser",
            "bdk_module_used": "partner.learn.bdk_constraints (legacy classification audit only; no FunctionPool training)",
        },
        "rollback": "remove the candidate file from share/mind/governance/experience_guided_policy/candidate_skills/",
    }


def scan_notes(source_root: Path) -> list[Path]:
    """Return every .md file under source_root/notes/ (recursive)."""
    notes_dir = source_root / "notes"
    if not notes_dir.exists():
        return []
    return sorted(notes_dir.rglob("*.md"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m partner.learn.learn_from_hermes",
        description="Partner 04 instance: scan hermes_external_learning and register candidate skills.",
    )
    parser.add_argument(
        "--workspace", required=True,
        help="Partner workspace root (e.g. /mnt/e/work/partner_workspace)"
    )
    parser.add_argument(
        "--source", default=str(HERMES_DEFAULT_ROOT),
        help="Path to hermes_external_learning root (default: %(default)s)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse + validate but do NOT register any skill."
    )
    args = parser.parse_args(argv)

    workspace = args.workspace
    source_root = Path(args.source)
    if not source_root.exists():
        print(f"ERROR: source root does not exist: {source_root}", file=sys.stderr)
        return 2

    notes = scan_notes(source_root)
    print(f"== partner.learn.learn_from_hermes ==")
    print(f"workspace:        {workspace}")
    print(f"source_root:      {source_root}")
    print(f"notes found:      {len(notes)}")
    print(f"dry_run:          {args.dry_run}")
    print()

    parsed: list[tuple[Path, dict[str, Any]]] = []
    skipped = 0
    for note_path in notes:
        meta = _parse_note(note_path)
        if meta is None:
            skipped += 1
            continue
        parsed.append((note_path, meta))

    print(f"== parsed {len(parsed)} notes, skipped {skipped} ==")
    print()

    registered: list[dict[str, Any]] = []
    for note_path, meta in parsed:
        payload = build_skill_payload(meta, workspace, source_root)

        # Defensive check: ocamms must pass before we register.  If a note's
        # declared category produces logits that fail ocamms, we still
        # register (with ocamms_passed=false in shadow_evidence) but flag it.
        ocamms_passed = apply_ocamms_constraint(
            _kernel_logits_for(_category_for_topic(meta.get("topic", ""))[0])
        )

        if args.dry_run:
            print(f"[DRY] would register: {payload['candidate_id']} from {note_path}")
            continue

        result = register_candidate_skill(workspace, payload)
        registered.append({
            "candidate_id": payload["candidate_id"],
            "source": str(note_path.relative_to(source_root)),
            "status": result.get("status"),
            "path": result.get("path"),
            "ocamms_passed": ocamms_passed,
        })

    if not args.dry_run:
        print(f"== registered {len(registered)} candidate skills ==")
        for entry in registered:
            print(
                f"  - {entry['candidate_id']} "
                f"[status={entry['status']}, ocamms={'PASS' if entry['ocamms_passed'] else 'FAIL'}] "
                f"<- {entry['source']}"
            )
        print()
        print(f"== summary ==")
        print(f"  notes_scanned:        {len(notes)}")
        print(f"  parsed:               {len(parsed)}")
        print(f"  skipped:              {skipped}")
        print(f"  skills_registered:    {len(registered)}")
        print(f"  ocamms_passed:        {sum(1 for e in registered if e['ocamms_passed'])}")
        print(f"  ocamms_failed:        {sum(1 for e in registered if not e['ocamms_passed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
