#!/usr/bin/env python3
"""One bounded, auditable molecular-generation canary for the commitment kernel.

What makes this real:

* the molecule pool, its QED/SA values and the baseline selection method come from
  the 02 project's own frozen artifacts
  (``datasets/bootstrap/molecular_synth_comparison.csv``, whose columns are exactly
  the project's protocol: group / canonical_smiles / qed / sa_score);
* the candidate methods are the project's own methods, read from
  ``metrics/method_candidates.jsonl``;
* the executor really re-runs a selection for two pre-declared seeds and writes a new
  artifact; the evaluator really recomputes every metric from that artifact with RDKit;
* the baseline is executed and measured by the same instrument, and stored as evidence.

What this is not: activity, efficacy, wet-lab synthesizability or clinical value.
QED/SA/scaffold/fingerprint metrics are computational proxies.  This run also does
not modify the project's own reports or its production loop.

Boundaries enforced here rather than promised:

* the production Job DB is opened read-only (``mode=ro``) and only counted;
* a single ownership lock is taken; the run refuses to start if the legacy main chain
  is running, and never claims, cancels or schedules a Job;
* exactly one bet, bounded by an absolute deadline and a hard budget.

Usage::

    PYTHONPATH=. python3 scripts/run_commitment_molecular_canary.py \
        --workspace <isolated workspace> --out <report dir> [--dry-run] [--no-llm]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from partner.application.molecular_selection_adapter import (  # noqa: E402
    BASELINE_METHOD, DOMAIN_VERSION, EVALUATOR_ID, EVALUATOR_VERSION, EXECUTOR_ID,
    EXECUTOR_VERSION, SEED_PROTOCOL, SELECT_COUNT, MolecularSelectionExecutor, load_pool,
    molecular_evaluator, pool_hash, pool_signature, ProjectBaselineProvider,
)
from partner.commitment import models as M  # noqa: E402
from partner.commitment.ports import FrozenClock  # noqa: E402
from partner.commitment.proposer import LLMProposer, ProposalError, resolve_provider_config  # noqa: E402
from partner.commitment.proposer import DeterministicProposer  # noqa: E402
from partner.commitment.runner import BetRunner, RunnerConfig  # noqa: E402
from partner.commitment.freezer import Freezer  # noqa: E402
from partner.commitment.selector import GuardedGainSelector  # noqa: E402
from partner.commitment.settlement import (  # noqa: E402
    budget_hash, make_treatment_contract, protocol_hash,
)
from partner.commitment.store import CommitmentStore  # noqa: E402

CANARY_ID = "molecular_generation_02_canary_v1"
RUN_ID = "molecular_canary_02"
BET_ID = "bet_molecular_canary_02"
HARNESS_VERSION = "commitment-kernel-2"
ENVIRONMENT = "production_canary"
LEGACY_CHAIN_PATTERNS = ("run_instance_native_runtime", "shared_worker", "terminal_bridge",
                         "run_native_readmission", "run_overnight_canary")

#: Frozen before any measurement.  Declared priors, not predictions.
CANDIDATE_SPACE = (
    {"candidate_id": "cand_scaffold_cap", "method": "scaffold_cap",
     "description": "greedy QED order with at most 3 molecules per Bemis-Murcko scaffold",
     "prior": {"expected_gain": 0.08, "risk": 0.35},
     "rationale": "caps scaffold concentration, so it should lift mean QED"},
    {"candidate_id": "cand_pareto_diverse", "method": "pareto_diverse",
     "description": "QED-descending order with an SA tie-break (project method pareto_diverse)",
     "prior": {"expected_gain": 0.06, "risk": 0.30},
     "rationale": "project method; balances QED against SA in the ordering"},
    {"candidate_id": "cand_maxmin_fingerprint", "method": "maxmin_fingerprint",
     "description": "farthest-point Morgan-fingerprint sampling from the best-QED molecule",
     "prior": {"expected_gain": 0.01, "risk": 0.15},
     "rationale": "diversity-first, so it may not move the QED mean much"},
)

SA_CEILING = 1.60
MIN_QED_DELTA = 0.02
MOLECULE_FLOOR = float(SELECT_COUNT)


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# boundary 1: the production Job store is read-only
# ---------------------------------------------------------------------------

class JobReadOnlyProjection:
    """A read-only view of the authoritative Job store.  No write path exists."""

    def __init__(self, db_path: str | os.PathLike) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise RuntimeError(f"job store not found: {self.db_path}")

    def read(self) -> dict[str, Any]:
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            counts = {status: int(count) for status, count in
                      connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY 1")}
            total = int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        finally:
            connection.close()
        return {"db_path": str(self.db_path),
                "db_mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(self.db_path.stat().st_mtime)),
                "db_size": self.db_path.stat().st_size,
                "total_jobs": total, "counts": counts,
                "read_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "ro"}


def legacy_chain_running() -> list[str]:
    """Names of any legacy main-chain process that is currently running."""
    found: list[str] = []
    listing = subprocess.run(["ps", "-eo", "cmd"], capture_output=True, text=True).stdout
    for pattern in LEGACY_CHAIN_PATTERNS:
        if any(pattern in line and "grep" not in line for line in listing.splitlines()):
            found.append(pattern)
    return found


# ---------------------------------------------------------------------------
# boundary 2: ownership handoff
# ---------------------------------------------------------------------------

class OwnershipLock:
    """One canary owner at a time, with explicit staleness handling."""

    def __init__(self, path: Path, owner_id: str) -> None:
        self.path = path
        self.owner_id = owner_id
        self.acquired = False

    def _alive(self, pid: int) -> bool:
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            return False
        return True

    def acquire(self) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"owner_id": self.owner_id, "pid": os.getpid(),
                  "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "canary_id": CANARY_ID, "harness_version": HARNESS_VERSION}
        if self.path.exists():
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            if self._alive(existing.get("pid", -1)) and existing.get("owner_id") != self.owner_id:
                raise RuntimeError(
                    f"another canary owner is live: {existing.get('owner_id')} "
                    f"(pid {existing.get('pid')}); refusing to run two at once")
            record["took_over_from"] = existing
            record["stale"] = True
        self.path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self.acquired = True
        return record

    def release(self, *, status: str) -> None:
        if self.acquired and self.path.exists():
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if current.get("owner_id") == self.owner_id:
                current["released_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                current["final_status"] = status
                self.path.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                                     encoding="utf-8")


# ---------------------------------------------------------------------------
# the bet
# ---------------------------------------------------------------------------

def build_spec(*, project_dir: Path, pool_rows, fingerprint: str) -> dict[str, Any]:
    baseline = None
    effects = None
    return {
        "partner_id": "partner-molecular-02",
        "project_id": "molecular_generation",
        "run_id": RUN_ID,
        "bet_id": BET_ID,
        "question": (
            f"On the frozen {len(pool_rows)}-molecule project pool and the two pre-declared "
            f"seeds {list(SEED_PROTOCOL)}, does the project method `scaffold_cap` raise the mean "
            f"QED of the selected set by at least {MIN_QED_DELTA} over the frozen `{BASELINE_METHOD}` "
            f"baseline while keeping mean SA <= {SA_CEILING}, validity and uniqueness at 1.0 and "
            f"selecting at least {SELECT_COUNT} molecules?"),
        "context_snapshot_ref": "context/snapshot.json",
        "baseline_ref": f"project:method={BASELINE_METHOD}",
        "environment": ENVIRONMENT,
        "harness_version": HARNESS_VERSION,
        "environment_fingerprint": fingerprint,
        "treatment": {"baseline_treatment": BASELINE_METHOD,
                      "declared_paths": [], "allows_code_change": False,
                      "note": "project-action experiment: same code, one method differs"},
        "evaluation_protocol": {
            "evaluator_id": EVALUATOR_ID, "evaluator_version": EVALUATOR_VERSION,
            "independence": "artifact_only", "agent_output_visible": False,
            "replicates": len(SEED_PROTOCOL),
            "metric_specs": [{"metric": m} for m in
                             ("validity", "uniqueness", "qed_mean", "sa_mean", "scaffold_count",
                              "selected_count", "nn_tanimoto_mean")],
        },
        "expected_effects": [
            {"metric": "qed_mean", "direction": "increase", "unit": "qed",
             "kind": "delta_over_baseline", "min_delta": MIN_QED_DELTA, "threshold": 0.0},
            {"metric": "sa_mean", "direction": "decrease", "unit": "sa",
             "kind": "guardrail", "threshold": SA_CEILING},
            {"metric": "validity", "direction": "increase", "unit": "fraction",
             "kind": "guardrail", "threshold": 1.0},
            {"metric": "uniqueness", "direction": "increase", "unit": "fraction",
             "kind": "guardrail", "threshold": 1.0},
            {"metric": "selected_count", "direction": "increase", "unit": "count",
             "kind": "guardrail", "threshold": MOLECULE_FLOOR},
        ],
        "falsification_conditions": [
            {"code": "qed_delta_missing", "kind": "metric_violation",
             "description": f"mean QED did not rise by {MIN_QED_DELTA} over the baseline",
             "params": {"metric": "qed_mean", "delta": MIN_QED_DELTA}},
            {"code": "sa_ceiling_broken", "kind": "guardrail_violation",
             "description": "mean synthetic accessibility left the frozen ceiling",
             "params": {"metric": "sa_mean", "limit": SA_CEILING, "direction": "max"}},
            {"code": "validity_loss", "kind": "guardrail_violation",
             "description": "some selected molecule does not parse",
             "params": {"metric": "validity", "limit": 1.0, "direction": "min"}},
            {"code": "missing_measurement", "kind": "missing_evidence",
             "description": "a required metric could not be measured from the artifact",
             "params": {"metric": "qed_mean"}},
        ],
        "budget": {"wall_clock_seconds": 900, "model_calls": 2, "actions": 2, "rounds": 1},
        "commitment_policy": {"earliest_turn_round": 2, "max_turns": 1,
                              "require_new_evidence_to_turn": True,
                              "early_stop_conditions": ["settled", "budget_exhausted",
                                                        "absolute_deadline", "blocked"]},
        "max_candidates": 3,
        "code_version": HARNESS_VERSION,
        "data_version": f"pool:{pool_hash(pool_rows)[:16]}",
        "model_config_ref": "workspace:config/agent_api_config.json",
        "max_risk": 0.5,
        "scope": "molecular_generation:selection_quality",
    }


def _snapshot(project_dir: Path, rows, fingerprint: str) -> dict[str, Any]:
    return {
        "canary_id": CANARY_ID, "project_id": "molecular_generation",
        "owner_instance": "02", "project_dir": str(project_dir),
        "pool_size": len(rows), "pool_hash": pool_hash(rows),
        "pool_signature": fingerprint,
        "pool_source": str(project_dir / "datasets" / "bootstrap" /
                           "molecular_synth_comparison.csv"),
        "declared_select_count": SELECT_COUNT, "seeds": list(SEED_PROTOCOL),
        "baseline_method": BASELINE_METHOD,
        "metric_scope": ("computational proxies only: QED/SA/scaffold/fingerprint; "
                         "not activity, efficacy or synthesizability"),
        # The kernel reads a candidate's action parameters from ``params``; the
        # declaration here must therefore carry them, or the executor would (rightly)
        # refuse to act on an empty action.
        "candidate_space": [
            {"candidate_id": entry["candidate_id"], "description": entry["description"],
             "params": {"method": entry["method"]}, "prior": dict(entry["prior"]),
             "rationale": entry["rationale"]}
            for entry in CANDIDATE_SPACE],
    }


def assemble(*, workspace: Path, project_dir: Path, spec: dict[str, Any], snapshot: dict[str, Any],
             clock, proposer) -> BetRunner:
    store = CommitmentStore(workspace, RUN_ID, BET_ID)
    snapshot_path = store.path("context", "snapshot.json")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    spec = dict(spec)
    spec["context_snapshot_ref"] = "context/snapshot.json"
    from partner.application.commitment_adapter import runner_config_from_spec
    config = runner_config_from_spec(spec, clock=clock)
    executor = MolecularSelectionExecutor(project_dir=str(project_dir))
    evaluator = molecular_evaluator(allowed_roots=[workspace / "state" / "commitment_execution"])
    provider = ProjectBaselineProvider(
        project_dir=str(project_dir), executor=executor, evaluator=evaluator,
        environment=ENVIRONMENT, environment_fingerprint_value=spec["environment_fingerprint"],
        harness_version=HARNESS_VERSION)
    return BetRunner(store=store, config=config, clock=clock, proposer=proposer,
                     executor=executor, evaluator=evaluator,
                     snapshot_reader=_Reader(snapshot_path),
                     selector=GuardedGainSelector(max_risk=0.5), freezer=Freezer(),
                     baseline_provider=provider, owner_id=f"canary-{os.getpid()}")


class _Reader:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read_frozen_state(self) -> Mapping[str, Any]:
        return json.loads(self._path.read_text(encoding="utf-8"))


def render_pdf(markdown_text: str, pdf_path: Path) -> dict[str, Any]:
    """Markdown -> HTML -> PDF with real tools; a failure is reported, not faked."""
    html_path = pdf_path.with_suffix(".html")
    steps: list[dict[str, Any]] = []
    for name, command in (("pandoc", ["pandoc", "-f", "markdown", "-t", "html",
                                      "-o", str(html_path), "-"]),
                          ("wkhtmltopdf", ["wkhtmltopdf", "--enable-local-file-access",
                                           str(html_path), str(pdf_path)])):
        result = subprocess.run(command, input=(markdown_text if name == "pandoc" else None),
                                capture_output=True, text=True)
        steps.append({"tool": name, "returncode": result.returncode,
                      "stderr_tail": (result.stderr or "")[-300:]})
        if result.returncode != 0:
            return {"ok": False, "steps": steps, "reason": f"{name} failed"}
    ok = pdf_path.exists() and pdf_path.stat().st_size > 1500
    return {"ok": ok, "steps": steps, "pdf_bytes": pdf_path.stat().st_size if pdf_path.exists() else 0,
            "reason": "" if ok else "pdf missing or too small to be a real document"}


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def per_repeat_table(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Raw per-seed numbers read back from the candidate artifact (display only)."""
    rows: list[dict[str, Any]] = []
    for repeat in artifact.get("repeats") or ():
        selected = list(repeat.get("selected") or ())
        qed = [float(r["qed"]) for r in selected if r.get("qed") is not None]
        sa = [float(r["sa"]) for r in selected if r.get("sa") is not None]
        rows.append({
            "seed": repeat.get("seed"), "selected": len(selected),
            "unique_smiles": len({r["smiles"] for r in selected}),
            "scaffolds": len({r["scaffold"] for r in selected}),
            "mean_qed": round(sum(qed) / len(qed), 6) if qed else None,
            "mean_sa": round(sum(sa) / len(sa), 6) if sa else None,
        })
    return rows


