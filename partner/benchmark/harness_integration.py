"""Partner Harness 集成 — Benchmark 执行引擎。

使用 Partner 核心组件（TaskInstance）执行 benchmark 任务，
通过 Hermes CLI 子进程进行内容生成。全线同步，避免 asyncio 兼容问题。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


def _import_harness():
    """延迟导入避免循环依赖。"""
    global MindEvent, EventType, TaskInstance, RobustExecutor, load_harness_config
    from ..mind.event_types import MindEvent, EventType
    from ..harness_core import TaskInstance, RobustExecutor, load_harness_config


# ── Workspace 初始化 ──────────────────────────────────────────────────

def _build_minimal_workspace(work_dir: str) -> str:
    """在 work_dir 中创建 minimal Partner workspace 配置。"""
    workspace = os.path.join(work_dir, "benchmark_workspace")
    os.makedirs(workspace, exist_ok=True)
    os.makedirs(os.path.join(workspace, "state"), exist_ok=True)

    backend = os.environ.get("PARTNER_BENCHMARK_BACKEND", "hermes")
    config = {
        "version": "1.0",
        "instance_id": "benchmark",
        "display_name": "Benchmark Runner",
        "agent": {
            "backend": backend,
            "model": os.environ.get("PARTNER_BENCHMARK_MODEL", ""),
            "provider": os.environ.get("PARTNER_BENCHMARK_PROVIDER", ""),
        },
    }
    config_json = json.dumps(config, ensure_ascii=False, indent=2)

    for sub in ("config", "00_config", "."):
        d = os.path.join(workspace, sub) if sub != "." else workspace
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "partner_config.json"), "w", encoding="utf-8") as f:
            f.write(config_json)

    return workspace


# ── Hermes 子进程调用（纯同步） ──────────────────────────────────────

_HERMES_BIN: str | None = None


def _find_hermes() -> str:
    global _HERMES_BIN
    if _HERMES_BIN:
        return _HERMES_BIN
    import shutil
    hermes = shutil.which("hermes")
    if hermes:
        _HERMES_BIN = hermes
        return hermes
    candidates = [
        os.path.expanduser("~/.local/bin/hermes"),
        os.path.expanduser("~/.hermes/hermes-agent/venv/bin/hermes"),
        "/usr/local/bin/hermes",
    ]
    for c in candidates:
        if os.path.exists(c):
            _HERMES_BIN = c
            return c
    raise RuntimeError("hermes CLI not found")


def _call_hermes_sync(prompt: str, *, timeout_sec: int = 120) -> str:
    """纯同步 Hermes chat 调用。"""
    hermes_bin = _find_hermes()
    cmd = [
        hermes_bin, "chat", "-q", prompt, "-Q",
        "--ignore-rules", "--max-turns", "5",
    ]
    model = os.environ.get("PARTNER_BENCHMARK_MODEL", "")
    provider = os.environ.get("PARTNER_BENCHMARK_PROVIDER", "")
    if model:
        cmd.extend(["-m", model])
    if provider:
        cmd.extend(["--provider", provider])

    logger.info("[BENCHMARK/HERMES] calling hermes chat (timeout=%ss, prompt_len=%d)",
                timeout_sec, len(prompt))
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
            text=True,
        )
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()[:500] if r.stderr else ""
        if stdout:
            logger.info("[BENCHMARK/HERMES] success (%d chars)", len(stdout))
            return stdout
        logger.warning("[BENCHMARK/HERMES] empty reply, stderr=%s", stderr)
        return "(hermes returned empty response)"
    except subprocess.TimeoutExpired:
        logger.warning("[BENCHMARK/HERMES] timeout after %ss", timeout_sec)
        return "(hermes chat timed out)"
    except Exception as exc:
        logger.warning("[BENCHMARK/HERMES] error: %s", exc)
        return f"(hermes chat error: {exc})"


# ── Benchmark 执行器（纯同步） ────────────────────────────────────────

class HarnessBenchmarkExecutor:
    """使用 Partner TaskInstance + Hermes 子进程执行 benchmark 任务。

    每个 benchmark 任务：
    1. 创建 TaskInstance（日志/状态/产物管理）
    2. 通过 hermes chat 生成内容（带超时）
    3. 写入产物文件
    4. 记录完整的执行轨迹
    """

    def __init__(self, workspace: str | None = None):
        self._workspace = workspace

    def execute(self, task: Any, work_dir: str) -> Any:
        """同步执行一个 benchmark 任务。"""
        from .naturebench_runner import BenchmarkResult

        _import_harness()

        # 设置共享 workspace
        if self._workspace:
            workspace = self._workspace
        else:
            parent = os.path.dirname(os.path.abspath(work_dir))
            workspace = _build_minimal_workspace(parent)

        # 任务目录
        task_dir = os.path.join(work_dir, task.task_id)
        os.makedirs(task_dir, exist_ok=True)

        user_message = task.input_data.get("query", task.description)
        output_format = task.expected_output.get("format", "markdown")

        # 1. 创建 TaskInstance
        task_instance = TaskInstance.create(
            workspace,
            user_message,
            task_id=task.task_id,
            metadata={
                "title": task.title,
                "benchmark_id": task.task_id,
                "source": "benchmark",
                "scoring_rules": task.scoring_rules,
            },
        )
        task_instance.append_log("benchmark_task_start", {
            "task_id": task.task_id,
            "title": task.title,
        })

        # 2. 构建 prompt
        key_points = task.expected_output.get("key_points", [])
        prompt_lines = [
            f"## 任务\n{task.title}",
            f"\n## 详细说明\n{task.description}",
            f"\n## 输入查询\n{user_message}",
            "\n## 必须覆盖的关键点",
        ]
        for kp in key_points:
            prompt_lines.append(f"- {kp}")
        prompt_lines.extend([
            f"\n请输出 {output_format} 格式的结构化内容。",
            "确保内容完整、有深度、学术性强。",
        ])
        prompt = "\n".join(prompt_lines)

        # 3. 调用 Hermes（纯同步 subprocess.run）
        start_ts = time.time()
        task_instance.append_log("benchmark_llm_call_start", {
            "prompt_length": len(prompt),
        })
        result_content = _call_hermes_sync(prompt, timeout_sec=int(os.environ.get("BENCHMARK_TIMEOUT", "300")))
        elapsed_ms = int((time.time() - start_ts) * 1000)
        task_instance.append_log("benchmark_llm_call_end", {
            "elapsed_ms": elapsed_ms,
            "content_length": len(result_content),
        })

        # 4. 写入产物文件
        result_path = os.path.join(task_dir, f"{task.task_id}_result.{output_format}")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(result_content)
        task_instance.append_log("benchmark_artifact_written", {
            "path": result_path,
            "size": os.path.getsize(result_path),
        })

        # 5. 更新 TaskInstance 状态
        timed_out = "timed out" in result_content
        errored = "error" in result_content[:50]
        has_content = len(result_content) > 100 and not timed_out and not errored
        status = "done" if has_content else "partial"
        task_instance.mark(status, {
            "elapsed_ms": elapsed_ms,
            "content_length": len(result_content),
        })

        # 6. task_state.json（供 export 读取）
        task_state = {
            "task_id": task.task_id,
            "title": task.title,
            "created_at": task_instance.created_at,
            "completion_status": "completed" if has_content else "partial",
            "working_dir": task_dir,
        }
        with open(os.path.join(task_dir, "task_instance.json"), "w", encoding="utf-8") as f:
            json.dump(task_state, f, ensure_ascii=False, indent=2)

        return BenchmarkResult(
            task_id=task.task_id,
            title=task.title,
            status="completed" if has_content else "partial",
            content=result_content,
            artifacts=[result_path],
            duration_ms=elapsed_ms,
            metadata={
                "task_dir": task_dir,
                "workspace": workspace,
                "task_instance_id": task_instance.task_id,
            },
        )

    def execute_with_harness(self, task: Any, work_dir: str) -> Any:
        """Execute benchmark task using real Partner Harness (BatchPlanner + run_harness_plan).
        
        This creates a real MicroPlan via BatchPlanner and executes it through
        the Harness engine, producing full execution traces.
        """
        import time as _time
        import asyncio
        from .naturebench_runner import BenchmarkResult
        
        _import_harness()
        
        # Setup workspace
        if self._workspace:
            workspace = self._workspace
        else:
            parent = os.path.dirname(os.path.abspath(work_dir))
            workspace = _build_minimal_workspace(parent)
        
        task_dir = os.path.join(work_dir, task.task_id)
        os.makedirs(task_dir, exist_ok=True)
        
        user_message = task.input_data.get("query", task.description)
        
        # 1. Create TaskInstance
        task_instance = TaskInstance.create(
            workspace,
            user_message,
            task_id=task.task_id,
            metadata={
                "title": task.title,
                "benchmark_id": task.task_id,
                "source": "benchmark",
            },
        )
        
        timeout_sec = int(os.environ.get("BENCHMARK_TIMEOUT", "300"))
        start_ts = _time.time()
        
        try:
            # 2. Build MicroPlan via BatchPlanner
            from ..planner import BatchPlanner
            from ..mind.harness import default_registry, run_harness_plan
            
            # Try to get an adapter for the Harness call
            adapter = None
            try:
                from ..adapters.adapter import create_adapter
                adapter = create_adapter(
                    backend=os.environ.get("PARTNER_BENCHMARK_BACKEND", "hermes"),
                    workspace_path=workspace,
                    model=os.environ.get("PARTNER_BENCHMARK_MODEL", ""),
                    provider=os.environ.get("PARTNER_BENCHMARK_PROVIDER", ""),
                )
            except Exception:
                pass
            
            if not adapter:
                # Fallback to simple execute
                return self.execute(task, work_dir)
            
            registry = default_registry()
            planner = BatchPlanner.from_workspace(workspace)
            
            # Build prompt and get plan (synchronous call)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                micro_plan, planner_calls = loop.run_until_complete(planner.plan(
                    adapter=adapter,
                    user_message=user_message,
                    task_instance=task_instance,
                    registry=registry,
                    event_type="data_analysis",
                ))
            finally:
                loop.close()
            
            if not micro_plan or not micro_plan.plan:
                task_instance.append_log("plan_empty", {})
                return BenchmarkResult(
                    task_id=task.task_id,
                    title=task.title,
                    status="failed",
                    content="",
                    error="Planner returned empty plan",
                    duration_ms=int((_time.time() - start_ts) * 1000),
                )
            
            planned_steps = len(micro_plan.plan)
            task_instance.append_log("plan_created", {
                "steps": planned_steps,
                "planner_calls": planner_calls,
            })
            
            # 3. Execute via Harness
            from ..mind.event_types import MindEvent, EventType
            dummy_event = MindEvent(
                type=EventType.BATCH_PLAN,
                priority=5,
                payload={"title": task.title, "user_request": user_message},
            )
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(run_harness_plan(
                    workspace=workspace,
                    event=dummy_event,
                    title=task.title,
                    project_dir=task_dir,
                    state_md="",
                    artifact_path=os.path.join(task_dir, "result.md"),
                    adapter=adapter,
                    build_action_prompt=lambda event, title, state_md, artifact_path: f"执行任务: {title}\n{event.payload.get('user_request', '')}",
                    parse_structured_response=lambda response: {"content": response, "action": "batch_plan", "findings": [], "next_action": ""},
                    micro_plan=micro_plan,
                    planner_llm_calls=planner_calls,
                    progress_callback=None,
                ))
            finally:
                loop.close()
            
            elapsed_ms = int((_time.time() - start_ts) * 1000)
            
            # 4. Collect output
            content = ""
            if result and hasattr(result, 'parsed') and result.parsed:
                parsed = result.parsed if isinstance(result.parsed, dict) else {}
                content = str(parsed.get("artifact_content") or parsed.get("content") or "")
            if not content and result and hasattr(result, 'step_results'):
                for _sid, _sr in (result.step_results or {}).items():
                    if isinstance(_sr, dict):
                        _c = str(_sr.get("content") or _sr.get("output") or "")
                        if _c and len(_c) > len(content):
                            content = _c
            
            if not content:
                # Write plan + result summary as content
                content_lines = [f"# {task.title}", "", "## Plan"]
                for step in micro_plan.plan:
                    content_lines.append(f"- {step.id}: {step.event_type}")
                content_lines.extend(["", "## Result", str(getattr(result, 'ok', False))])
                content = "\n".join(content_lines)
            
            # Write artifact
            result_path = os.path.join(task_dir, f"{task.task_id}_result.md")
            with open(result_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            status = "completed" if getattr(result, 'ok', False) else "partial"
            
            return BenchmarkResult(
                task_id=task.task_id,
                title=task.title,
                status=status,
                content=content,
                artifacts=[result_path],
                duration_ms=elapsed_ms,
                metadata={
                    "task_dir": task_dir,
                    "workspace": workspace,
                    "planned_steps": planned_steps,
                    "harness_ok": getattr(result, 'ok', False),
                },
            )
            
        except Exception as exc:
            elapsed_ms = int((_time.time() - start_ts) * 1000)
            logger.error("[BENCHMARK] Harness execution failed for %s: %s", task.task_id, exc)
            return BenchmarkResult(
                task_id=task.task_id,
                title=task.title,
                status="failed",
                content="",
                error=str(exc),
                duration_ms=elapsed_ms,
            )


# ── 便利执行器函数 ────────────────────────────────────────────────────

def sync_harness_executor(task: Any, work_dir: str) -> Any:
    """同步执行器（供 NatureBenchRunner 默认使用）。"""
    executor = HarnessBenchmarkExecutor()
    return executor.execute(task, work_dir)
