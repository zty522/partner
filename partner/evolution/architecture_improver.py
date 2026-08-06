"""Architecture Improver — 根据改进方案执行实际改进操作。

支持三个实施层面和 A+B 方案：
- A 方案：低风险（config 层面）在自进化周期末尾自动应用
- B 方案：中/高风险（Prompt/代码层面）通过 Event 触发，需用户审批

风险等级：
- low: 修改 YAML 配置文件，自动应用，无需审批
- medium: 修改 Prompt 模板，需用户审批
- high: 修改代码，需用户审批
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class ImprovementResult:
    applied_count: int = 0
    rejected_count: int = 0
    config_changes: list[dict] = field(default_factory=list)
    prompt_changes: list[dict] = field(default_factory=list)
    code_pending: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ArchitectureImprover:
    """Applies architecture improvements to Partner's config, prompts, and code."""

    def __init__(self, workspace: str = ""):
        self.workspace = workspace
        self._db_path = self._resolve_db_path()

    def _resolve_db_path(self) -> str:
        """Resolve the learning.db path."""
        env = os.environ.get("PARTNER_DATA_DIR", "")
        if env:
            return os.path.join(env, "learning.db")
        if self.workspace:
            return os.path.join(self.workspace, "partner_data", "learning.db")
        return os.path.expanduser("~/.partner/learning.db")

    def apply_improvements(
        self, max_risk_level: str = "low", require_approval: bool = False
    ) -> ImprovementResult:
        """Apply all pending improvements up to a risk level.

        A 方案: max_risk_level='low', require_approval=False
           自动应用所有低风险配置改进，无需审批。

        B 方案: max_risk_level='high', require_approval=True
           列出所有待处理改进，需用户审批后执行。
        """
        pending = self._load_pending_improvements()
        filtered = [p for p in pending if self._risk_level_ok(p, max_risk_level)]

        if not filtered:
            logger.info("[ARCH_IMPROVER] no pending improvements to apply")
            return ImprovementResult()

        if require_approval:
            return self._apply_with_approval(filtered)
        else:
            return self._apply_directly(filtered)

    def apply_specific(
        self, improvement_ids: list[str], require_approval: bool = True
    ) -> ImprovementResult:
        """Apply specific improvements by ID."""
        pending = self._load_pending_improvements()
        selected = [p for p in pending if p.get("id") in improvement_ids]
        if not selected:
            return ImprovementResult(errors=[f"No improvements found for IDs: {improvement_ids}"])
        if require_approval:
            return self._apply_with_approval(selected)
        else:
            return self._apply_directly(selected)

    def get_pending_summary(self, max_risk_level: str = "high") -> str:
        """Generate a human-readable summary of pending improvements for user approval."""
        pending = self._load_pending_improvements()
        filtered = [p for p in pending if self._risk_level_ok(p, max_risk_level)]

        if not filtered:
            return "没有待处理的架构改进方案。"

        lines = [f"检测到 {len(filtered)} 条待应用的架构改进方案：\n"]
        for i, imp in enumerate(filtered, 1):
            lines.append(f"[{i}] {imp.get('external_pattern', '?')}")
            lines.append(f"    影响: {imp.get('apply_to', '?')}")
            lines.append(f"    风险: {imp.get('risk_level', 'unknown')}")
            lines.append(f"    改动: {imp.get('proposed_change', '')[:80]}")
            lines.append(f"    输入 'approve {i}' 批准，或 'approve all' 批准全部\n")

        lines.append("输入 'reject all' 拒绝全部")
        return "\n".join(lines)

    def approve_from_message(self, message: str) -> ImprovementResult:
        """Process an approval/rejection message from the user.

        Supported formats:
        - "approve 1" / "approve 1,2,3" → approve specific IDs
        - "approve all" → approve all pending
        - "reject all" → reject all pending
        """
        msg = message.strip().lower()
        pending = self._load_pending_improvements()

        if msg == "reject all":
            for p in pending:
                self._set_status(p["id"], "rejected")
            return ImprovementResult(rejected_count=len(pending))

        if msg == "approve all":
            result = self._apply_directly(pending)
            for p in pending:
                self._set_status(p["id"], "applied")
            result.applied_count = len(pending)
            return result

        if msg.startswith("approve"):
            parts = msg.replace("approve", "").strip().split(",")
            ids = []
            for p in parts:
                p = p.strip()
                if p.isdigit():
                    idx = int(p) - 1
                    if 0 <= idx < len(pending):
                        ids.append(pending[idx]["id"])
            selected = [p for p in pending if p["id"] in ids]
            result = self._apply_directly(selected)
            for p in selected:
                self._set_status(p["id"], "applied")
            rejected = [p for p in pending if p["id"] not in ids]
            for p in rejected:
                self._set_status(p["id"], "rejected")
            result.applied_count = len(selected)
            result.rejected_count = len(rejected)
            return result

        return ImprovementResult(errors=[f"无法解析审批消息: {message}"])

    # ── Internal methods ──

    def _risk_level_ok(self, improvement: dict, max_level: str) -> bool:
        return _RISK_ORDER.get(improvement.get("risk_level", "high"), 99) <= _RISK_ORDER.get(max_level, 0)

    def _load_pending_improvements(self) -> list[dict]:
        """Load improvements with status='pending' from evolution_rules."""
        import sqlite3

        try:
            db = sqlite3.connect(self._db_path)
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT id, rule_text, condition, category, confidence FROM evolution_rules "
                "WHERE category IN ('architecture_insight', 'architecture_improvement')"
            ).fetchall()
            db.close()

            result = []
            for r in rows:
                # Try condition column first (full JSON stored by save_improvements)
                condition = r["condition"] or ""
                if condition.startswith("{"):
                    try:
                        improvement = json.loads(condition)
                        improvement["id"] = improvement.get("id", f"rule_{r['id']}")
                        improvement["db_id"] = r["id"]
                        improvement["risk_level"] = improvement.get("risk_level", "low")
                        result.append(improvement)
                        continue
                    except Exception:
                        pass

                # Fallback: parse rule_text
                rule_text = r["rule_text"] or ""
                improvement = {}
                if rule_text.startswith("{"):
                    try:
                        improvement = json.loads(rule_text)
                    except Exception:
                        pass
                else:
                    improvement = {
                        "external_pattern": rule_text.split(":")[0] if ":" in rule_text else rule_text[:40],
                        "proposed_change": rule_text.split(":")[1] if ":" in rule_text else "",
                    }
                improvement["id"] = improvement.get("id", f"rule_{r['id']}")
                improvement["db_id"] = r["id"]
                improvement["risk_level"] = improvement.get("risk_level", "low")
                result.append(improvement)
            return result
        except Exception as e:
            logger.warning("[ARCH_IMPROVER] failed to load pending: %s", e)
            return []

    def _apply_directly(self, improvements: list[dict]) -> ImprovementResult:
        """Apply improvements directly without approval."""
        result = ImprovementResult()
        for imp in improvements:
            try:
                level = imp.get("implementation_level", "config")
                risk = imp.get("risk_level", "low")
                if level == "config":
                    self._apply_config(imp)
                    result.config_changes.append(imp)
                    result.applied_count += 1
                elif level == "prompt":
                    self._apply_prompt(imp)
                    result.prompt_changes.append(imp)
                    result.applied_count += 1
                else:
                    self._save_code_pending(imp)
                    result.code_pending.append(imp)
                self._log_improvement(imp)
            except Exception as e:
                result.errors.append(f"{imp.get('id','?')}: {e}")
                logger.error("[ARCH_IMPROVER] apply failed: %s", e)
        return result

    def _apply_with_approval(self, improvements: list[dict]) -> ImprovementResult:
        """Save improvements as needing approval (B 方案). Return summary for user."""
        import sqlite3

        db = sqlite3.connect(self._db_path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS pending_improvements ("
            "id TEXT PRIMARY KEY, improvement TEXT, status TEXT DEFAULT 'pending', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        saved = 0
        for imp in improvements:
            imp_id = imp.get("id", f"imp_{hash(str(imp)) % 10000:04d}")
            existing = db.execute(
                "SELECT status FROM pending_improvements WHERE id=?", (imp_id,)
            ).fetchone()
            if not existing or existing[0] != "pending":
                db.execute(
                    "INSERT OR REPLACE INTO pending_improvements (id, improvement, status) VALUES (?, ?, 'pending')",
                    (imp_id, json.dumps(imp, ensure_ascii=False)),
                )
                saved += 1
        db.commit()
        db.close()
        logger.info("[ARCH_IMPROVER] saved %d improvements for approval", saved)
        return ImprovementResult(applied_count=saved)

    def _apply_config(self, imp: dict) -> None:
        """Apply a config-level improvement by modifying YAML files."""
        apply_to = imp.get("apply_to", "")
        proposed = imp.get("proposed_change", "")

        # Find and backup config file
        cfg_path = self._find_config("harness_goals.yaml")
        if not cfg_path:
            logger.warning("[ARCH_IMPROVER] harness_goals.yaml not found, skipping")
            return

        # Create backup
        backup_path = cfg_path + ".backup"
        if not os.path.exists(backup_path):
            shutil.copy2(cfg_path, backup_path)
            logger.info("[ARCH_IMPROVER] backed up %s -> %s", cfg_path, backup_path)

        # Apply based on apply_to
        import yaml
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}

        # Normalize: some configs have a top-level "harness_goals" wrapper
        if "harness_goals" in cfg:
            cfg = cfg["harness_goals"]

        changed = False
        if apply_to == "event_parallelism":
            if cfg.get("parallel_execution") is not True:
                cfg["parallel_execution"] = True
                cfg["max_parallel_steps"] = 4
                changed = True
        elif apply_to == "micro_planner":
            if cfg.get("replan_interval") != 2:
                cfg["replan_interval"] = 2
                cfg["max_plan_steps"] = 8
                changed = True
        elif apply_to == "step_dependency":
            if cfg.get("dependency_check") != "strict":
                cfg["dependency_check"] = "strict"
                changed = True

        if changed:
            # Use the in-memory modified cfg (don't re-read the file)
            full_cfg = {}
            if os.path.exists(cfg_path):
                import yaml as _yaml
                with open(cfg_path) as f:
                    full_cfg = _yaml.safe_load(f) or {}
            if "harness_goals" in full_cfg:
                full_cfg["harness_goals"] = cfg
            else:
                full_cfg = cfg

            with open(cfg_path, "w") as f:
                yaml.dump(full_cfg, f, default_flow_style=False, allow_unicode=True)
            logger.info("[ARCH_IMPROVER] applied config change: %s -> %s", apply_to, proposed[:60])

    def _apply_prompt(self, imp: dict) -> None:
        """Apply a prompt-level improvement by writing to evolution_rules."""
        from .architecture_mapper import save_improvements
        saved = save_improvements([imp])
        if saved > 0:
            logger.info("[ARCH_IMPROVER] prompt improvement saved to evolution_rules")

    def _save_code_pending(self, imp: dict) -> None:
        """Save code-level improvement as pending (needs user approval)."""
        import sqlite3
        db = sqlite3.connect(self._db_path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS pending_improvements ("
            "id TEXT PRIMARY KEY, improvement TEXT, status TEXT DEFAULT 'pending', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT OR REPLACE INTO pending_improvements (id, improvement, status) VALUES (?, ?, 'pending')",
            (imp.get("id", "unknown"), json.dumps(imp, ensure_ascii=False)),
        )
        db.commit()
        db.close()

    def _set_status(self, imp_id: str, status: str) -> None:
        """Update the status of an improvement."""
        import sqlite3
        try:
            db = sqlite3.connect(self._db_path)
            db.execute(
                "UPDATE pending_improvements SET status=? WHERE id=?",
                (status, imp_id),
            )
            db.commit()
            db.close()
        except Exception:
            pass

    def _find_config(self, filename: str) -> str | None:
        """Find a config file in standard paths."""
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(_base, "config", filename),
            os.path.join(os.path.dirname(_base), "config", filename),
            os.path.expanduser(f"~/.partner/config/{filename}"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    def _log_improvement(self, imp: dict) -> None:
        """Record improvement to growth table."""
        try:
            from ..meta.learning import record_growth
            milestone = (
                f"架构改进 [{imp.get('implementation_level','?')}]: "
                f"{imp.get('external_pattern','?')[:40]}"
            )
            record_growth(
                user_id="default",
                milestone=milestone,
                reflection=json.dumps(imp, ensure_ascii=False),
                category="architecture_improvement",
            )
        except Exception:
            pass
