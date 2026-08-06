from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ..harness_core import RobustExecutor, TaskInstance, load_harness_config
from ..meta.learning import format_habits_for_prompt as _fmt_habits
from ..mind.harness import (
    HarnessStep,
    MicroPlan,
    _json_from_llm,
    _normalize_micro_plan,
)
from ..world_model import WorldModelClient, load_world_model_config

logger = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_yaml_config(workspace: str, filename: str, defaults: dict[str, Any]) -> dict[str, Any]:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    candidates = [
        os.path.join(workspace, "config", filename),
        os.path.join(workspace, filename),
        os.path.join(repo_root, "config", filename),
        os.path.join(repo_root, "configs", filename),
    ]
    config = dict(defaults)
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                config = _deep_merge(config, loaded)
                config["_config_path"] = path
                break
        except Exception as exc:
            logger.debug("[BATCH_PLANNER] failed to load config %s: %s", path, exc)
    return config


def _read_prompt_template(workspace: str, rel_path: str) -> str:
    candidates = [
        os.path.join(workspace, rel_path),
        os.path.join(os.path.dirname(__file__), "..", rel_path),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as exc:
                logger.debug("[BATCH_PLANNER] failed to read prompt %s: %s", path, exc)
    return ""


def _safe_filename(text: str, suffix: str = ".md") -> str:
    raw = re.sub(r"\s+", "_", str(text or "").strip())[:80]
    raw = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", raw).strip("_.-")
    if not raw:
        raw = "batch_plan_result"
    if not raw.lower().endswith(suffix.lower()):
        raw += suffix
    return raw


def _is_unavailable_sentinel(text: str) -> bool:
    raw = str(text or "").strip()
    return any(token in raw for token in (
        "PARTNER_AGENT_STILL_RUNNING_OR_UNAVAILABLE",
        "Error: agent backend not available",
    ))


@dataclass
class BatchPlanner:
    workspace: str
    config: dict[str, Any] = field(default_factory=dict)
    world_model_client: Any = None  # Optional[WorldModelClient] instance
    _last_wm_warning_ts: float = 0.0  # Rate-limit WorldModel warnings to 1 per 5 min

    @classmethod
    def from_workspace(cls, workspace: str) -> "BatchPlanner":
        defaults = {
            "llm_model": None,  # Must be set in config or raise error
            "min_steps": 2,
            "max_steps": 8,
            "max_active_skills": 10,
            "prompt_template": "prompts/batch_planner.txt",
            "unavailable_retries": 1,
            "unavailable_retry_delay_sec": 1,
        }
        config = _load_yaml_config(workspace, "batch_planner.yaml", defaults)

        # Load world model config and create client if enabled
        world_model_client = None
        try:
            wm_cfg = load_world_model_config(workspace)
            if wm_cfg.get("enabled", False):
                world_model_client = WorldModelClient(wm_cfg)
                logger.info(
                    "[BATCH_PLANNER] WorldModelClient enabled, endpoint=%s",
                    wm_cfg.get("endpoint", "N/A"),
                )
            else:
                logger.info("[BATCH_PLANNER] WorldModelClient disabled in config")
        except Exception as exc:
            import time as _wm_time
            if _wm_time.time() - BatchPlanner._last_wm_warning_ts > 300:
                logger.warning("[BATCH_PLANNER] WorldModel 不可用 (AETHER GPU 未连接): %s", exc)
                BatchPlanner._last_wm_warning_ts = _wm_time.time()

        return cls(
            workspace=workspace,
            config=config,
            world_model_client=world_model_client,
        )

    async def plan(
        self,
        *,
        adapter: Any,
        user_message: str,
        task_instance: TaskInstance,
        registry: Any,  # EventRegistry
        state_md: str = "",
        history: list[dict[str, Any]] | None = None,
        relevant_experiences: str = "",
        growth_context: str = "",
        event_type: str = "",
    ) -> tuple[MicroPlan, int]:
        if not adapter:
            raise RuntimeError("BatchPlanner requires an LLM adapter")

        llm_model = self.config.get("llm_model")
        if not llm_model:
            # Try to get model from adapter
            llm_model = getattr(adapter, "default_model", None) or getattr(adapter, "model", None)
        if not llm_model:
            llm_model = "deepseek-v4-flash"  # final fallback

        max_steps = max(1, int(self.config.get("max_steps") or 8))
        min_steps = max(1, int(self.config.get("min_steps") or 2))

        # Build prompt using dynamic builder
        try:
            from .prompt_builder import build_prompt
            prompt = build_prompt(
                user_message=str(user_message),
                available_events=registry.describe_for_prompt(),
                habits=_fmt_habits(),
                experiences=relevant_experiences or "",
                growth=growth_context or "",
                working_dir=task_instance.working_dir,
                expected_artifacts=task_instance.expected_artifacts,
                min_steps=min_steps,
                max_steps=max_steps,
                event_type=event_type,
            )
        except Exception as e:
            logger.error("[BATCH_PLANNER] failed to build prompt: %s", e)
            raise RuntimeError("Failed to build planner prompt") from e

        robust = RobustExecutor(load_harness_config(self.workspace))
        unavailable_retries = max(0, int(self.config.get("unavailable_retries") or 0))
        retry_delay = max(0.0, float(self.config.get("unavailable_retry_delay_sec") or 0))
        planner_calls = 0
        raw = ""

        for attempt in range(unavailable_retries + 1):
            result = await robust.execute(
                event_name="batch_planner",
                task_instance=task_instance,
                operation=lambda: adapter.chat(prompt, purpose="batch_plan"),
                on_timeout="fail_fast",
                on_failure="fail_fast",
                metadata={"model": llm_model, "max_steps": max_steps, "attempt": attempt + 1},
            )
            planner_calls += 1
            if not result.ok:
                raise RuntimeError(f"Batch planner LLM call failed: {result.error}")
            raw = str(result.value or "")
            if not _is_unavailable_sentinel(raw):
                break
            logger.warning("[BATCH_PLANNER] unavailable sentinel, raw output (first 500): %s", raw[:500])
            if attempt < unavailable_retries:
                logger.warning("[BATCH_PLANNER] unavailable sentinel, retrying...")
                if retry_delay:
                    await asyncio.sleep(retry_delay)
                continue
            raise RuntimeError("Batch planner LLM returned unavailable sentinel")

        # Parse JSON
        try:
            micro_plan = _normalize_micro_plan(_json_from_llm(raw), max_steps=max_steps)
        except Exception as exc:
            # Extract error position from JSONDecodeError if applicable
            error_pos = getattr(exc, 'pos', 'unknown')
            error_type = type(exc).__name__
            raw_preview = raw[:800]
            logger.error("[BATCH_PLANNER] failed to parse JSON: %s (type=%s, pos=%s)\nRaw: %s", exc, error_type, error_pos, raw_preview)
            task_instance.append_log('batch_planner_json_error', {
                'raw_preview': raw_preview,
                'error': str(exc),
                'error_type': error_type,
                'error_pos': error_pos,
            })
            # Retry: always retry at least once on JSON parse failure
            micro_plan = None
            _retry_count = 0
            _max_retries = max(1, int(self.config.get("max_json_retries") or 2))
            while micro_plan is None and _retry_count < _max_retries:
                _retry_count += 1
                logger.info("[BATCH_PLANNER] retry %d/%d with stricter JSON instruction", _retry_count, _max_retries)
                # Use a very explicit instruction: ONLY output JSON, no explanation
                if _retry_count == 1:
                    retry_prompt = prompt + (
                        "\n\n⚠️ 你上一轮的输出没有包含有效的 JSON 计划数组。\n"
                        "请只输出一个 JSON 对象，不要任何解释、分析或对话。\n"
                        "你必须输出一个 JSON 数组（plan 字段）或直接输出 JSON 数组。\n"
                        "输出格式严格为：{\"plan\": [{\"id\": \"step1\", \"event_type\": \"...\", \"parameters\": {...}}, ...]}\n"
                        "不要包含```markdown包裹，不要任何其他文字。"
                    )
                else:
                    # Fallback: ultra-short prompt for stubborn cases
                    retry_prompt = (
                        "你是一个任务规划器。用户要求："
                        + user_message[:200]
                        + "\n\n可用操作（只能使用以下 event_type）：\n"
                        + "\n".join(e.split("(")[0].strip() for e in registry.describe_for_prompt().split("\n") if e.strip())[:1000]
                        + "\n\n只输出 JSON 数组，不要任何解释：\n"
                        + '[{\"id\": \"step1\", \"event_type\": \"...\", \"parameters\": {}, \"depends_on\": []}]'
                    )
                result = await robust.execute(
                    event_name="batch_planner",
                    task_instance=task_instance,
                    operation=lambda: adapter.chat(retry_prompt, purpose="batch_plan"),
                    on_timeout="fail_fast",
                    on_failure="fail_fast",
                    metadata={"model": llm_model, "max_steps": max_steps, "attempt": attempt + _retry_count},
                )
                planner_calls += 1
                if result.ok:
                    raw2 = str(result.value or "")
                    if not _is_unavailable_sentinel(raw2):
                        try:
                            micro_plan = _normalize_micro_plan(_json_from_llm(raw2), max_steps=max_steps)
                            logger.info("[BATCH_PLANNER] retry %d succeeded", _retry_count)
                            break
                        except Exception as retry_exc:
                            logger.warning("[BATCH_PLANNER] retry %d also failed: %s", _retry_count, retry_exc)
                            task_instance.append_log('batch_planner_retry_error', {
                                'raw_preview': str(raw2)[:500],
                                'error': str(retry_exc),
                                'attempt': _retry_count,
                            })
            if micro_plan is None:
                raise RuntimeError(f"Batch planner returned invalid JSON [type={type(exc).__name__}, pos={getattr(exc, 'pos', 'unknown')}]: {exc}") from exc

        # Sanitize: remove curiosity_explore steps
        filtered = [step for step in micro_plan.plan if step.event_type != "curiosity_explore"]
        if len(filtered) != len(micro_plan.plan):
            micro_plan = MicroPlan(plan=filtered, expected_artifacts=micro_plan.expected_artifacts)
            filtered = micro_plan.plan

        # Sanitize: cytobridge already produces its own analysis_report_zh.md + figures.
        # Strip any smart_llm_structured_action and atomic_write_artifact that follow
        # a call_agent_skill(cytobridge) step — they generate a redundant LLM report.
        # Replace with a direct atomic_convert_md_to_pdf step.
        _has_cytobridge = any(
            s.event_type == "call_agent_skill"
            and s.parameters.get("agent", "").lower() in ("cytobridge", "cytobridge-agent")
            for s in filtered
        )
        if _has_cytobridge:
            # Find the cytobridge step index
            _cb_idx = next(
                i for i, s in enumerate(filtered)
                if s.event_type == "call_agent_skill"
                and s.parameters.get("agent", "").lower() in ("cytobridge", "cytobridge-agent")
            )
            _cb_id = filtered[_cb_idx].id
            # ── Ensure cytobridge has ALL required parameters ──
            _cb_params = dict(filtered[_cb_idx].parameters)
            _inner_params = dict(_cb_params.get("parameters", {}) or {})
            _out_dir = task_instance.working_dir if task_instance else ""
            # 1) Inject output from working_dir if missing
            if _out_dir and "output" not in _inner_params:
                _inner_params["output"] = _out_dir
            # 2) Inject question from user_message / task text if missing
            if "question" not in _inner_params:
                _task_text = str(_cb_params.get("task", "") or _inner_params.get("task", "") or user_message or "").strip()
                if _task_text:
                    _inner_params["question"] = _task_text
            # 3) Ensure input is present (the planner should already set this)
            if "input" not in _inner_params:
                # Try to extract from task text
                import re as _re
                _path_match = _re.search(r"(?:/data/|/mnt/[a-z]/)[\w/. -]+\.\w+", str(_cb_params.get("task", "")))
                if _path_match:
                    _inner_params["input"] = _path_match.group(0)
            _cb_params["parameters"] = _inner_params
            filtered[_cb_idx] = HarnessStep(
                id=_cb_id,
                event_type="call_agent_skill",
                parameters=_cb_params,
                depends_on=filtered[_cb_idx].depends_on,
            )
            # Remove redundant LLM report and .md write steps
            _removed_ids = set()
            _keep = []
            for s in filtered:
                if s.event_type in ("smart_llm_structured_action", "atomic_write_artifact"):
                    # Keep if it doesn't depend on cytobridge (e.g. unrelated analysis)
                    if _cb_id in s.depends_on:
                        logger.info("[BATCH_PLANNER] stripped redundant %s step (%s) after cytobridge", s.event_type, s.id)
                        _removed_ids.add(s.id)
                        continue
                _keep.append(s)
            filtered = _keep
            # Ensure atomic_convert_md_to_pdf exists (inject or update after cytobridge step)
            _pdf_source = os.path.join(_out_dir, "analysis_report_zh.md") if _out_dir else ""
            _existing_pdf_idx = next(
                (i for i, s in enumerate(filtered) if s.event_type == "atomic_convert_md_to_pdf"),
                None,
            )
            if _existing_pdf_idx is not None:
                # Update existing step with source parameter and fix dependencies
                _old = filtered[_existing_pdf_idx]
                _old_params = dict(_old.parameters)
                if _pdf_source and not _old_params.get("source"):
                    _old_params["source"] = _pdf_source
                # Fix depends_on: replace removed steps with the cytobridge step
                _old_deps = list(_old.depends_on) if _old.depends_on else []
                _new_deps = [_cb_id if d in _removed_ids else d for d in _old_deps]
                if not _new_deps:
                    _new_deps = [_cb_id]
                filtered[_existing_pdf_idx] = HarnessStep(
                    id=_old.id,
                    event_type="atomic_convert_md_to_pdf",
                    parameters=_old_params,
                    depends_on=_old.depends_on,
                )
                logger.info("[BATCH_PLANNER] updated existing atomic_convert_md_to_pdf with source=%s", _pdf_source)
            else:
                _pdf_step = HarnessStep(
                    id="step_convert_to_pdf",
                    event_type="atomic_convert_md_to_pdf",
                    parameters={"source": _pdf_source} if _pdf_source else {},
                    depends_on=[_cb_id],
                )
                # Insert right after cytobridge step
                filtered = filtered[:_cb_idx + 1] + [_pdf_step] + filtered[_cb_idx + 1:]
                logger.info("[BATCH_PLANNER] injected atomic_convert_md_to_pdf step after cytobridge")
            micro_plan = MicroPlan(plan=filtered, expected_artifacts=micro_plan.expected_artifacts)
            filtered = micro_plan.plan

        # ── Habit auto-application: apply user preferences from habits ──
        try:
            _habit_config = _load_yaml_config(self.workspace, "evolution_internal.yaml", {})
            _internal_cfg = _habit_config.get("internal_evolution", {})
            if _internal_cfg.get("habit_auto_apply", True):
                from ..meta.learning import load_habits
                _habits = load_habits()
                
                # Apply PDF preference
                if _habits.get("prefer_pdf", False):
                    # Check if final step already outputs PDF
                    _has_pdf = any(
                        s.event_type == "atomic_convert_md_to_pdf" for s in micro_plan.plan
                    ) if micro_plan.plan else False
                    _has_md = any(
                        s.event_type == "atomic_write_artifact" 
                        and "md" in str(s.parameters.get("filename", "")).lower()
                        for s in micro_plan.plan
                    ) if micro_plan.plan else False
                    if not _has_pdf and _has_md:
                        # Add PDF conversion after the last MD write step
                        _last_md_idx = -1
                        for _i, _s in enumerate(micro_plan.plan):
                            if _s.event_type == "atomic_write_artifact" and "md" in str(_s.parameters.get("filename", "")).lower():
                                _last_md_idx = _i
                        if _last_md_idx >= 0:
                            _pdf_step = HarnessStep(
                                id=f"step{len(micro_plan.plan) + 1}",
                                event_type="atomic_convert_md_to_pdf",
                                parameters={},
                                depends_on=[micro_plan.plan[_last_md_idx].id],
                            )
                            micro_plan.plan.append(_pdf_step)
                            logger.info("[BATCH_PLANNER] habit auto-apply: added PDF conversion (user prefers PDF)")
                
                # Apply avoid_web_search preference
                if _habits.get("avoid_web_search", False):
                    _stripped = [s for s in micro_plan.plan if s.event_type not in ("web_search",)]
                    if len(_stripped) < len(micro_plan.plan):
                        logger.info("[BATCH_PLANNER] habit auto-apply: removed %d web_search steps (user prefers no web search)",
                                    len(micro_plan.plan) - len(_stripped))
                        micro_plan = type(micro_plan)(
                            plan=_stripped,
                            expected_artifacts=micro_plan.expected_artifacts,
                        )
                logger.info("[BATCH_PLANNER] habit auto-apply complete")
        except Exception as _h_exc:
            logger.debug("[BATCH_PLANNER] habit auto-apply failed (non-fatal): %s", _h_exc)

        # Check step count
        if len(micro_plan.plan) < min_steps:
            logger.warning("[BATCH_PLANNER] plan has only %d steps (configured min is %d), accepting anyway", len(micro_plan.plan), min_steps)

        # Normalize references
        micro_plan = self._normalize_plan_references(micro_plan, task_instance)

        # --- World Model simulation & optimization ---
        if self.world_model_client is not None and self.world_model_client.is_available():
            try:
                plan_dicts = [
                    {
                        "id": step.id,
                        "action": step.event_type,
                        "parameters": step.parameters,
                        "depends_on": step.depends_on,
                    }
                    for step in micro_plan.plan
                ]
                state = {
                    "workspace": self.workspace,
                    "user_message": str(user_message),
                    "expected_artifacts": micro_plan.expected_artifacts,
                }
                logger.info(
                    "[BATCH_PLANNER] running WorldModel simulation on %d-step plan",
                    len(plan_dicts),
                )
                sim_result = await self.world_model_client.simulate_plan(plan_dicts, state)
                task_instance.append_log("world_model_simulate", sim_result)

                # Store world model status for user-facing display
                wm_label = ""
                sim_ok = sim_result.get("status") in ("ok", "success", "simulated")
                if sim_ok:
                    backend = sim_result.get("_backend", sim_result.get("backend", "aether"))
                    frames = sim_result.get("frames_generated", 0)
                    elapsed = sim_result.get("elapsed_seconds", 0)
                    session_id = sim_result.get("session_id", "")
                    video_path = sim_result.get("video_path", "")
                    local_dir = sim_result.get("local_session_dir", "")

                    # Build display message
                    parts = [f"生成{frames}帧视频 ({elapsed:.1f}s)"]
                    if video_path:
                        parts.append(f"视频已保存")
                    if local_dir:
                        parts.append(f"完整记录: {local_dir}")
                    wm_label = f"[世界模型] AETHER GPU 模拟完成 ({backend}): {'; '.join(parts)}"

                    # Save video/metadata paths to task metadata
                    if isinstance(getattr(task_instance, "metadata", None), dict):
                        task_instance.metadata["world_model_status"] = wm_label
                        task_instance.metadata["world_model_video_path"] = video_path or ""
                        task_instance.metadata["world_model_session_dir"] = local_dir or ""
                        task_instance.metadata["world_model_session_id"] = session_id or ""
                else:
                    reason = sim_result.get("error", sim_result.get("reason", "unknown"))
                    wm_label = f"[世界模型] AETHER 不可用 ({reason})"
                    if isinstance(getattr(task_instance, "metadata", None), dict):
                        task_instance.metadata["world_model_status"] = wm_label

                if sim_ok and sim_result.get("optimized_plan"):
                    logger.info(
                        "[BATCH_PLANNER] WorldModel returned optimized plan with %d steps",
                        len(sim_result["optimized_plan"]),
                    )
                    micro_plan = self._apply_world_model_optimizations(
                        micro_plan, sim_result["optimized_plan"], task_instance
                    )
                elif sim_ok:
                    logger.info(
                        "[BATCH_PLANNER] WorldModel simulation OK (AETHER video, no text optimizations)"
                    )
                else:
                    logger.info(
                        "[BATCH_PLANNER] WorldModel simulation failed (suppressed if repeated): %s",
                        sim_result.get("error", "unknown"),
                    )
            except Exception as exc:
                logger.warning(
                    "[BATCH_PLANNER] WorldModel simulation error (skipped): %s", exc
                )
                task_instance.append_log("world_model_simulate_error", {"error": str(exc)})
        else:
            logger.debug(
                "[BATCH_PLANNER] WorldModelClient not available, skipping simulation"
            )
        # --- End World Model ---

        task_instance.append_log("batch_plan_created", {
            "steps": [step.__dict__ for step in micro_plan.plan],
            "expected_artifacts": micro_plan.expected_artifacts,
        })
        logger.info("[BATCH_PLANNER] Generated plan with %d steps", len(micro_plan.plan))
        return micro_plan, planner_calls

    def _normalize_plan_references(self, micro_plan: MicroPlan, task_instance: TaskInstance) -> MicroPlan:
        changed = False

        def normalize_value(value: Any) -> Any:
            nonlocal changed
            if isinstance(value, str):
                raw = value.strip()
                match = re.fullmatch(r"\$\{([A-Za-z0-9_.-]+)_output\}", raw)
                if match:
                    changed = True
                    return f"${match.group(1)}.content"
                match = re.fullmatch(r"([A-Za-z0-9_.-]+)_output_json_path", raw)
                if match:
                    changed = True
                    return f"${match.group(1)}.json"
                fixed = re.sub(r"\$simple_llm_structured_action_([0-9]+)\.", r"$step_\1.", value)
                if fixed != value:
                    changed = True
                    return fixed
                return value
            if isinstance(value, list):
                return [normalize_value(item) for item in value]
            if isinstance(value, dict):
                return {key: normalize_value(item) for key, item in value.items()}
            return value

        normalized_steps = []
        for step in micro_plan.plan:
            params = normalize_value(step.parameters)
            normalized_steps.append(HarnessStep(
                id=step.id,
                event_type=step.event_type,
                parameters=params if isinstance(params, dict) else {},
                depends_on=step.depends_on,
            ))
        if changed:
            task_instance.append_log("batch_plan_sanitized", {"reason": "normalized_references"})
            return MicroPlan(plan=normalized_steps, expected_artifacts=micro_plan.expected_artifacts)
        return micro_plan

    def _apply_world_model_optimizations(
        self,
        micro_plan: MicroPlan,
        optimized_plan: list[dict],
        task_instance: TaskInstance,
    ) -> MicroPlan:
        """Apply optimized steps from WorldModel simulation result.

        Replaces the current plan with the optimized plan while preserving
        expected_artifacts from the original.
        """
        if not optimized_plan:
            return micro_plan

        new_steps = []
        for raw_step in optimized_plan:
            step_id = str(raw_step.get("id", ""))
            event_type = str(raw_step.get("action", raw_step.get("event_type", "")))
            parameters = raw_step.get("parameters", {})
            depends_on = raw_step.get("depends_on", [])

            if not step_id or not event_type:
                logger.warning(
                    "[BATCH_PLANNER] skipping invalid optimized step: %s", raw_step
                )
                continue

            new_steps.append(HarnessStep(
                id=step_id,
                event_type=event_type,
                parameters=parameters if isinstance(parameters, dict) else {},
                depends_on=depends_on if isinstance(depends_on, list) else [],
            ))

        if not new_steps:
            logger.warning(
                "[BATCH_PLANNER] no valid steps in optimized plan, keeping original"
            )
            return micro_plan

        task_instance.append_log("world_model_optimized", {
            "original_step_count": len(micro_plan.plan),
            "optimized_step_count": len(new_steps),
        })
        logger.info(
            "[BATCH_PLANNER] world model optimized plan: %d -> %d steps",
            len(micro_plan.plan),
            len(new_steps),
        )
        return MicroPlan(plan=new_steps, expected_artifacts=micro_plan.expected_artifacts)

    def _apply_world_model_suggestions(
        self,
        micro_plan: MicroPlan,
        sim_result: dict,
        task_instance: TaskInstance,
    ) -> bool:
        """Apply suggestions and per-step risk from world model simulation.

        Processes:
          - add_step: Insert new events into the plan
          - modify_parameter: Change step parameters (timeout, retries)
          - reorder: Reorder steps based on risk
          - per_step_risk: Adjust high-risk step parameters
          - parallel_recommendation: Set parallelism hints

        Returns True if the plan was modified.
        """
        modified = False
        suggestions = sim_result.get("suggestions", []) or []
        per_step_risk = sim_result.get("per_step_risk", []) or []
        parallel_rec = sim_result.get("parallel_recommendation", "")

        new_steps = list(micro_plan.plan)

        # 1. Process suggestions
        for suggestion in suggestions:
            s_type = suggestion.get("type", "")
            s_event = suggestion.get("event", "")
            s_target = suggestion.get("target", "")
            s_param = suggestion.get("param", "")
            s_value = suggestion.get("value")
            s_strategy = suggestion.get("strategy", "")

            if s_type == "add_step" and s_event:
                import uuid
                step_id = f"wm_{uuid.uuid4().hex[:8]}"
                depends_on = []
                if s_target == "after_search":
                    depends_on = [step.id for step in new_steps if "search" in step.event_type or "http" in step.event_type]
                elif s_target == "before_report":
                    depends_on = []
                    # Find the last report-related step
                    for i, step in enumerate(new_steps):
                        if "write" in step.event_type or "report" in step.event_type or "convert" in step.event_type:
                            depends_on = [step.id]
                            break
                insert_idx = len(new_steps)
                if s_target == "before_report":
                    for i, step in enumerate(new_steps):
                        if "write" in step.event_type or "convert" in step.event_type:
                            insert_idx = i
                            break
                new_step = HarnessStep(
                    id=step_id,
                    event_type=s_event,
                    parameters={},
                    depends_on=depends_on,
                )
                new_steps.insert(insert_idx, new_step)
                modified = True
                logger.info("[BATCH_PLANNER] WorldModel suggestion: added step %s (%s)", s_event, suggestion.get("reason", ""))

            elif s_type == "modify_parameter" and s_param:
                targeted = [step for step in new_steps if s_target in step.event_type or s_target in str(step.parameters)]
                for step in targeted:
                    params = dict(step.parameters)
                    params[s_param] = s_value
                    new_params = {k: v for k, v in params.items()}
                    modified_steps = []
                    for i, s in enumerate(new_steps):
                        if s.id == step.id:
                            new_steps[i] = HarnessStep(
                                id=s.id,
                                event_type=s.event_type,
                                parameters=new_params,
                                depends_on=s.depends_on,
                            )
                            modified_steps.append(s.id)
                    if modified_steps:
                        modified = True
                        logger.info("[BATCH_PLANNER] WorldModel suggestion: set %s=%s on %s steps (%s)",
                                    s_param, s_value, len(modified_steps), suggestion.get("reason", ""))

            elif s_type == "reorder" and s_strategy == "parallel_safe_first":
                # Sort steps by risk: low-risk first, high-risk last
                risk_map = {}
                if per_step_risk:
                    for item in per_step_risk:
                        action = item.get("action", "")
                        risk = item.get("risk", 0.5)
                        risk_map[action] = risk
                def sort_key(step):
                    base = risk_map.get(step.event_type, 0.5)
                    return base
                new_steps.sort(key=sort_key)
                modified = True
                logger.info("[BATCH_PLANNER] WorldModel reorder: sorted %d steps by risk (%s)",
                            len(new_steps), suggestion.get("reason", ""))

        # 2. Apply per_step_risk adjustments
        if per_step_risk:
            risk_map = {item.get("action", ""): item.get("risk", 0.3) for item in per_step_risk}
            for i, step in enumerate(new_steps):
                step_risk = risk_map.get(step.event_type, 0.3)
                if step_risk > 0.5:
                    params = dict(step.parameters)
                    if "timeout" not in params:
                        params["timeout"] = max(30, int(step_risk * 60))
                    if "max_retries" not in params:
                        params["max_retries"] = 2
                    new_steps[i] = HarnessStep(
                        id=step.id,
                        event_type=step.event_type,
                        parameters=params,
                        depends_on=step.depends_on,
                    )
                    modified = True
                    logger.info("[BATCH_PLANNER] WorldModel risk adjustment: step %s risk=%.2f, added timeout/retry",
                                step.id, step_risk)

        # 3. Store parallelism recommendation
        if parallel_rec and isinstance(getattr(task_instance, "metadata", None), dict):
            task_instance.metadata["world_model_parallel"] = parallel_rec
            task_instance.metadata["world_model_optimized"] = modified
            task_instance.save()

        if modified:
            task_instance.append_log("world_model_suggestions_applied", {
                "suggestion_count": len(suggestions),
                "new_step_count": len(new_steps) - len(micro_plan.plan),
                "per_step_risk_applied": bool(per_step_risk),
            })

        return modified
