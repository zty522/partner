"""Project-centred application boundary.

Public surface:
- ``PartnerApplicationService`` / ``Submission`` — the legacy intent
  flow that callers can still hit directly.
- ``orchestrate_submit`` — the unified submission entry point used by
  the Web API, the CLI, the QQ adapter, and the benchmark runner.  All
  paths route through this function so identity / authorisation /
  idempotency / intent / projection live in one place.
- ``compute_fingerprint`` / ``canonical_payload`` — the canonicalised
  payload hash used for ``request_id`` idempotency.
"""
from .service import PartnerApplicationService, Submission
from .views import ApplicationCommand, ArtifactView, EventDetail, JobView, UserUpdate
from .read_model import ApplicationReadModel
from .orchestrator import (
    orchestrate_submit, SubmissionResult,
    IdempotencyConflict, IdempotencyScopeError, UnauthorisedInstanceError,
    compute_fingerprint, canonical_payload,
)

__all__ = [
    "ApplicationCommand", "ArtifactView", "EventDetail", "JobView",
    "ApplicationReadModel", "PartnerApplicationService", "Submission",
    "UserUpdate",
    "orchestrate_submit", "SubmissionResult",
    "IdempotencyConflict", "IdempotencyScopeError", "UnauthorisedInstanceError",
    "compute_fingerprint", "canonical_payload",
]