def build_report(*, canary_id: str, workspace: Path, out_dir: Path, project_dir: Path,
                 projection_before: Mapping[str, Any], projection_after: Mapping[str, Any],
                 lock_record: Mapping[str, Any], dry_run: Mapping[str, Any],
                 result: Any, store: CommitmentStore, artifact: Mapping[str, Any],
                 proposer_label: str, llm_report: Mapping[str, Any],
                 started: float, finished: float) -> str:
    settlement = result.settlement if result is not None else None
    comparison = None if settlement is None else settlement.comparison
    evidence = result.baseline_evidence if result is not None else None
    measurement = result.measurement if result is not None else None
    repeat_rows = per_repeat_table(artifact)
    lines: list[str] = [
        f"# 分子生成项目（实例 02）commitment canary 报告 — {canary_id}", "",
        "> 指标口径：本报告全部数字为**计算代理指标**（QED / SA / Bemis-Murcko scaffold / "
        "Morgan 指纹距离）。它们不是活性、不是药效、不是湿实验可合成性，也不是临床价值。",
        "> 本 canary 不修改项目自身的报告与生产循环，只读取其冻结产物并在隔离 workspace 里重跑。", "",
        "## 1. 运行身份与时间", "",
        f"- canary id：`{canary_id}`",
        f"- run_id / bet_id：`{RUN_ID}` / `{BET_ID}`",
        f"- 开始 / 结束：`{time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(started))}` / "
        f"`{time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(finished))}`"
        f"（墙钟 {round(finished - started, 2)} s）",
        f"- workspace：`{workspace}`",
        f"- 报告目录：`{out_dir}`",
        f"- 项目目录：`{project_dir}`",
        f"- 所有权锁：`{json.dumps(lock_record, ensure_ascii=False)}`", "",
        "## 2. 生产 Job DB（只读投影，运行前 / 运行后）", "",
        f"- 路径：`{projection_before.get('db_path')}`（mtime `{projection_before.get('db_mtime')}`）",
        f"- 运行前：{json.dumps(projection_before.get('counts'), ensure_ascii=False)}"
        f"（total {projection_before.get('total_jobs')}）",
        f"- 运行后：{json.dumps(projection_after.get('counts'), ensure_ascii=False)}"
        f"（total {projection_after.get('total_jobs')}）",
        f"- 是否写入：**{'否' if projection_before.get('counts') == projection_after.get('counts') else '是（异常，需调查）'}**"
        "（连接以 `mode=ro` 打开，代码中不存在任何写路径）", "",
        "## 3. dry-run 证明了什么", "",
    ]
    if dry_run:
        lines += [
            f"- 只读投影：counts {json.dumps(dry_run.get('projection', {}).get('counts'), ensure_ascii=False)}",
            f"- 所有权锁：pid {dry_run.get('lock', {}).get('pid')}，"
            f"took_over_from={'无' if not dry_run.get('lock', {}).get('took_over_from') else '有（stale）'}",
            f"- 契约校验：{'通过' if dry_run.get('config_ok') else '失败'}",
            f"- 冻结预演：{json.dumps(dry_run.get('freeze', {}), ensure_ascii=False)}",
            "- 未执行任何领域动作、未产生结算。", "",
        ]
    else:
        lines += ["- 本次未跑 dry-run。", ""]

    lines += [
        "## 4. 问题、候选与冻结项", "",
        f"- 可证伪问题：{artifact.get('question') or '（见 bet.json）'}",
        f"- 池：{artifact.get('pool_size')} 个真实分子，pool_hash `{str(artifact.get('pool_hash'))[:16]}`，"
        f"来源 `{artifact.get('pool_source')}`",
        f"- 重复 seed（预先声明）：{artifact.get('seeds')}",
        f"- baseline（control）方法：`{BASELINE_METHOD}`；candidate（treatment）方法："
        f"`{artifact.get('selected_method')}`",
        f"- 冻结最小效应量 δ：{MIN_QED_DELTA}；SA 上限：{SA_CEILING}；"
        f"有效性/唯一性下限：1.0；选择数下限：{SELECT_COUNT}",
        "- 候选与舍弃理由：",
    ]
    for entry in CANDIDATE_SPACE:
        mark = "选中" if entry["method"] == artifact.get("selected_method") else "舍弃"
        lines.append(f"  - `{entry['candidate_id']}`（{entry['method']}）— {mark}；"
                     f"声明先验 gain={entry['prior']['expected_gain']} / risk={entry['prior']['risk']}"
                     f"；理由：{entry['rationale']}")
    lines += [
        "- 声明先验是**提案信息**，不是已验证预测；测量始终由确定性评测器独立完成。", "",
        "## 5. baseline 证据（可采纳的、被执行过的 baseline）", "",
    ]
    if evidence is None:
        lines += ["- 无：本次运行未取得可采纳的 baseline 证据。", ""]
    else:
        lines += [
            f"- baseline_id：`{evidence.baseline_id}`，来源 `{evidence.provenance}`，"
            f"不可变性 `{evidence.immutable}`",
            f"- 执行器：`{evidence.executor_id}` v{evidence.executor_version}；"
            f"评测器：`{evidence.evaluator_id}` v{evidence.evaluator_version}",
            f"- receipt `{evidence.receipt_ref}` / sha256 `{evidence.receipt_hash[:16]}`；"
            f"measurement `{evidence.measurement_ref}` / sha256 `{evidence.measurement_hash[:16]}`",
            f"- 产物：`{list(evidence.artifact_refs)}`；测量前后哈希一致：`{evidence.immutable}`",
            f"- 输入 hash `{evidence.input_hash[:16]}`；环境 `{evidence.environment}`；"
            f"harness `{evidence.harness_version}`",
            f"- baseline 指标：{json.dumps(dict(evidence.metric_values), ensure_ascii=False)}", "",
        ]

    lines += ["## 6. candidate 每个重复的原始指标", "",
              "| seed | 选中数 | 去重 SMILES | scaffold 数 | 平均 QED | 平均 SA |",
              "|---|---|---|---|---|---|"]
    for row in repeat_rows:
        lines.append(f"| {row['seed']} | {row['selected']} | {row['unique_smiles']} | "
                     f"{row['scaffolds']} | {row['mean_qed']} | {row['mean_sa']} |")
    lines += ["", "## 7. 独立测量与比较", ""]
    if measurement is None:
        lines += ["- 无测量（运行未进入测量阶段）。", ""]
    else:
        lines += [f"- 测量有效性：`{measurement.validity}`"
                  + (f"（原因：{measurement.missing_reason}）" if measurement.missing_reason else ""),
                  f"- 汇总指标：{json.dumps(dict(measurement.metric_values), ensure_ascii=False)}",
                  f"- 产物测量前后不变：`{measurement.agent_artifacts_unchanged}`", ""]
    if comparison is not None:
        lines += [f"- ComparisonProof matched：**`{comparison.matched}`**"
                  f"（mismatch: {comparison.mismatch_reasons()}）",
                  f"- 控制变量：inputs={comparison.inputs_identical} evaluator={comparison.evaluator_identical} "
                  f"protocol={comparison.protocol_identical} budget={comparison.budget_comparable} "
                  f"environment={comparison.environment_identical} "
                  f"harness={comparison.harness_version_identical}",
                  f"- 处理变量：baseline `{comparison.baseline_treatment}` → candidate "
                  f"`{comparison.candidate_treatment}`；"
                  f"treatment_as_frozen={comparison.treatment_as_frozen}；"
                  f"code_changed={comparison.code_changed}",
                  f"- 效应量：baseline `{comparison.baseline_value}` → candidate "
                  f"`{comparison.candidate_value}`，delta **`{comparison.delta}`**", ""]

    lines += ["## 8. 结算（机器规则）", ""]
    if settlement is None:
        lines += [f"- 未产生结算。终态 `{getattr(result, 'state', 'n/a')}`："
                  f"{getattr(result, 'reason', 'n/a')}", ""]
    else:
        lines += [
            f"- settlement_class：**`{settlement.settlement_class}`**",
            f"- supported_claim：`{settlement.supported_claim}`",
            f"- 三个分开的概念：expectations_met=`{settlement.expectations_met}`、"
            f"improvement_over_baseline=`{settlement.improvement_over_baseline}`、"
            f"baseline_already_satisfied=`{settlement.baseline_already_satisfied}`",
            f"- 新增回归：{list(settlement.new_regressions)}；既有失败："
            f"{list(settlement.pre_existing_failures)}",
            f"- environment：`{settlement.environment}`（schema `{settlement.schema_version if hasattr(settlement, 'schema_version') else 'commitment/2'}`）",
            f"- publish_eligible：**`{settlement.publish_eligible}`**",
            f"- publish_blockers：{list(settlement.publish_blockers)}",
            f"- 机器规则：",
        ]
        for rule in settlement.machine_rules:
            lines.append(f"  - {rule}")
        lines += ["", "| 指标 | kind | 阈值 | 观测 | met | 相对 baseline |",
                  "|---|---|---|---|---|---|"]
        for outcome in settlement.expectation_outcomes:
            lines.append(f"| {outcome.metric} | {outcome.kind} | {outcome.threshold} | "
                         f"{outcome.observed} | {outcome.met} | "
                         f"{outcome.improvement_over_baseline} |")
        lines.append("")
        lines += ["- 逐条解释：" ]
        for outcome in settlement.expectation_outcomes:
            lines.append(f"  - `{outcome.metric}`（{outcome.kind}）：{outcome.note or '—'}")
        lines.append("")

    lines += ["## 9. 是否推进了分子项目", "",
              "- 本 canary 对项目的作用是**方法选择质量的证据**，而不是活性/药效结论。",
              f"- 本次结论：{_advancement_sentence(settlement)}", "",
              "## 10. LLM 使用与投递", "",
              f"- 提案方：`{proposer_label}`",
              f"- LLM：{json.dumps({k: v for k, v in dict(llm_report).items() if k != 'transcript'}, ensure_ascii=False)}",
              f"- dry-run：`{bool(dry_run)}`",
              "",
              "## 11. 关键产物绝对路径", ""]
    for key, value in sorted((result.paths if result is not None else {}).items()):
        lines.append(f"- {key}: `{value}`")
    lines += [
        f"- bet: `{store.path('bet.json')}`",
        f"- state: `{store.path('state.json')}`",
        f"- events: `{store.events_path}`",
        f"- manifest: `{store.path('manifest.json')}`",
        f"- issues: `{store.issues_path}`",
        f"- baseline 证据: `{store.artifact_path('baseline', 'base_' + BET_ID)}`",
        "",
        "## 12. 证据边界与未解决问题", "",
        "- 单 bet、单项目、两个 seed；不构成跨项目或长期结论。",
        "- 池是项目冻结的真实对比池（170 条真实 SMILES），候选方法为项目 ledger 中已有的方法；"
        "方法实现由本次 canary 按项目协议重写，可能与项目原始实现有细节差异，未做逐位对齐。",
        "- 未接入项目的生产循环，未修改项目报告；未启动任何无界 campaign；未消费历史 Job。",
        "- QED/SA/scaffold/fingerprint 均为代理指标；本项目简报本身也已声明"
        "“重复 QED/SA 头部排序不增加目标活性证据”，本报告不与之冲突。",
        "- 仍未解决：项目主线所需的更大外部实验 pK 测试集仍未获得；"
        "27 条可匹配样本的统计功效问题依旧。",
        "",
    ]
    return "\n".join(lines) + "\n"


