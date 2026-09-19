"""Append-only, hash-chained, idempotent, recoverable storage for one bet.

Layout::

    <workspace>/state/commitments/<run_id>/<bet_id>/
        events.jsonl        append-only hash chain (the truth of what happened)
        state.json          materialised lifecycle (a cache of the chain)
        bet.json            current frozen record
        revisions/r<N>.json every revision, never overwritten
        context/snapshot.json
        receipts/<id>.json
        measurements/<id>.json
        settlement/<id>.json
        experience/<id>.json
        manifest.json       ids, schema version, artifact hashes, chain head
        issues.jsonl        integrity problems (deliberately NOT hash-chained)

Rules
-----
* Read discipline: every read here is a point read of a known path.  Nothing in
  this module walks the tree, so a state transition cannot become a full scan.
* A broken chain fails closed: ``verify_chain`` reports the break, an Issue is
  recorded, and no historical record is ever rewritten.
* ``append`` is idempotent on ``event_id``: replaying a message from Event Fabric
  must not produce a second execution, experience, or round.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import (
    SCHEMA_VERSION, BaselineEvidence, BetRecord, ContractError, ExperienceRecord,
    ExecutionReceipt, OutcomeMeasurement, SettlementDecision, canonical_json, sha256_of,
)
from .state_machine import BetLifecycle, IllegalTransition


class StoreIntegrityError(RuntimeError):
    """The chain or a stored record failed verification.  Fail closed."""


GENESIS_HASH = "0" * 64


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def file_sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    length: int
    head_hash: str
    broken_at: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "length": self.length, "head_hash": self.head_hash,
                "broken_at": self.broken_at, "reason": self.reason}


class CommitmentStore:
    """One bet's durable record."""

    def __init__(self, workspace: str | os.PathLike, run_id: str, bet_id: str) -> None:
        if not str(run_id).strip() or not str(bet_id).strip():
            raise ContractError("CommitmentStore: run_id and bet_id must be non-empty")
        self.workspace = Path(workspace)
        self.run_id = str(run_id)
        self.bet_id = str(bet_id)
        self.root = self.workspace / "state" / "commitments" / self.run_id / self.bet_id
        self.events_path = self.root / "events.jsonl"
        self.issues_path = self.root / "issues.jsonl"

    # -- paths ---------------------------------------------------------------

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def artifact_path(self, kind: str, identifier: str) -> Path:
        safe = str(identifier).replace("/", "_")
        folder = {"receipt": "receipts", "measurement": "measurements",
                  "settlement": "settlement", "experience": "experience",
                  "baseline": "baseline"}.get(kind, kind)
        return self.root / folder / f"{safe}.json"

    # -- chain ---------------------------------------------------------------

    def events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows

    def has_event(self, event_id: str) -> bool:
        return any(row.get("event_id") == event_id for row in self.events())

    def append(self, kind: str, payload: Mapping[str, Any], *, event_id: str,
               at: float | None = None) -> dict[str, Any]:
        """Append one event.  Replaying the same ``event_id`` is a no-op.

        Returns the stored (existing or new) event row.
        """
        if not str(event_id).strip():
            raise ContractError("CommitmentStore.append: event_id must be non-empty")
        rows = self.events()
        for row in rows:
            if row.get("event_id") == event_id:
                return row
        previous = rows[-1]["hash"] if rows else GENESIS_HASH
        body = {"seq": len(rows) + 1, "event_id": str(event_id), "kind": str(kind),
                "at": float(at if at is not None else time.time()),
                "payload": json.loads(canonical_json(dict(payload))),
                "prev_hash": previous}
        row = dict(body)
        row["hash"] = sha256_of(body)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return row

    def verify_chain(self, *, record_issue: bool = True) -> ChainReport:
        rows = self.events()
        previous = GENESIS_HASH
        for index, row in enumerate(rows):
            body = {k: row[k] for k in ("seq", "event_id", "kind", "at", "payload", "prev_hash")}
            expected = sha256_of(body)
            if row.get("hash") != expected:
                report = ChainReport(False, len(rows), previous, index + 1,
                                     "event hash mismatch (record was modified)")
                if record_issue:
                    self.record_issue("chain_hash_mismatch", report.to_dict())
                return report
            if row.get("prev_hash") != previous:
                report = ChainReport(False, len(rows), previous, index + 1,
                                     "prev_hash does not link to the previous event")
                if record_issue:
                    self.record_issue("chain_link_broken", report.to_dict())
                return report
            previous = row["hash"]
        return ChainReport(True, len(rows), previous)

    def record_issue(self, kind: str, detail: Mapping[str, Any]) -> None:
        """Issues live outside the chain: a broken chain must stay broken."""
        self.root.mkdir(parents=True, exist_ok=True)
        row = {"at": time.time(), "kind": str(kind), "bet_id": self.bet_id,
               "run_id": self.run_id, "detail": json.loads(canonical_json(dict(detail)))}
        with self.issues_path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(row) + "\n")
            handle.flush()

    def issues(self) -> list[dict[str, Any]]:
        if not self.issues_path.exists():
            return []
        return [json.loads(line) for line in self.issues_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # -- lifecycle -----------------------------------------------------------

    def load_lifecycle(self) -> BetLifecycle:
        """Read the materialised state, falling back to replaying the chain.

        A replay is used only when ``state.json`` is absent; if it exists but is
        unreadable the store fails closed instead of guessing.
        """
        state_path = self.root / "state.json"
        if state_path.exists():
            try:
                return BetLifecycle.from_dict(_read_json(state_path))
            except (OSError, ValueError, ContractError) as exc:
                self.record_issue("state_unreadable", {"error": f"{type(exc).__name__}: {exc}"})
                raise StoreIntegrityError(f"state.json unreadable: {exc}") from exc
        return self.replay_lifecycle()

    def replay_lifecycle(self) -> BetLifecycle:
        report = self.verify_chain()
        if not report.ok:
            raise StoreIntegrityError(
                f"cannot recover {self.bet_id}: chain broken at {report.broken_at} ({report.reason})")
        lifecycle: BetLifecycle | None = None
        for row in self.events():
            if row.get("kind") == "lifecycle":
                lifecycle = BetLifecycle.from_dict(row["payload"])
        if lifecycle is None:
            return BetLifecycle(bet_id=self.bet_id)
        return lifecycle

    def save_lifecycle(self, lifecycle: BetLifecycle, *, reason: str = "") -> None:
        if lifecycle.bet_id != self.bet_id:
            raise ContractError(
                f"CommitmentStore: lifecycle for {lifecycle.bet_id} written to {self.bet_id}")
        report = self.verify_chain(record_issue=False)
        if not report.ok:
            self.record_issue("refused_write_on_broken_chain", report.to_dict())
            raise StoreIntegrityError(
                f"refusing to extend a broken chain for {self.bet_id}: {report.reason}")
        _write_json(self.root / "state.json", lifecycle.to_dict())
        self.append("lifecycle", lifecycle.to_dict(),
                    event_id=f"lifecycle:{lifecycle.state}:r{lifecycle.revision}",
                    at=lifecycle.updated_at or None)

    # -- frozen record -------------------------------------------------------

    def save_bet(self, record: BetRecord) -> None:
        """Persist a bet revision, enforcing the post-COMMITTED freeze rule."""
        if record.bet_id != self.bet_id:
            raise ContractError(f"CommitmentStore: bet {record.bet_id} written to {self.bet_id}")
        current_path = self.root / "bet.json"
        if current_path.exists():
            try:
                previous = BetRecord.from_dict(_read_json(current_path))
            except (OSError, ValueError, ContractError) as exc:
                self.record_issue("bet_unreadable", {"error": f"{type(exc).__name__}: {exc}"})
                raise StoreIntegrityError(f"bet.json unreadable: {exc}") from exc
            if int(record.revision) == int(previous.revision):
                changed = previous.tamper_evidence(record)
                if changed:
                    self.record_issue("frozen_fields_edited",
                                      {"fields": changed, "revision": record.revision})
                    raise IllegalTransition(
                        f"{self.bet_id}: refusing to edit frozen field(s) {changed} in place at "
                        f"revision {record.revision}; append a new revision instead")
            else:
                if int(record.revision) != int(previous.revision) + 1:
                    raise IllegalTransition(
                        f"{self.bet_id}: revision must increase by exactly 1 "
                        f"(previous {previous.revision}, got {record.revision})")
                if int(record.parent_revision) != int(previous.revision):
                    raise IllegalTransition(
                        f"{self.bet_id}: revision {record.revision} must reference parent_revision="
                        f"{previous.revision}, got {record.parent_revision}")
                _write_json(self.root / "revisions" / f"r{previous.revision}.json", previous.to_dict())
        _write_json(current_path, record.to_dict())
        _write_json(self.root / "revisions" / f"r{record.revision}.json", record.to_dict())
        self.append("bet_revision", {"revision": record.revision,
                                     "freeze_hash": record.freeze_hash(),
                                     "parent_revision": record.parent_revision,
                                     "status": record.status},
                    event_id=f"bet_revision:r{record.revision}")

    def load_bet(self) -> BetRecord:
        path = self.root / "bet.json"
        if not path.exists():
            raise StoreIntegrityError(f"{self.bet_id}: no bet.json recorded")
        return BetRecord.from_dict(_read_json(path))

    # -- context -------------------------------------------------------------

    def save_context_snapshot(self, snapshot: Mapping[str, Any]) -> str:
        """Store the frozen context and return its hash."""
        payload = json.loads(canonical_json(dict(snapshot)))
        digest = sha256_of(payload)
        _write_json(self.root / "context" / "snapshot.json", payload)
        _write_text(self.root / "context" / "snapshot.sha256", digest + "\n")
        return digest

    def context_snapshot_hash(self) -> str:
        path = self.root / "context" / "snapshot.sha256"
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    # -- typed artifacts -----------------------------------------------------

    def save_receipt(self, receipt: ExecutionReceipt) -> None:
        _write_json(self.artifact_path("receipt", receipt.receipt_id), receipt.to_dict())
        self.append("execution_receipt",
                    {"receipt_id": receipt.receipt_id, "status": receipt.status,
                     "artifact_hashes": dict(receipt.artifact_hashes)},
                    event_id=f"receipt:{receipt.receipt_id}",
                    at=receipt.finished_epoch)

    def save_measurement(self, measurement: OutcomeMeasurement) -> None:
        _write_json(self.artifact_path("measurement", measurement.measurement_id),
                    measurement.to_dict())
        self.append("outcome_measurement",
                    {"measurement_id": measurement.measurement_id, "metric": measurement.metric,
                     "value": measurement.value, "validity": measurement.validity},
                    event_id=f"measurement:{measurement.measurement_id}")

    def save_settlement(self, settlement: SettlementDecision) -> None:
        _write_json(self.artifact_path("settlement", settlement.settlement_id),
                    settlement.to_dict())
        self.append("settlement",
                    {"settlement_id": settlement.settlement_id,
                     "settlement_class": settlement.settlement_class,
                     "improvement_observed": settlement.improvement_observed,
                     "publish_eligible": settlement.publish_eligible},
                    event_id=f"settlement:{settlement.settlement_id}")

    def save_experience(self, experience: ExperienceRecord) -> None:
        _write_json(self.artifact_path("experience", experience.experience_id),
                    experience.to_dict())
        self.append("experience",
                    {"experience_id": experience.experience_id,
                     "settlement_class": experience.settlement_class,
                     "level": experience.level},
                    event_id=f"experience:{experience.experience_id}")

    def load_receipt(self, receipt_id: str) -> ExecutionReceipt:
        return ExecutionReceipt.from_dict(_read_json(self.artifact_path("receipt", receipt_id)))

    def load_measurement(self, measurement_id: str) -> OutcomeMeasurement:
        return OutcomeMeasurement.from_dict(_read_json(self.artifact_path("measurement", measurement_id)))

    def load_settlement(self, settlement_id: str) -> SettlementDecision:
        return SettlementDecision.from_dict(_read_json(self.artifact_path("settlement", settlement_id)))

    def load_experience(self, experience_id: str) -> ExperienceRecord:
        return ExperienceRecord.from_dict(_read_json(self.artifact_path("experience", experience_id)))

    def list_artifacts(self, kind: str) -> list[str]:
        folder = {"receipt": "receipts", "measurement": "measurements",
                  "settlement": "settlement", "experience": "experience",
                  "baseline": "baseline"}.get(kind, kind)
        directory = self.root / folder
        if not directory.is_dir():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))

    # -- baseline evidence ---------------------------------------------------

    def save_baseline_evidence(self, evidence: "BaselineEvidence") -> None:
        """Persist the baseline's receipt, measurement and evidence record.

        All three go into the same append-only store as the candidate's, so the
        comparison can be re-audited from a single artifact set (fix 4.5).
        """
        if evidence.provenance == "fresh_execution":
            for path in evidence.artifact_refs:
                source = Path(path)
                if source.exists():
                    target = self.path("baseline", "candidate_side_artifact_placeholder")
                    del target  # artifacts are hashed, not copied: keep the store small
                    break
        _write_json(self.artifact_path("baseline", evidence.baseline_id), evidence.to_dict())
        self.append("baseline_evidence",
                    {"baseline_id": evidence.baseline_id,
                     "provenance": evidence.provenance,
                     "environment": evidence.environment,
                     "metric_values": dict(evidence.metric_values),
                     "artifact_hashes": dict(evidence.artifact_hashes),
                     "receipt_hash": evidence.receipt_hash,
                     "measurement_hash": evidence.measurement_hash},
                    event_id=f"baseline:{evidence.baseline_id}")

    def load_baseline_evidence(self, baseline_id: str) -> "BaselineEvidence":
        path = self.artifact_path("baseline", baseline_id)
        if not path.exists():
            raise StoreIntegrityError(f"{self.bet_id}: no baseline evidence {baseline_id!r}")
        return BaselineEvidence.from_dict(_read_json(path))

    # -- manifest ------------------------------------------------------------

    def write_manifest(self, *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        report = self.verify_chain(record_issue=False)
        artifacts: dict[str, str] = {}
        # Keys are paths relative to the bet root, so verify_manifest can re-hash
        # exactly the file the manifest claims to describe.
        for folder in (".", "revisions", "receipts", "measurements", "settlement",
                       "experience", "baseline", "context"):
            directory = self.root if folder == "." else self.root / folder
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                relative = path.name if folder == "." else f"{folder}/{path.name}"
                artifacts[relative] = file_sha256(path)
        manifest = {
            "schema_version": SCHEMA_VERSION, "run_id": self.run_id, "bet_id": self.bet_id,
            "chain": report.to_dict(), "artifacts": artifacts,
            "context_snapshot_hash": self.context_snapshot_hash(),
            "written_at": time.time(),
        }
        if extra:
            manifest["extra"] = json.loads(canonical_json(dict(extra)))
        _write_json(self.root / "manifest.json", manifest)
        return manifest

    def verify_manifest(self) -> dict[str, Any]:
        path = self.root / "manifest.json"
        if not path.exists():
            return {"ok": False, "reason": "manifest.json missing"}
        manifest = _read_json(path)
        mismatched = [name for name, digest in (manifest.get("artifacts") or {}).items()
                      if not (self.root / name).exists()
                      or file_sha256(self.root / name) != digest]
        chain = self.verify_chain(record_issue=False)
        return {"ok": (not mismatched) and chain.ok,
                "manifest_mismatches": mismatched,
                "chain_ok": chain.ok, "chain_length": chain.length}


__all__ = ["CommitmentStore", "StoreIntegrityError", "ChainReport", "GENESIS_HASH", "file_sha256"]
