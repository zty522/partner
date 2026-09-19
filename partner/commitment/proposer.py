"""Candidate proposal: a deterministic proposer and a real LLM proposer.

The LLM may propose candidates, a rationale and a prior.  It may not write a
final state, an external metric, or a claim about its own success: this module's
return type has no field for any of those.

The LLM path is bounded by an explicit call cap, records the real
provider/model/usage/latency, and returns an honest failure (no candidates) when
the provider is unreachable.  A dependency failure is never converted into
"no direction exists".
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import CANDIDATE_SOURCES, Candidate, ContractError, canonical_json, sha256_of
from .ports import ProposalResult

DEFAULT_TEMPERATURE = 0.0
# A reasoning model spends completion budget on reasoning tokens before it writes
# any content.  Measured on this deployment: deepseek-v4-flash burned all 900
# tokens as reasoning_tokens and returned empty content, so the proposal parsed as
# unparsable.  The budget must leave room for the answer itself.
DEFAULT_MAX_TOKENS = 2500


class ProposalError(RuntimeError):
    """The proposer could not produce a usable candidate set."""


# ---------------------------------------------------------------------------
# deterministic proposer
# ---------------------------------------------------------------------------

class DeterministicProposer:
    """Propose from a candidate space already declared in the frozen snapshot.

    This exists so the kernel is testable and so a bet can be formed without any
    model call.  It does not invent directions: every candidate must be declared,
    which keeps the deterministic path auditable.
    """

    proposed_by = "policy"

    def propose(self, *, question: str, snapshot: Mapping[str, Any],
                max_candidates: int) -> ProposalResult:
        space = list(snapshot.get("candidate_space") or ())
        if not space:
            return ProposalResult((), self.proposed_by, failure="candidate_space_missing")
        if int(max_candidates) < 1:
            raise ContractError("DeterministicProposer: max_candidates must be >= 1")
        taken = space[: int(max_candidates)]
        candidates: list[Candidate] = []
        for entry in taken:
            cid = str(entry.get("candidate_id") or "")
            if not cid:
                return ProposalResult((), self.proposed_by,
                                      failure=f"candidate_space entry missing candidate_id: {entry!r}")
            prior = dict(entry.get("prior") or {})
            candidates.append(Candidate(
                candidate_id=cid,
                description=str(entry.get("description") or cid),
                params=dict(entry.get("params") or {}),
                rationale=str(entry.get("rationale") or "") or
                          f"declared candidate; prior={canonical_json(prior)}",
                proposed_by=self.proposed_by,
            ))
        return ProposalResult(tuple(candidates), self.proposed_by,
                              trace_ref=f"declared:{sha256_of(space)[:16]}")


# ---------------------------------------------------------------------------
# real LLM proposer (DeepSeek Flash via the workspace configuration)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    model: str
    base_url: str
    api_key: str

    def public(self) -> dict[str, Any]:
        """Never include the credential."""
        return {"provider": self.provider, "model": self.model, "base_url": self.base_url,
                "api_key": "***REDACTED***"}


def resolve_provider_config(workspace: str | Path) -> LLMProviderConfig:
    """Resolve the configured provider from the workspace config files.

    Read order mirrors the runner's own routing file first, then the generic
    ``api.json``.  The credential is never logged or returned in public views.
    """
    workspace = Path(workspace)
    routing = workspace / "config" / "agent_api_config.json"
    if routing.exists():
        payload = json.loads(routing.read_text(encoding="utf-8"))
        provider = str((payload.get("_routing") or {}).get("provider") or "")
        entry = dict(payload.get(provider) or {})
        if provider and entry.get("api_key") and entry.get("base_url") and entry.get("model"):
            return LLMProviderConfig(provider, str(entry["model"]), str(entry["base_url"]),
                                     str(entry["api_key"]))
    generic = workspace / "config" / "api.json"
    if generic.exists():
        payload = json.loads(generic.read_text(encoding="utf-8"))
        apis = dict(payload.get("apis") or {})
        for name, entry in apis.items():
            entry = dict(entry or {})
            if entry.get("api_key") and entry.get("base_url") and entry.get("model"):
                return LLMProviderConfig(str(name), str(entry["model"]), str(entry["base_url"]),
                                         str(entry["api_key"]))
    raise ProposalError("no usable provider configuration found in the workspace")


class LLMProposer:
    """Ask a real model for a bounded candidate set.

    Guardrails:
    * a hard call cap; exceeding it is a recorded failure, not a silent retry
    * the model may only choose among the *declared* direction identifiers, so a
      hallucinated direction cannot enter the bet
    * every call records provider, model, usage and latency
    """

    proposed_by = "llm"

    def __init__(self, config: LLMProviderConfig, *, max_calls: int = 2,
                 temperature: float = DEFAULT_TEMPERATURE, timeout: float = 120.0) -> None:
        if int(max_calls) < 1:
            raise ContractError("LLMProposer: max_calls must be >= 1")
        self._config = config
        self._max_calls = int(max_calls)
        self._calls = 0
        self._temperature = float(temperature)
        self._timeout = float(timeout)
        self._transcript: list[dict[str, Any]] = []

    @property
    def calls_used(self) -> int:
        return self._calls

    @property
    def transcript(self) -> list[dict[str, Any]]:
        return list(self._transcript)

    def propose(self, *, question: str, snapshot: Mapping[str, Any],
                max_candidates: int) -> ProposalResult:
        if self._calls >= self._max_calls:
            return ProposalResult((), self.proposed_by, model_calls=0,
                                  provider=self._config.provider, model=self._config.model,
                                  failure="llm_call_cap_reached",
                                  trace_ref=self._transcript_ref())
        space = list(snapshot.get("candidate_space") or ())
        allowed = [str(entry.get("candidate_id") or "") for entry in space]
        if len(allowed) < 2:
            return ProposalResult((), self.proposed_by, provider=self._config.provider,
                                  model=self._config.model, failure="candidate_space_too_small")

        snapshot_brief = {
            "project_id": snapshot.get("project_id"),
            "constraints": snapshot.get("constraints"),
            "baseline_metrics": snapshot.get("baseline_metrics"),
            "candidate_space": [{"candidate_id": e.get("candidate_id"),
                                 "description": e.get("description"),
                                 "params": e.get("params")} for e in space],
        }
        prompt = (
            "You are a bounded research direction proposer. Return strict JSON only.\n"
            f"Question: {question}\n"
            f"Snapshot: {canonical_json(snapshot_brief)}\n"
            "Choose at most "
            f"{int(max_candidates)} candidate_id values from candidate_space, most promising first. "
            "You may not invent identifiers. You may not state results or scores.\n"
            'Return {"candidates":[{"candidate_id":"...","rationale":"..."}]}'
        )
        started = time.time()
        try:
            response = self._post_chat(prompt)
        except Exception as exc:  # noqa: BLE001 -- provider failure is data, not a crash
            self._calls += 1
            self._transcript.append({"at": time.time(), "ok": False,
                                     "error": f"{type(exc).__name__}: {exc}",
                                     "latency_ms": round((time.time() - started) * 1000, 2)})
            return ProposalResult((), self.proposed_by, model_calls=1,
                                  provider=self._config.provider, model=self._config.model,
                                  latency_ms=(time.time() - started) * 1000,
                                  failure=f"provider_error: {type(exc).__name__}",
                                  trace_ref=self._transcript_ref())
        self._calls += 1
        latency_ms = (time.time() - started) * 1000
        text = response["text"]
        usage = response["usage"]
        self._transcript.append({"at": time.time(), "ok": response["ok"], "latency_ms": round(latency_ms, 2),
                                 "usage": usage, "raw_len": len(text)})

        chosen = self._parse_candidates(text, allowed)
        if chosen is None:
            return ProposalResult((), self.proposed_by, model_calls=1,
                                  provider=self._config.provider, model=self._config.model,
                                  usage=usage, latency_ms=latency_ms,
                                  failure="llm_response_unparsable",
                                  trace_ref=self._transcript_ref())
        by_id = {str(e.get("candidate_id")): e for e in space}
        candidates: list[Candidate] = []
        for cid, rationale in chosen[: int(max_candidates)]:
            entry = by_id[cid]
            candidates.append(Candidate(
                candidate_id=cid,
                description=str(entry.get("description") or cid),
                params=dict(entry.get("params") or {}),
                rationale=rationale or str(entry.get("rationale") or "") or "llm proposal",
                proposed_by=self.proposed_by,
                llm_trace_ref=self._transcript_ref(),
            ))
        if not candidates:
            return ProposalResult((), self.proposed_by, model_calls=1, provider=self._config.provider,
                                  model=self._config.model, usage=usage, latency_ms=latency_ms,
                                  failure="llm_selected_no_known_candidate",
                                  trace_ref=self._transcript_ref())
        return ProposalResult(tuple(candidates), self.proposed_by, model_calls=1,
                              provider=self._config.provider, model=self._config.model,
                              usage=usage, latency_ms=latency_ms, trace_ref=self._transcript_ref())

    # -- internals -----------------------------------------------------------

    def _transcript_ref(self) -> str:
        return f"llm_transcript:{sha256_of(self._transcript)[:16]}"

    @staticmethod
    def _parse_candidates(text: str, allowed: Sequence[str]) -> list[tuple[str, str]] | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned.strip("`")
            cleaned = cleaned.lstrip("json").strip()
        try:
            payload = json.loads(cleaned)
        except ValueError:
            # Reasoning models sometimes wrap or prefix the JSON object; take the
            # first balanced object rather than giving up.
            payload = _first_json_object(cleaned)
            if payload is None:
                return None
        rows = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return None
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("candidate_id") or "")
            if cid not in allowed or cid in seen:
                continue  # a hallucinated identifier is dropped, never guessed at
            seen.add(cid)
            out.append((cid, str(row.get("rationale") or "")))
        return out

    def _post_chat(self, prompt: str) -> dict[str, Any]:
        import urllib.request
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": self._config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._temperature,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        })
        with urllib.request.urlopen(request, timeout=self._timeout) as handle:
            payload = json.loads(handle.read().decode("utf-8"))
        text = ""
        choices = payload.get("choices") or []
        if choices:
            text = str(((choices[0].get("message") or {}).get("content")) or "")
        return {"text": text, "usage": dict(payload.get("usage") or {}), "ok": bool(text)}


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Return the first balanced ``{...}`` block parsable as JSON, else None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        candidate = json.loads(text[start:index + 1])
                    except ValueError:
                        break
                    if isinstance(candidate, dict):
                        return candidate
                    break
        start = text.find("{", start + 1)
    return None


__all__ = ["DeterministicProposer", "LLMProposer", "LLMProviderConfig",
           "resolve_provider_config", "ProposalError", "DEFAULT_MAX_TOKENS"]