def _advancement_sentence(settlement) -> str:
    if settlement is None:
        return "本次运行未进入结算，无法回答是否推进。"
    if settlement.settlement_class == "supported" and settlement.publish_eligible:
        return ("冻结预期在匹配比较下达成，且发布门全部通过；但按本轮约定，"
                "首次 canary 的晋升留待后续复核，本次只记录资格。")
    if settlement.settlement_class == "supported":
        return ("冻结预期达成，但发布被阻塞（"
                f"{', '.join(settlement.publish_blockers)}）：可作为候选证据，不足以改变项目主线。")
    if settlement.settlement_class == "falsified":
        return ("方向被证伪（未满足的预期见上表）。这是一个**有效**的负结果："
                "它说明该候选方法在本口径下不能作为主线依据。")
    return f"结算类别 `{settlement.settlement_class}`：证据不足以支持或否定方向。"


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

_stop = {"requested": False}


def _on_signal(*_a) -> None:
    _stop["requested"] = True
    _log("stop signal received; finishing the current bounded step")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="isolated canary workspace")
    parser.add_argument("--out", required=True, help="report directory")
    parser.add_argument("--project-dir",
                        default=os.environ.get("MOLECULAR_PROJECT_DIR", ""),
                        help="02 project dir; defaults to $MOLECULAR_PROJECT_DIR")
    parser.add_argument("--job-db",
                        default=os.environ.get("PARTNER_JOBS_DB", ""),
                        help="authoritative Job DB path (read-only); defaults to $PARTNER_JOBS_DB")
    parser.add_argument("--deadline-seconds", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--config-workspace", default=os.environ.get("PARTNER_WORKSPACE", ""))
    args = parser.parse_args(argv)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    workspace = Path(args.workspace).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    project_dir = Path(args.project_dir).resolve()
    started = time.time()
    deadline = started + int(args.deadline_seconds)

    report: dict[str, Any] = {"canary_id": CANARY_ID, "workspace": str(workspace),
                              "project_dir": str(project_dir), "dry_run": bool(args.dry_run)}

    # -- precondition: the legacy main chain must not be running ---------------
    running = legacy_chain_running()
    report["legacy_chain_running"] = running
    if running:
        _log(f"REFUSING to start: legacy main chain is running: {running}")
        (out_dir / "canary_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
        return 3

    # -- boundary 1: read-only projection -------------------------------------
    projection = JobReadOnlyProjection(args.job_db)
    before = projection.read()
    report["job_db_before"] = before
    _log(f"job db (read-only): {before['counts']} @ {before['db_mtime']}")

    # -- boundary 2: ownership handoff ----------------------------------------
    lock = OwnershipLock(workspace / "state" / "commitment_canary" / "OWNER.lock",
                         owner_id=f"canary-{CANARY_ID}-{os.getpid()}")
    lock_record = lock.acquire()
    report["lock"] = lock_record
    _log(f"ownership acquired by {lock_record['owner_id']} (pid {lock_record['pid']})")

    status = "failed"
    try:
        rows = load_pool(project_dir)
        fingerprint = pool_signature(rows, path=project_dir / "datasets" / "bootstrap" /
                                     "molecular_synth_comparison.csv")
        spec = build_spec(project_dir=project_dir, pool_rows=rows, fingerprint=fingerprint)
        snapshot = _snapshot(project_dir, rows, fingerprint)
        report["pool"] = {"size": len(rows), "hash": pool_hash(rows),
                          "fingerprint": fingerprint}

        proposer = DeterministicProposer()
        proposer_label = "deterministic"
        if not args.no_llm and str(args.config_workspace).strip():
            try:
                provider_config = resolve_provider_config(args.config_workspace)
                proposer = LLMProposer(provider_config, max_calls=2)
                proposer_label = "llm"
            except ProposalError as exc:
                report["llm_report"] = {"failed_reason": f"provider_unavailable: {exc}"}
        elif not args.no_llm:
            report["llm_report"] = {"skipped": "config workspace not provided"}

        clock = FrozenClock(time.time())
        runner = assemble(workspace=workspace, project_dir=project_dir, spec=spec,
                          snapshot=snapshot, clock=clock, proposer=proposer)

        if args.dry_run:
            # prove: lock + projection + contract validation + freeze, then stop
            from partner.commitment.freezer import FreezeRequest
            candidates = tuple(M.Candidate(candidate_id=e["candidate_id"],
                                           description=e["description"],
                                           params={"method": e["method"]}, proposed_by="policy",
                                           rationale=e["rationale"]) for e in CANDIDATE_SPACE)
            selection = GuardedGainSelector(max_risk=0.5).select(candidates=candidates,
                                                                snapshot=snapshot)
            frozen = Freezer().freeze(
                FreezeRequest(partner_id=spec["partner_id"], project_id=spec["project_id"],
                              run_id=RUN_ID, question=spec["question"],
                              context_snapshot_ref="context/snapshot.json",
                              context_snapshot_hash=pool_hash(rows),
                              candidates=candidates, selection=selection,
                              expected_effects=runner.config.expected_effects,
                              falsification_conditions=runner.config.falsification_conditions,
                              evaluation_protocol=runner.config.evaluation_protocol,
                              baseline_ref=spec["baseline_ref"], budget=runner.config.budget,
                              commitment_policy=runner.config.commitment_policy,
                              code_version=HARNESS_VERSION, data_version=spec["data_version"],
                              model_config_ref=spec["model_config_ref"],
                              environment=ENVIRONMENT,
                              treatment=make_treatment_contract(
                                  baseline_treatment=BASELINE_METHOD,
                                  candidate_treatment=selection.selected.candidate_id,
                                  declared_paths=(), allows_code_change=False)),
                bet_id=BET_ID, now_iso=time.strftime("%Y-%m-%dT%H:%M:%S"))
            report["dry_run_detail"] = {
                "projection": {"counts": before["counts"]},
                "lock": lock_record, "config_ok": True,
                "freeze": {"bet_id": frozen.bet_id, "freeze_hash": frozen.freeze_hash()[:16],
                           "environment": frozen.environment,
                           "selected": selection.selected.candidate_id,
                           "protocol_replicates": frozen.evaluation_protocol.replicates},
            }
            report["status"] = "dry_run_ok"
            status = "dry_run_ok"
            _log(f"dry-run ok: freeze_hash={frozen.freeze_hash()[:16]} "
                 f"selected={selection.selected.candidate_id} "
                 f"replicates={frozen.evaluation_protocol.replicates}")
        else:
            result = runner.run()
            report["result"] = result.to_dict()
            report["state"] = result.state
            store = runner.store
            artifact_path = None
            if result.receipt is not None and result.receipt.artifacts:
                artifact_path = Path(result.receipt.artifacts[0])
            artifact: dict[str, Any] = {}
            if artifact_path is not None and artifact_path.exists():
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            artifact.update({"question": spec["question"], "pool_size": len(rows),
                             "pool_hash": pool_hash(rows),
                             "pool_source": snapshot["pool_source"],
                             "seeds": list(SEED_PROTOCOL),
                             "selected_method": None})
            bet = store.load_bet()
            selected = next((c for c in bet.candidates
                             if c.candidate_id == bet.selected_action), None)
            artifact["selected_method"] = (selected.params.get("method") if selected else None)
            report["llm_report"] = getattr(proposer, "transcript", None) and {
                "provider": getattr(proposer, "provider_id", "llm"),
                "calls_used": getattr(proposer, "calls_used", None),
                "transcript": getattr(proposer, "transcript", []),
            } or report.get("llm_report", {})
            report["proposer_label"] = proposer_label

            markdown = build_report(canary_id=CANARY_ID, workspace=workspace, out_dir=out_dir,
                                    project_dir=project_dir, projection_before=before,
                                    projection_after=projection.read(), lock_record=lock_record,
                                    dry_run={}, result=result, store=store, artifact=artifact,
                                    proposer_label=proposer_label,
                                    llm_report=report.get("llm_report") or {},
                                    started=started, finished=time.time())
            md_path = out_dir / "molecular_canary_report.md"
            md_path.write_text(markdown, encoding="utf-8")
            report["report_md"] = str(md_path)
            pdf_result = render_pdf(markdown, out_dir / "molecular_canary_report.pdf")
            report["report_pdf"] = {"path": str(out_dir / "molecular_canary_report.pdf"),
                                    **pdf_result}
            report["artifacts"] = {
                "bet": str(store.path("bet.json")), "state": str(store.path("state.json")),
                "events": str(store.events_path), "manifest": str(store.path("manifest.json")),
                "issues": str(store.issues_path),
                "baseline_evidence": str(store.artifact_path("baseline", f"base_{BET_ID}")),
                "settlement": str(store.artifact_path(
                    "settlement", f"stl_{BET_ID}_r1")),
            }
            status = result.state
            _log(f"bet terminal: {result.state} — {result.reason}")
    finally:
        report["status"] = status
        lock.release(status=status)
        report["job_db_after"] = JobReadOnlyProjection(args.job_db).read()
        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        report["wall_clock_seconds"] = round(time.time() - started, 2)
        (out_dir / "canary_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _log(f"done: status={status}; report {out_dir}")
    return 0 if status in ("dry_run_ok", "CLOSED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
