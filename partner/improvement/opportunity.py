"""Versioned OpportunityRecord + EvidenceBundle for self_improvement and learning_improvement."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import uuid


class OpportunityType(str, Enum):
    REPAIR = "repair"
    OPTIMIZE = "optimize"
    EXTEND = "extend"
    TRANSFER = "transfer"


class OpportunityStatus(str, Enum):
    DRAFT = "draft"
    SELECTED = "selected"
    IN_EXPERIMENT = "in_experiment"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class EvidenceOrigin(str, Enum):
    SEALED_CYCLE = "sealed_project_cycle"
    RUNTIME_OBSERVATION = "runtime_observation"
    EXTERNAL_LEARNING = "external_learning_opportunity"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _files_with_hash(paths):
    out = []
    for p in paths:
        p = Path(p)
        if p.is_file():
            out.append({"path": str(p), "sha256": _sha(p)})
    return out


def _git_head():
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd="/mnt/e/work/partner", capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


@dataclass
class EvidenceBundle:
    bundle_id: str
    origin: str
    scope: str
    instance_id: str
    created_at: str = field(default_factory=_now)
    source_version: str = ""
    file_refs: list = field(default_factory=list)
    event_refs: list = field(default_factory=list)
    flow_refs: list = field(default_factory=list)
    job_refs: list = field(default_factory=list)
    bundle_boundary: str = ""
    deferred_stages: list = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    @classmethod
    def from_sealed_cycle(cls, cycle_manifest_path, ctx):
        cycle = Path(cycle_manifest_path).parent
        manifest = json.loads(Path(cycle_manifest_path).read_text())
        file_refs = []
        for item in manifest.get("files", []):
            p = Path(item["path"])
            if p.is_file() and _sha(p) == item.get("sha256"):
                file_refs.append({"path": str(p), "sha256": item["sha256"], "kind": "sealed_cycle"})
        return cls(
            bundle_id=f"bundle-{uuid.uuid4().hex[:12]}",
            origin=EvidenceOrigin.SEALED_CYCLE,
            scope=manifest.get("project_id", "partner"),
            instance_id=ctx.instance_id,
            source_version=f"partner@{_git_head() or 'unknown'}",
            file_refs=file_refs[:50],
            event_refs=[manifest.get("root_event_id", "")],
            flow_refs=[str(cycle / "sealed_cycle.json")] if (cycle / "sealed_cycle.json").exists() else [],
            job_refs=[ctx.job_id],
            bundle_boundary="Two bounded business rounds completed; sealed before self-evolution started.",
            deferred_stages=["audit", "counter", "design"],
        )

    @classmethod
    def from_runtime_observation(cls, instance_id, source_paths,
                                 boundary="internal only; no real QQ delivery"):
        return cls(
            bundle_id=f"bundle-{uuid.uuid4().hex[:12]}",
            origin=EvidenceOrigin.RUNTIME_OBSERVATION,
            scope="partner",
            instance_id=instance_id,
            source_version=f"partner@{_git_head() or 'unknown'}",
            file_refs=_files_with_hash(source_paths),
            bundle_boundary=boundary,
            deferred_stages=["observe_execute"],
        )

    @classmethod
    def from_external_learning(cls, instance_id, source_paths, source_uris,
                               boundary="external sources read; no side effects on partner runtime"):
        return cls(
            bundle_id=f"bundle-{uuid.uuid4().hex[:12]}",
            origin=EvidenceOrigin.EXTERNAL_LEARNING,
            scope="partner",
            instance_id=instance_id,
            source_version=f"partner@{_git_head() or 'unknown'}",
            file_refs=_files_with_hash(source_paths),
            extras={"source_uris": source_uris},
            bundle_boundary=boundary,
            deferred_stages=["source_retrieve", "source_read", "claim_crosscheck", "local_compare"],
        )

    def write(self, workspace):
        workspace = Path(workspace)
        out_dir = workspace / "state/improvement_evidence"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.bundle_id}.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        from partner.index.resource_catalog import register_runtime
        register_runtime(path, asdict(self))
        return str(path)


@dataclass
class OpportunityRecord:
    opportunity_id: str
    origin: str
    type: str
    instance_id: str
    scope: str
    capability: str
    current_behavior: str
    desired_behavior: str
    evidence_refs: list
    source_refs: list = field(default_factory=list)
    source_version: str = ""
    hypothesis: str = ""
    counterevidence: str = ""
    unknowns: list = field(default_factory=list)
    expected_value: str = ""
    cost_and_risk: str = ""
    minimum_probe: str = ""
    status: str = OpportunityStatus.DRAFT
    bundle_id: str = ""
    created_at: str = field(default_factory=_now)
    prompt_version: str = "opportunity.v1"

    def to_dict(self):
        return asdict(self)

    def write(self, workspace):
        workspace = Path(workspace)
        out_dir = workspace / "state/opportunities"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.opportunity_id}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        from partner.index.resource_catalog import register_runtime
        register_runtime(path, asdict(self))
        return str(path)

    @classmethod
    def load(cls, workspace, opportunity_id):
        path = Path(workspace) / "state/opportunities" / f"{opportunity_id}.json"
        if not path.exists():
            return None
        d = json.loads(path.read_text())
        return cls(**d)


def list_opportunities(workspace, instance_id="", status=""):
    workspace = Path(workspace)
    out_dir = workspace / "state/opportunities"
    if not out_dir.exists():
        return []
    out = []
    from partner.index.resource_catalog import ResourceCatalog
    for entry in ResourceCatalog(workspace).query('opportunity',limit=100):
        path=Path(entry['path'])
        if not path.name.endswith(".json"):
            continue
        try:
            d = json.loads(path.read_text())
        except Exception:
            continue
        if instance_id and d.get("instance_id") not in ("", instance_id):
            continue
        if status and d.get("status") != status:
            continue
        out.append(d)
    return out
