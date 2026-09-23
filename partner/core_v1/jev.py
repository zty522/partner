"""Typed Jev adapter.

Jev is advisory in Core v1.  Its answer is recorded with probabilities but can
never establish a scientific result, grant permission, promote code or settle a
commitment.  Missing credentials and provider failures degrade to an explicit
unavailable judgment.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import os
import time

from .models import CoreMode, DecisionState, TypedJudgment


Transport = Callable[[str, Mapping[str, str], Mapping[str, Any], float], Mapping[str, Any]]


def _http_transport(url: str, headers: Mapping[str, str], payload: Mapping[str, Any],
                    timeout: float) -> Mapping[str, Any]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers=dict(headers), method="POST")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured HTTPS API
        return json.loads(response.read().decode("utf-8"))


@dataclass(frozen=True)
class JevConfig:
    mode: CoreMode = CoreMode.SHADOW
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    model: str = "jev-latest"
    api_key_env: str = "TYPESAFE_API_KEY"
    timeout_seconds: float = 10.0


class JevClient:
    def __init__(self, config: JevConfig | None = None, *, transport: Transport | None = None,
                 environ: Mapping[str, str] | None = None) -> None:
        self.config = config or JevConfig()
        self.transport = transport or _http_transport
        self.environ = environ if environ is not None else os.environ

    @staticmethod
    def core_questions() -> dict[str, Any]:
        return {
            "route": {"type": "choice", "instructions": "Which bounded route best matches the supplied evidence?",
                      "criteria": {
                          "continue_project": "Executable project action with adequate evidence and budget",
                          "active_learning": "A resolvable external knowledge or source gap blocks progress",
                          "self_evolution": "A reproducible Partner mechanism defect blocks progress",
                          "waiting": "No safe, evidence-backed action is ready",
                          "complete": "The declared objective has verified completion evidence",
                      }},
            "epistemic_gap": {"type": "noul", "instructions": "Is the blocker primarily missing external knowledge or source evidence?"},
            "mechanism_defect": {"type": "noul", "instructions": "Is there evidence of a reproducible defect in Partner itself?"},
            "readiness": {"type": "score", "instructions": "How ready is the selected action for bounded execution?",
                          "criteria": ["not ready", "major evidence missing", "ready with review", "ready"]},
        }

    def evaluate(self, state: DecisionState, *, questions: Mapping[str, Any] | None = None) -> TypedJudgment:
        if self.config.mode == CoreMode.DISABLED:
            return TypedJudgment("disabled", self.config.model, {}, reason="Jev disabled")
        api_key = str(self.environ.get(self.config.api_key_env) or "").strip()
        if not api_key:
            return TypedJudgment("unavailable", self.config.model, {},
                                 reason=f"missing {self.config.api_key_env}")
        payload = {"model": self.config.model, "state": state.to_dict(),
                   "questions": dict(questions or self.core_questions())}
        started = time.monotonic()
        try:
            response = self.transport(
                self.config.endpoint,
                {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                payload, self.config.timeout_seconds)
            answers = response.get("answers")
            if not isinstance(answers, dict) or not answers:
                raise ValueError("Jev response contains no typed answers")
            confidences = [float(v.get("confidence", 0.0)) for v in answers.values()
                           if isinstance(v, dict) and "confidence" in v]
            confidence = min(confidences) if confidences else 0.0
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            return TypedJudgment(
                "completed", str(response.get("model") or self.config.model), answers,
                confidence=max(0.0, min(1.0, confidence)),
                usage={str(k): int(v) for k, v in usage.items()},
                latency_ms=(time.monotonic() - started) * 1000,
                authoritative=self.config.mode == CoreMode.GATED)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return TypedJudgment("unavailable", self.config.model, {},
                                 latency_ms=(time.monotonic() - started) * 1000,
                                 reason=f"{type(exc).__name__}: {exc}")
