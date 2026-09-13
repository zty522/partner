"""4-level project classifier.  ADR 0099 §3.

L1: explicit project_id from caller (e.g. attachment metadata,
    explicit tag in request)
L2: session context lookup
L3: dynamic project registry semantic retrieval (tfidf now, embedding later)
L4: LLM fallback — returns ``new_project_kind`` when nothing else matched

Default Phase 2 stub: L3 backend='none', so L3 never returns hits and
classification typically falls through to L4.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol


class Kind(str, Enum):
    EXISTING_PROJECT = "existing_project"
    NEW_PROJECT_KIND = "new_project_kind"
    CHAT_REPLY = "chat_reply"


@dataclass(frozen=True)
class ProjectDecision:
    kind: Kind
    project_id: str = ""        # set when kind=existing_project
    project_hint: str = ""      # suggested id when kind=new_project_kind
    source_level: str = ""      # "L1" | "L2" | "L3" | "L4" | ""
    confidence: float = 0.0     # 0..1; higher = more confident
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "project_id": self.project_id,
            "project_hint": self.project_hint,
            "source_level": self.source_level,
            "confidence": self.confidence,
            "reason": self.reason,
        }


class _LLMCallable(Protocol):
    """Subclass of partner.adapters.adapter.create_adapter / HermesAdapter.

    We don't import the concrete adapter here to keep this module
    dependency-free for tests; the project_classifier orchestrator
    passes any object that exposes ``chat(purpose=..., prompt=...)``.
    """
    def chat(self, *, purpose: str, prompt: str) -> Any: ...


@dataclass
class ProjectClassifier:
    """Orchestrates L1..L4 and returns a ProjectDecision."""

    workspace_root: Path
    session_context: Any  # partner.projects.session_context.SessionContext
    registry: Any       # partner.projects.dynamic_project_registry.DynamicProjectRegistry
    llm_adapter: Optional[_LLMCallable] = None
    semantic_backend: str = "none"  # user decision 2026-09-12: none first
    new_project_fallback: str = "project_init"  # "project_init" | "chat_reply" | "error"

    def classify(self, *,
                 request: str,
                 conversation_id: str = "",
                 explicit_project_id: str = "",
                 llm_call: Optional[Any] = None) -> ProjectDecision:
        """Run L1..L4 and return the first non-empty decision.

        ``llm_call`` is an optional override (for tests) — when omitted,
        ``self.llm_adapter.chat`` is used if available.
        """
        # ---------------- L1: explicit
        if explicit_project_id:
            proj = self.registry.get_brief_summary(explicit_project_id)
            if proj:
                return ProjectDecision(
                    kind=Kind.EXISTING_PROJECT,
                    project_id=explicit_project_id,
                    source_level="L1",
                    confidence=1.0,
                    reason="explicit project_id supplied by caller",
                )
            # Caller asked for a project that doesn't exist yet — treat as new
            return ProjectDecision(
                kind=Kind.NEW_PROJECT_KIND,
                project_hint=explicit_project_id,
                source_level="L1",
                confidence=0.9,
                reason="explicit project_id not in registry -> new_project_kind",
            )

        # ---------------- L2: session
        if conversation_id and self.session_context is not None:
            pid = self.session_context.lookup(conversation_id)
            if pid:
                return ProjectDecision(
                    kind=Kind.EXISTING_PROJECT,
                    project_id=pid,
                    source_level="L2",
                    confidence=0.85,
                    reason="session context lookup hit",
                )

        # ---------------- L3: semantic retrieval
        if self.registry is not None:
            # Force the configured semantic_backend on the registry so the
            # test path and runtime path see the same value (the registry's
            # own ``backend`` may default to "none" if constructed without
            # one).
            self.registry.backend = self.semantic_backend
            hits = self.registry.semantic_retrieve(request, top_k=1)
            if hits:
                best = hits[0]
                return ProjectDecision(
                    kind=Kind.EXISTING_PROJECT,
                    project_id=best.project_id,
                    source_level="L3",
                    confidence=0.6,
                    reason=f"semantic top hit (backend={self.semantic_backend})",
                )

        # ---------------- L4: LLM fallback
        llm_kind, llm_hint = self._llm_classify(request, llm_call=llm_call)
        if llm_kind == Kind.NEW_PROJECT_KIND and self.new_project_fallback == "project_init":
            hint = llm_hint or self._derive_hint(request)
            return ProjectDecision(
                kind=Kind.NEW_PROJECT_KIND,
                project_hint=hint,
                source_level="L4",
                confidence=0.55 if hint else 0.3,
                reason=("LLM says new direction; project_init will handle"
                        if hint else
                        "LLM says new direction but gave no hint; "
                        "project_init will derive from request"),
            )
        if llm_kind == Kind.CHAT_REPLY:
            return ProjectDecision(
                kind=Kind.CHAT_REPLY,
                source_level="L4",
                confidence=0.7,
                reason="LLM says chitchat",
            )
        # LLM said existing but no project_id returned
        if llm_kind == Kind.EXISTING_PROJECT and llm_hint:
            return ProjectDecision(
                kind=Kind.EXISTING_PROJECT,
                project_id=llm_hint,
                source_level="L4",
                confidence=0.5,
                reason="LLM pointed at existing project",
            )
        # Could not classify — surface as new_project_kind so a human can
        # rename / merge if needed
        return ProjectDecision(
            kind=Kind.NEW_PROJECT_KIND,
            project_hint=self._derive_hint(request),
            source_level="L4",
            confidence=0.3,
            reason="no signal; falling back to new_project_kind",
        )

    # -------------------------------------------------------- LLM fallback

    def _llm_classify(self, request: str, *,
                      llm_call: Optional[Any] = None) -> tuple[Kind, str]:
        """Call LLM to classify the request into one of three kinds.

        Returns ``(Kind, project_id_or_hint)``.  LLM output is parsed from
        a strict JSON shape; if parsing fails we conservatively assume
        new_project_kind so a human can rename.
        """
        prompt = (
            "You classify a user request into exactly one of three intents:\n"
            "- existing_project: it describes continuing work on an existing project\n"
            "- new_project_kind: it describes a brand-new direction not yet in the registry\n"
            "- chat_reply: it is a casual question not tied to any project\n\n"
            "Reply ONLY with a JSON object: {\"kind\": <one of three>, "
            "\"project_hint\": \"<short ascii slug if new or existing id>\"}\n\n"
            "User request=\"" + (request or "").strip()[:800] + "\"\n"
        )
        raw = ""
        try:
            if llm_call is not None:
                raw = str(llm_call(prompt) or "")
            elif self.llm_adapter is not None:
                raw = str(self.llm_adapter.chat(purpose="project_classify",
                                                prompt=prompt) or "")
        except Exception:  # noqa: BLE001
            raw = ""
        if not raw:
            return Kind.NEW_PROJECT_KIND, ""
        m = re.search(r"\{[^{}]*\}", raw)
        if not m:
            return Kind.NEW_PROJECT_KIND, ""
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return Kind.NEW_PROJECT_KIND, ""
        kind_str = str(data.get("kind") or "").strip()
        if kind_str == "existing_project":
            return Kind.EXISTING_PROJECT, str(data.get("project_hint") or "")
        if kind_str == "chat_reply":
            return Kind.CHAT_REPLY, ""
        return Kind.NEW_PROJECT_KIND, str(data.get("project_hint") or "")

    # --------------------------------------------------------- helpers

    @staticmethod
    def _derive_hint(request: str) -> str:
        """Cheap fallback slug from the user request.

        Strategy:
          1. take the first whitespace-delimited token
          2. lowercase + replace non-ASCII-safe chars with "_"
          3. strip leading/trailing "_"
          4. if step 3 yields "" (pure CJK / no useful token), fall back
             to the first 6 contiguous CJK characters of the request so
             the resulting project dir name is still meaningful
        """
        text = (request or "").strip()
        if not text:
            return ""
        first = re.split(r"[\s\u3000]+", text, maxsplit=1)[0]
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", first).strip("_").lower()
        if slug:
            return slug[:48]
        # Fallback for CJK-only / symbol-only input
        cjk = re.findall(r"[\u4e00-\u9fff]+", text)
        joined = "".join(cjk)[:24]
        return joined or ""


__all__ = ["ProjectClassifier", "ProjectDecision", "Kind"]
