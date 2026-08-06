"""Partner benchmark CLI 命令。

用法:
    partner benchmark list
    partner benchmark run --suite naturebench/literature_review_v1
    partner benchmark score --suite naturebench/literature_review_v1
    partner benchmark export --suite naturebench/literature_review_v1 --output ./results
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from ..benchmark import (
    BenchmarkRegistry,
    NatureBenchRunner,
    list_benchmark_suites,
    get_registry,
    score_benchmark_run,
    format_score_report,
    export_event_trace,
    export_benchmark_results,
)
from ..cli.common import (
    C_RESET, C_BOLD, C_DIM, C_CYAN, C_GREEN, C_YELLOW, C_RED,
    _print_commands,
)

logger = logging.getLogger(__name__)
JsonDict = dict[str, Any]


# ── 命令处理函数 ───────────────────────────────────────────────────────


def cmd_benchmark_list(args: argparse.Namespace) -> None:
    """列出所有已注册的 benchmark 套件。"""
    suites = list_benchmark_suites()

    if not suites:
        print(f"\n  {C_YELLOW}暂无已注册的 benchmark 套件。{C_RESET}\n")
        return

    print()
    print(f"  {C_BOLD}{C_CYAN}Available Benchmarks{C_RESET}")
    print(f"  {'=' * 60}")
    print()

    for i, suite in enumerate(suites, 1):
        tags = ", ".join(suite.get("tags", [])) if suite.get("tags") else "-"
        difficulty = ", ".join(suite.get("difficulty_range", [])) if suite.get("difficulty_range") else "-"

        print(f"  {C_BOLD}{i}. {suite['benchmark_id']}{C_RESET}")
        print(f"     名称: {suite['display_name']}")
        print(f"     版本: {suite['version']}")
        print(f"     任务数: {suite['task_count']}")
        print(f"     难度: {difficulty}")
        print(f"     标签: {tags}")
        print(f"     说明: {suite.get('description', '')}")
        print()

    total = get_registry().task_count_total()
    print(f"  {C_DIM}共 {len(suites)} 个套件，{total} 个任务{C_RESET}")
    print()
    _print_commands()


def cmd_benchmark_run(args: argparse.Namespace) -> None:
    """执行一个 benchmark 套件。"""
    benchmark_id = args.suite
    if not benchmark_id:
        print(f"\n  {C_RED}❌ 请指定 --suite 参数{C_RESET}")
        print(f"     可用: partner benchmark list")
        print()
        return

    # 显示模式标志
    dry_run = getattr(args, "dry_run", False)
    task_ids = getattr(args, "tasks", None)

    registry = get_registry()
    protocol = registry.get(benchmark_id)
    if not protocol:
        print(f"\n  {C_RED}❌ 未知 benchmark: {benchmark_id}{C_RESET}")
        print(f"     使用 {C_BOLD}partner benchmark list{C_RESET} 查看可用套件")
        print()
        return

    print()
    print(f"  {C_BOLD}{C_CYAN}🔬 Benchmark: {benchmark_id}{C_RESET}")
    print(f"  {'=' * 60}")
    print(f"  名称: {protocol.display_name}")
    print(f"  版本: {protocol.version}")
    print(f"  任务数: {protocol.task_count()}")
    print()

    if dry_run:
        print(f"  {C_GREEN}✅ Dry-run 模式 — 仅显示任务列表{C_RESET}")
        print()
        for i, task in enumerate(protocol.tasks, 1):
            selected = task_ids is None or task.task_id in task_ids
            prefix = f"  {C_GREEN}▶{C_RESET}" if selected else f"  {C_DIM}⏭️{C_RESET}"
            print(f"  {prefix} {i:2d}. {C_BOLD}{task.title}{C_RESET}")
            print(f"      ID: {task.task_id}")
            print(f"      难度: {task.metadata.get('difficulty', 'N/A')}")
            print(f"      预估: {task.metadata.get('estimated_time_min', '?')} min")
            print()
        print(f"  {C_DIM}共 {len(protocol.tasks)} 个任务{C_RESET}")
        print()
        _print_commands()
        return

    # 实际执行
    print(f"  {C_YELLOW}⏳ 正在执行...{C_RESET}")
    print()

    work_dir = args.work_dir or os.path.join(os.getcwd(), ".benchmark_work")
    output_dir = args.output or os.path.join(os.getcwd(), "benchmark_results")

    runner = NatureBenchRunner(
        work_dir=work_dir,
        output_dir=output_dir,
        model_name=args.model or "",
        provider_name=args.provider or "",
        agent_id=args.agent or "partner",
    )

    try:
        run = runner.run(
            benchmark_id,
            task_ids=task_ids.split(",") if task_ids else None,
            export=True,
        )
    except Exception as exc:
        print(f"  {C_RED}❌ 执行失败: {exc}{C_RESET}")
        print()
        return

    # 输出结果
    print(f"  {C_GREEN}✅ 执行完成{C_RESET}")
    print(f"  {'=' * 60}")
    print(f"  耗时: {run.duration_ms}ms")
    print(f"  完成: {sum(1 for r in run.results.values() if r.status == 'completed')}")
    print(f"  失败: {sum(1 for r in run.results.values() if r.status == 'failed')}")
    print()

    if run.score_set:
        print(format_score_report(run.score_set))
    else:
        print(f"  {C_YELLOW}⚠ 未评分（缺少 scoring_rules）{C_RESET}")
        print()

    if run.export_path:
        print(f"  {C_DIM}导出文件: {run.export_path}{C_RESET}")

    print()
    _print_commands()


def cmd_benchmark_score(args: argparse.Namespace) -> None:
    """对已执行的结果进行评分（重新评分）。"""
    benchmark_id = args.suite
    result_path = args.result

    if not benchmark_id and not result_path:
        print(f"\n  {C_RED}❌ 请指定 --suite 或 --result{C_RESET}")
        print(f"     用法: partner benchmark score --suite <id> [--result <path>]")
        print()
        return

    registry = get_registry()
    protocol = registry.get(benchmark_id) if benchmark_id else None

    if not protocol:
        print(f"\n  {C_RED}❌ 未知 benchmark: {benchmark_id}{C_RESET}")
        print(f"     使用 {C_BOLD}partner benchmark list{C_RESET} 查看可用套件")
        print()
        return

    # 加载外部结果文件（如果提供）
    task_outputs: dict[str, JsonDict] = {}
    if result_path and os.path.exists(result_path):
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    tid = item.get("task_id", "")
                    if tid:
                        task_outputs[tid] = item
            elif isinstance(data, dict) and "tasks" in data:
                for task in data["tasks"]:
                    tid = task.get("task_id", "")
                    if tid:
                        task_outputs[tid] = task
        except Exception as exc:
            print(f"\n  {C_RED}❌ 加载结果文件失败: {exc}{C_RESET}")
            print()
            return

    print()
    score_set = score_benchmark_run(protocol, task_outputs)
    print(format_score_report(score_set))
    print()
    _print_commands()


def cmd_benchmark_export(args: argparse.Namespace) -> None:
    """导出 benchmark 结果到 NatureBench 格式。"""
    benchmark_id = args.suite
    result_path = args.result
    output_path = args.output or os.path.join(os.getcwd(), "naturebench_export.json")

    if not result_path or not os.path.exists(result_path):
        print(f"\n  {C_RED}❌ 请指定 --result <path>{C_RESET}")
        print()
        return

    try:
        with open(result_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"\n  {C_RED}❌ 读取结果文件失败: {exc}{C_RESET}")
        print()
        return

    registry = get_registry()
    protocol = registry.get(benchmark_id) if benchmark_id else None
    if not protocol:
        print(f"\n  {C_RED}❌ 未知 benchmark: {benchmark_id}{C_RESET}")
        print()
        return

    # 从结果文件构建 task_outputs
    task_outputs: dict[str, JsonDict] = {}
    task_dirs: dict[str, str] = {}
    if isinstance(data, dict) and "tasks" in data:
        for task in data.get("tasks", []):
            tid = task.get("task_id", "")
            task_outputs[tid] = task
            if task.get("task_dir"):
                task_dirs[tid] = task["task_dir"]

    json_str = export_benchmark_results(
        task_results=task_outputs,
        task_dirs=task_dirs,
        benchmark_id=benchmark_id,
        suite_version=protocol.version,
        output_path=output_path,
    )

    print(f"\n  {C_GREEN}✅ 已导出 NatureBench 格式: {output_path}{C_RESET}")
    print(f"     文件大小: {os.path.getsize(output_path)} bytes")
    print()
    _print_commands()


# ── 注册 CLI ──────────────────────────────────────────────────────────


def register_subparser(sub: argparse._SubParsersAction) -> None:
    """注册 benchmark 子命令到主 CLI parser。"""
    p_benchmark = sub.add_parser("benchmark", help="科研评估基准（NatureBench 兼容）")

    b_sub = p_benchmark.add_subparsers(dest="benchmark_action")
    b_sub.required = True

    # list
    p_list = b_sub.add_parser("list", help="列出所有可用的 benchmark 套件")
    p_list.set_defaults(func=cmd_benchmark_list)

    # run
    p_run = b_sub.add_parser("run", help="执行 benchmark 套件")
    p_run.add_argument("--suite", "-s", required=True, help="benchmark 标识（如 naturebench/literature_review_v1）")
    p_run.add_argument("--output", "-o", default=None, help="结果输出目录")
    p_run.add_argument("--work-dir", default=None, help="临时工作目录")
    p_run.add_argument("--tasks", default=None, help="仅执行指定任务 ID（逗号分隔）")
    p_run.add_argument("--model", default="", help="使用的模型名称（记录用）")
    p_run.add_argument("--provider", default="", help="使用的提供商（记录用）")
    p_run.add_argument("--agent", default="partner", help="Agent 标识")
    p_run.add_argument("--dry-run", action="store_true", help="仅显示任务列表，不执行")
    p_run.set_defaults(func=cmd_benchmark_run)

    # score
    p_score = b_sub.add_parser("score", help="对 benchmark 执行结果评分")
    p_score.add_argument("--suite", "-s", default=None, help="benchmark 标识")
    p_score.add_argument("--result", "-r", default=None, help="结果 JSON 文件路径（可选）")
    p_score.set_defaults(func=cmd_benchmark_score)

    # export
    p_export = b_sub.add_parser("export", help="导出结果为 NatureBench JSON 格式")
    p_export.add_argument("--suite", "-s", required=True, help="benchmark 标识")
    p_export.add_argument("--result", "-r", required=True, help="结果文件路径")
    p_export.add_argument("--output", "-o", default=None, help="导出路径（默认 naturebench_export.json）")
    p_export.set_defaults(func=cmd_benchmark_export)
