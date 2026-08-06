"""沙箱验证器 — 代码修改方案预验证。

**已升级为使用 SandboxService（partner/sandbox/service.py）作为后端。**

自进化流程中的"执行前预验证"：
每个代码修改方案在正式应用之前，先在隔离环境中验证/执行一次，
根据结果决定是否真正应用。

向后兼容：SandboxValidator 现在包装了 SandboxService。
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """单个方案的验证结果"""
    plan_id: str
    success: bool
    returncode: int
    stdout: str
    stderr: str
    duration: float
    error_type: Optional[str] = None
    error_detail: Optional[str] = None
    validation_steps: List[Dict] = field(default_factory=list)


@dataclass
class ValidationBatchResult:
    """一批方案的验证汇总"""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total_duration: float = 0.0
    results: List[ValidationResult] = field(default_factory=list)
    failure_patterns: Dict[str, int] = field(default_factory=dict)


class SandboxValidator:
    """沙箱验证器 — 包装 SandboxService。

    保持与自进化引擎兼容的接口（validate / validate_batch）。
    内部使用 SandboxService 提供的三层隔离执行。
    """

    def __init__(self, workspace: str, timeout: int = 30, project_root: str = ""):
        self.workspace = workspace
        self.timeout = timeout
        self.project_root = project_root or workspace
        self._failure_db_path = os.path.join(workspace, "state", "validation_failures.jsonl")

        # 创建 SandboxService 实例
        from partner.sandbox.service import SandboxService as _SS
        self._sandbox = _SS(workspace=workspace)

    async def validate(self, plan: Dict[str, Any]) -> ValidationResult:
        """验证单个方案。"""
        plan_id = plan.get("id", plan.get("plan_id", "unknown"))
        target_file = plan.get("target_file", plan.get("target_module", ""))
        new_code = plan.get("new_code", "")
        change_type = plan.get("change_type", "modify_function")

        if not new_code or not new_code.strip():
            return ValidationResult(
                plan_id=plan_id, success=False, returncode=-1,
                stdout="", stderr="", duration=0,
                error_type="no_code",
                error_detail="方案没有包含新代码内容",
            )

        start = time.time()
        try:
            # 使用 SandboxService 执行验证
            result = await self._sandbox.validate_modification(
                new_code=new_code,
                target_file=target_file,
                plan_id=plan_id,
            )

            duration = time.time() - start

            # 映射结果
            if not result.syntax_ok:
                vr = ValidationResult(
                    plan_id=plan_id, success=False,
                    returncode=1, stdout="", stderr=result.syntax_error or "",
                    duration=duration, error_type="syntax_error",
                    error_detail=result.syntax_error,
                    validation_steps=[{"step": "syntax_check", "ok": False, "error": result.syntax_error}],
                )
            elif not result.compile_ok:
                vr = ValidationResult(
                    plan_id=plan_id, success=False,
                    returncode=1, stdout="", stderr=result.compile_error or "",
                    duration=duration, error_type="compile_error",
                    error_detail=result.compile_error,
                    validation_steps=[
                        {"step": "syntax_check", "ok": True},
                        {"step": "compile", "ok": False, "error": result.compile_error},
                    ],
                )
            else:
                steps = [{"step": "syntax_check", "ok": True}, {"step": "compile", "ok": True}]
                if result.import_error:
                    steps.append({"step": "import_test", "ok": False, "error": result.import_error})
                vr = ValidationResult(
                    plan_id=plan_id, success=result.success,
                    returncode=result.returncode,
                    stdout=result.stdout[:500],
                    stderr=result.stderr[:500],
                    duration=duration,
                    error_type="import_error" if result.import_error else None,
                    error_detail=result.import_error or "",
                    validation_steps=steps,
                )

            self._log_validation(vr, plan)
            return vr

        except Exception as e:
            duration = time.time() - start
            logger.error("[VALIDATE] Plan %s 验证异常: %s", plan_id, e)
            return ValidationResult(
                plan_id=plan_id, success=False, returncode=-1,
                stdout="", stderr=str(e), duration=duration,
                error_type="exception",
            )

    async def validate_batch(self, plans: List[Dict]) -> ValidationBatchResult:
        """批量验证。"""
        batch = ValidationBatchResult(total=len(plans))
        for plan in plans:
            result = await self.validate(plan)
            batch.results.append(result)
            batch.total_duration += result.duration
            if result.success:
                batch.passed += 1
            else:
                batch.failed += 1
                p = result.error_type or "unknown"
                batch.failure_patterns[p] = batch.failure_patterns.get(p, 0) + 1
        return batch

    def _log_validation(self, result: ValidationResult, plan: Dict):
        """记录验证日志。"""
        status = "✅" if result.success else "❌"
        logger.info(
            "[VALIDATE] %s Plan %s: success=%s, duration=%.1fs, type=%s",
            status, result.plan_id, result.success,
            result.duration, result.error_type or "N/A",
        )
        try:
            db_path = self._failure_db_path
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            entry = {
                "ts": time.time(),
                "plan_id": result.plan_id,
                "success": result.success,
                "duration": result.duration,
                "error_type": result.error_type,
                "error_detail": result.error_detail,
                "change_type": plan.get("change_type", "unknown"),
                "target": plan.get("target_file", ""),
            }
            with open(db_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("[VALIDATE] Failed to write validation log: %s", e)

    def get_failure_stats(self) -> Dict:
        """获取历史验证失败统计。"""
        db_path = self._failure_db_path
        if not os.path.isfile(db_path):
            return {"total": 0, "passed": 0, "failed": 0, "patterns": {}}
        stats = {"total": 0, "passed": 0, "failed": 0, "patterns": {}}
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    stats["total"] += 1
                    if entry.get("success"):
                        stats["passed"] += 1
                    else:
                        stats["failed"] += 1
                        et = entry.get("error_type", "unknown")
                        stats["patterns"][et] = stats["patterns"].get(et, 0) + 1
        except Exception:
            pass
        return stats
