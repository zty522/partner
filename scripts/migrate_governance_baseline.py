#!/usr/bin/env python3
"""Idempotently seed governance receipts from the verified 2026-08-22 runs."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from partner.governance.project_loop import record_iteration
from partner.governance.storage import latest_receipt


def _latest_task_file(instance_dir: Path, filename: str) -> Path | None:
    candidates = list((instance_dir / "state" / "tasks").glob(f"*/{filename}"))
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _files_in_task(marker: Path | None, names: list[str]) -> list[str]:
    if marker is None:
        return []
    return [str(marker.parent / name) for name in names if (marker.parent / name).is_file()]


def _completed_action(title: str, event_type: str, task_id: str) -> dict:
    return {"title": title, "event_type": event_type, "status": "completed", "task_id": task_id}


def migrate_01(root: Path) -> list[dict]:
    workspace = root / "instances" / "01"
    if latest_receipt(str(workspace), "xiaohongshu_operations"):
        return [{"ok": True, "status": "already_migrated", "project_id": "xiaohongshu_operations"}]
    marker = _latest_task_file(workspace, "xhs_step_03_upload_requirements.png")
    if marker is None:
        return [{"ok": False, "status": "missing_verified_xhs_artifacts"}]
    first_artifacts = _files_in_task(marker, [
        "xhs_step_01_open_publish.png", "xhs_step_02_image_text_tab.png", "xiaohongshu_publish_editor.json",
    ])
    second_artifacts = _files_in_task(marker, [
        "xhs_step_03_upload_requirements.png", "xiaohongshu_upload_requirements.json",
        "xiaohongshu_upload_requirements.md",
    ])
    first = record_iteration(str(workspace), {
        "project_id": "xiaohongshu_operations", "owner_instance": "01",
        "project_goal": "可见、可验收的小红书账户操作", "iteration": 1,
        "goal": "打开并核验上传图文入口", "inputs": [],
        "actions_executed": ["xiaohongshu_open_publish_editor"],
        "artifacts": first_artifacts, "findings": ["已进入上传图文入口并核验图片文件控件"],
        "delivery_confirmed": True,
        "next_actions": [_completed_action("读取上传要求", "xiaohongshu_inspect_upload_requirements", "historical_xhs_round_2")],
    })
    second = record_iteration(str(workspace), {
        "project_id": "xiaohongshu_operations", "owner_instance": "01",
        "project_goal": "可见、可验收的小红书账户操作", "iteration": 2,
        "goal": "读取上传控件和页面要求", "inputs": first_artifacts,
        "actions_executed": ["xiaohongshu_inspect_upload_requirements"],
        "artifacts": second_artifacts, "findings": ["读取到文件控件以及格式、大小和分辨率要求"],
        "delivery_confirmed": True, "next_actions": [],
        "stop_reason": "当前协议阶段完成；最终发布需内容、安全检查和明确授权。",
        "project_status": "completed",
    })
    return [first, second]


def migrate_02(root: Path) -> list[dict]:
    workspace = root / "instances" / "02"
    if latest_receipt(str(workspace), "molecular_generation"):
        return [{"ok": True, "status": "already_migrated", "project_id": "molecular_generation"}]
    definitions = [
        ("molecular_generation_report.pdf", ["molecular_candidates.csv", "molecular_metrics.json", "molecular_qed_distribution.png", "molecular_generation_report.md", "molecular_generation_report.pdf"], "molecular_generation_benchmark", "完成85个有效候选的基础评估"),
        ("molecular_diversity_report.pdf", ["molecular_diversity_metrics.json", "molecular_diversity_report.md", "molecular_diversity_report.pdf"], "molecular_diversity_benchmark", "完成骨架和指纹多样性评估"),
        ("molecular_synth_baseline_report.pdf", ["molecular_synth_comparison.csv", "molecular_synth_comparison_metrics.json", "molecular_qed_sa_comparison.png", "molecular_synth_baseline_report.md", "molecular_synth_baseline_report.pdf"], "molecular_synth_baseline_benchmark", "完成SA与随机基线对照"),
        ("molecular_optimization_report.pdf", ["molecular_optimized_candidates.csv", "molecular_optimization_metrics.json", "molecular_optimization_top20.png", "molecular_optimization_report.md", "molecular_optimization_report.pdf"], "molecular_goal_optimization_benchmark", "Top-20仅9个唯一结构和4个骨架，暴露头部集中"),
    ]
    round_files = []
    for marker_name, names, _, _ in definitions:
        marker = _latest_task_file(workspace, marker_name)
        files = _files_in_task(marker, names)
        if not files:
            return [{"ok": False, "status": "missing_verified_molecular_artifacts", "marker": marker_name}]
        round_files.append(files)
    results = []
    for index, ((_, _, event_type, finding), artifacts) in enumerate(zip(definitions, round_files), 1):
        terminal = index == len(definitions)
        next_actions = [] if terminal else [_completed_action(
            f"分子项目第{index + 1}轮", definitions[index][2], f"historical_molecular_round_{index + 1}",
        )]
        results.append(record_iteration(str(workspace), {
            "project_id": "molecular_generation", "owner_instance": "02",
            "project_goal": "用可复现对照实验推进分子生成方法评估", "iteration": index,
            "goal": f"执行 {event_type}", "inputs": round_files[index - 2] if index > 1 else [],
            "actions_executed": [event_type], "artifacts": artifacts, "findings": [finding],
            "delivery_confirmed": True, "next_actions": next_actions,
            "stop_reason": "缺少目标活性、对接或实验数据；继续排序不会产生新证据。" if terminal else "",
            "project_status": "blocked" if terminal else "active",
            "resume_event": "molecular_target_data_available" if terminal else "",
        }))
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default="/mnt/e/work/partner_workspace")
    args = parser.parse_args()
    root = Path(args.workspace_root).expanduser().resolve()
    results = {"01": migrate_01(root), "02": migrate_02(root)}
    import json
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item.get("ok") for rows in results.values() for item in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
