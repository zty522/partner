#!/usr/bin/env python3
"""Sprint18 §5 follow-up: write 03/05 new-project seed tasks.

After governance + active_learning patches are installed and 03/05 are
pointed at new projects (03 -> molecular_dynamics_study, 05 ->
hermes_partner_explore), this script writes one inbox row each so they
have an immediate task on startup. Idempotent on message_id.

The other 3 instances (01/02/04) get *no* new task — they already had
their one-shot run earlier and are now driven by their project_state
status + active_learning chain.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s seed_project %(levelname)s %(message)s")
logger = logging.getLogger("seed_project")

sys.path.insert(0, "/mnt/e/work/partner")
WORKSPACE = Path(os.environ.get("PARTNER_WORKSPACE", "/mnt/e/work/partner_workspace"))


PROJECTS = {
    "03": {
        "project_id": "molecular_dynamics_study",
        "instructions": (
            "学 + 试分子动力学模拟：\n"
            "1. 读 share/projects/molecular_dynamics_study/project_brief.md\n"
            "2. web.fetch:URL 或 github_search:<query> 至少 1 个真实 MD 数据集\n"
            "   (推荐: RCSB PDB 4HHB / OpenMM tutorials / MDAnalysis 范例)。\n"
            "3. atomic_write_file:share/projects/molecular_dynamics_study/external_sources/<paper_or_dataset>.md\n"
            "4. exec:python3 ... 跑一个最小 MD 工作流（OpenMM alanine dipeptide minimization "
            "或 MDAnalysis 读 PDB + 计算 RMSD 均可）。\n"
            "5. 把结果写到 share/projects/molecular_dynamics_study/external_artifacts/"
            "<UTC日期>_md/ 下（含 .log 或 .csv 或 .png，至少 1 个产物）。\n"
            "6. governance/receipts/<UTC日期>_md_receipt.md ≥ 800 字中文，含真实数字。\n"
            "actions_executed 显式记录以上所有动作。findings 含三要素。"
        ),
    },
    "05": {
        "project_id": "hermes_partner_explore",
        "instructions": (
            "读 hermes-agent 与 partner 代码 + 写新 skill 与 event：\n"
            "1. atomic_inspect_file 读 /home/os/.hermes/hermes-agent/skills/ 至少 1 个 SKILL.md, "
            "读 /mnt/e/work/partner/partner/evolution/decision_loop.py 至少 200 行, "
            "读 /mnt/e/work/partner/partner/mind/executor.py 至少 200 行。\n"
            "2. atomic_write_file:share/projects/hermes_partner_explore/project_brief.md ≥ 500 字 "
            "总结 hermes-agent 当前能力 + partner 当前 event 集合。\n"
            "3. atomic_write_file:share/skills/<new_skill_name>/SKILL.md (新建 SKILL.md, "
            "至少包含 name + description + when_to_use 三段)。\n"
            "4. atomic_write_file:share/projects/hermes_partner_explore/events/<new_event>.py "
            "(新 event_type 实现, 至少 30 行真代码)。\n"
            "5. governance/receipts/<UTC日期>_explore_receipt.md ≥ 800 字中文, "
            "含真实发现与新增 skill/event 摘要。\n"
            "actions_executed 显式记录所有动作与文件路径。"
        ),
    },
}


def _bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _build_request(role: str, info: dict) -> str:
    return (
        f"【{role}实例新方向】项目：{info['project_id']}\n\n"
        f"目标：{info['instructions']}\n\n"
        f"actions_executed 显式列出动作；findings 三要素（动作+路径+数字）。\n"
        f"[instance_native=true] [native_kind=project] "
        f"[project_id={info['project_id']}]\n"
        f"[self_drive=true] [seed_project=true] "
        f"[emitted_at={datetime.now(timezone.utc).astimezone().isoformat()}]"
    )


def _write(role: str, mid: str, text: str, project_id: str) -> bool:
    inbox = WORKSPACE / "instances" / role / "state" / "desktop_inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": mid, "message_id": mid, "role": "user",
        "text": text, "content": text,
        "source": "instance_native", "channel": "local",
        "kind": "project", "project_id": project_id,
        "sender_id": f"partner_{role}_seed",
        "sender_name": f"Partner{role}项目种子",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "self_drive": True, "seed_project": True,
    }
    with inbox.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return True


def main() -> int:
    bucket = _bucket()
    for role, info in PROJECTS.items():
        mid = f"seed_{role}_{bucket}_" + hashlib.sha1((role+bucket).encode()).hexdigest()[:10]
        text = _build_request(role, info)
        if _write(role, mid, text, info["project_id"]):
            logger.info("seeded task for %s -> %s (mid=%s)",
                        role, info["project_id"], mid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
